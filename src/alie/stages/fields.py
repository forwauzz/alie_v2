"""Required fields read by cue, for documents no template covers (PRD §8.6).

The template registry reads known coordinates on known forms. These fields live in free
text — an expertise discusses a confounder and carries no form serial — so they are read
from any document, whatever its class.

Everything here is deterministic and cited. A field the rules cannot find is *absent*, and
absent is a value: §8.6's whole point is that `aucune` ≠ `trop tôt` ≠ absent, and that
"the engine never looked" is different again. 4b fills only what these leave open (§4.2).

The trajectory field is the one to read carefully. It stores the paralegal's source
sentence verbatim **and** a derived enum, as two records. Storing only the enum throws
away the sentence a tribunal would want to read; storing only the text makes the file
unqueryable. Never overwrite her wording.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..models import Block, BlockType, Legibility, ReportUnit
from ..packs import Pack
from ..packs import load as load_pack
from ..provenance import Producer
from ..stores import audit, cases, manifest
from ..stores import blocks as blocks_store
from ..stores.records import Record, for_unit, replace_for_unit

STAGE = "4c"

#: A rated barème row as it appears in text. Used to tell "no rating in this document"
#: from "a rating that omits what the text argues" without needing a template match.
RATED_CODE = re.compile(r"\bCode\s+\d[\d\s]*\s+\d+(?:[.,°]\d+)?\s*%", re.IGNORECASE)


@dataclass(frozen=True)
class FieldsResult:
    unit_id: str
    read: int
    absent: int
    skipped: str | None = None


def run_unit(
    conn: sqlite3.Connection, unit_id: str, *, run_id: str | None = None
) -> FieldsResult:
    unit = manifest.get_unit(conn, unit_id)
    if unit is None:
        raise KeyError(f"unknown unit: {unit_id}")

    # An illegible unit has no text worth cueing against, and a cue that fires on OCR
    # noise produces a cited-looking record pointing at gibberish.
    if unit.legibility is Legibility.ILLEGIBLE:
        replace_for_unit(conn, unit_id, STAGE, [], Producer())
        return FieldsResult(unit_id, 0, 0, skipped="illegible")

    case = cases.get_case(conn, unit.case_id)
    pack = load_pack(unit.regime or case["primary_pack"])
    blocks = [b for b in blocks_store.for_pages(conn, unit.bundle_id, unit.pages) if b.is_body_text]

    prior = {r.field: r for r in for_unit(conn, unit_id) if r.stage == "4a"}
    records = read_fields(unit, blocks, pack, prior)
    replace_for_unit(conn, unit_id, STAGE, records, Producer())

    read = sum(1 for r in records if not r.derived or r.field.endswith(".enum"))
    absent = sum(1 for r in records if r.value == "absent")
    audit.record(
        conn, subject_type="unit", subject_id=unit_id, action="fields_read",
        run_id=run_id, rule="stage.fields",
        detail={"fields": [r.field for r in records], "read": read, "absent": absent},
    )
    return FieldsResult(unit_id, read, absent)


def read_fields(
    unit: ReportUnit, blocks: list[Block], pack: Pack, prior: dict[str, Record]
) -> list[Record]:
    out: list[Record] = []
    furniture = _furniture(blocks)
    for spec in pack.cue_fields:
        kind = spec.get("kind")
        if kind == "free_text_with_enum":
            out.extend(_read_trajectory(unit, blocks, spec, furniture))
        elif kind == "cue_span":
            out.extend(_read_cue_span(unit, blocks, spec, furniture))
        elif kind == "classify":
            out.extend(_read_classify(unit, blocks, spec, pack, furniture))
        elif kind == "derived_claimed_sequelae":
            out.extend(_read_claimed_but_unrated(unit, blocks, spec, prior, furniture))
    return out


def _record(
    unit: ReportUnit, spec: dict, value: str | None, block: Block | None,
    span: tuple[int, int] | None, *, field: str | None = None, derived: bool = False,
    confidence: float = 1.0,
) -> Record:
    return Record(
        unit_id=unit.id,
        field=field or spec["id"],
        value=value,
        stage=STAGE,
        confidence=confidence,
        block_id=block.id if block else None,
        span_start=span[0] if span else None,
        span_end=span[1] if span else None,
        derived=derived,
        rule=f"field.{spec['id']}",
        epistemic_tag=spec.get("tag", "INF-H"),
    )


def _absent(unit: ReportUnit, spec: dict) -> Record:
    """The field was looked for and is not in the document. There is no text to cite —
    that *is* the finding — so it is derived, not an uncited transcription (§8.6)."""
    return _record(unit, spec, "absent", None, None, derived=True)


#: A statement needs this many words. Measured on case 1: the cues matched CNESST form
#: *field labels* far more often than clinical statements — `Sommaire de prise en charge et
#: d'évolution` is a form title, `Code de séquelle` is a field caption. A label names a
#: topic; a finding asserts something about it, and asserting takes words.
MIN_STATEMENT_WORDS = 7


#: Text that tells the reader what to do rather than saying what happened. A form caption
#: is grammatical and long enough to pass a word count — `Atteinte permanente à l'intégrité
#: physique ou psychique CONSOLIDATION (Inscrire la date)` did — so it has to be excluded
#: on what it is, not on its shape.
FORM_INSTRUCTION = re.compile(
    r"\(?\b(inscrire|cocher|indiquer|remplir|veuillez|joindre)\b"
    r"|\bsi\s+(?:la\s+r[ée]ponse|oui|non)\b"
    # A form asks; a finding states. `Si oui, ces limitations ont-elles aggravé…` is the
    # question printed above the blank, not the clinician's answer.
    r"|\b\w+-(?:elles?|ils?|vous|on)\b"
    r"|\b[àa]\s+l.usage\s+d[eu]\b"
    r"|\bformulaire\s+transmis\b"
    r"|\bn[°o]\s*de\s*r[ée]f[ée]rence\b",
    re.IGNORECASE,
)


def _looks_like_a_statement(sentence: str) -> bool:
    words = re.findall(r"\w+", sentence)
    if len(words) < MIN_STATEMENT_WORDS:
        return False
    if FORM_INSTRUCTION.search(sentence):
        return False
    # Blank form captions arrive as ALL CAPS on scanned CNESST sheets.
    letters = [c for c in sentence if c.isalpha()]
    return not (letters and sum(c.isupper() for c in letters) / len(letters) > 0.8)


def _first_hit(
    blocks: list[Block], patterns: list[str], *, furniture: frozenset[str] = frozenset(),
    require_statement: bool = False,
) -> tuple[Block, re.Match] | None:
    """First cue hit in prose.

    Headings are skipped. `## ÉVOLUTION` is a section title, not a trajectory statement,
    and storing it as the paralegal's wording would cite a word she never wrote as the
    finding. The sentence under the heading is the finding.

    Furniture is skipped too. A line printed on every sheet of a form is the form talking,
    not the clinician.
    """
    for block in blocks:
        if block.type is BlockType.HEADING or normalise(block.text) in furniture:
            continue
        for raw in patterns:
            m = re.search(raw, block.text, re.IGNORECASE)
            if not m:
                continue
            if require_statement:
                start, end = _sentence_around(block.text, m.start())
                if not _looks_like_a_statement(block.text[start:end]):
                    continue
            return block, m
    return None


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _furniture(blocks: list[Block]) -> frozenset[str]:
    """Lines that repeat within the unit. Chronology content does not appear identically
    on every sheet (§6, output.repeat_is_furniture)."""
    seen: dict[str, int] = {}
    for block in blocks:
        key = normalise(block.text)
        if key:
            seen[key] = seen.get(key, 0) + 1
    return frozenset(k for k, n in seen.items() if n > 1)


def _sentence_around(text: str, at: int) -> tuple[int, int]:
    """The sentence containing a cue. Her wording, not a window of characters."""
    start = max(text.rfind(".", 0, at), text.rfind("\n", 0, at)) + 1
    end = min(
        (i for i in (text.find(".", at), text.find("\n", at)) if i != -1),
        default=len(text),
    )
    return start, (end + 1 if end < len(text) else len(text))


def _continuation(blocks: list[Block], block: Block, text: str) -> list[Block]:
    """Blocks that finish a sentence running past the end of its own block.

    "confondue par une condition personnelle" and "préexistante de spondylarthrose étagée"
    can arrive as two blocks. Storing only the first says the finding is confounded
    without saying by what, which is a different claim from the one the document makes.
    """
    if text.rstrip().endswith((".", "!", "?", ":")):
        return []

    out: list[Block] = []
    following = [b for b in blocks if b.pdf_index == block.pdf_index and b.order > block.order]
    for nxt in following[:2]:
        if nxt.type is BlockType.HEADING or not nxt.text.strip():
            break
        out.append(nxt)
        if nxt.text.rstrip().endswith((".", "!", "?")):
            break
    return out


def _read_trajectory(
    unit: ReportUnit, blocks: list[Block], spec: dict, furniture: frozenset[str]
) -> list[Record]:
    """Free text **plus** a derived enum, as two records (§8.6)."""
    # The enum patterns are the cues when the pack says so. A trajectory the engine cannot
    # classify is not a trajectory statement — it is a form title or an instruction that
    # happens to contain the word (§4.2).
    cues = list(spec.get("cues", []))
    if spec.get("cues_from_enum"):
        cues = [p for patterns in (spec.get("enum") or {}).values() for p in patterns]

    hit = _first_hit(blocks, cues, furniture=furniture, require_statement=True)
    if hit is None:
        return [_absent(unit, spec)]

    block, match = hit
    start, end = _sentence_around(block.text, match.start())
    sentence = block.text[start:end]

    # An unfilled option row lists every answer at once: `Progrès du patient : o Aucun
    # O Régression $ Amélioration minimale o Amélioration importante O Plateau`. Matching
    # two mutually exclusive categories means this is the menu, not the choice.
    hit_categories = [
        value
        for value, patterns in (spec.get("enum") or {}).items()
        if any(re.search(p, sentence, re.IGNORECASE) for p in patterns)
    ]
    if len(hit_categories) > 1:
        return [_absent(unit, spec)]

    records = [
        _record(unit, spec, sentence.strip(), block, (start, end), confidence=block.confidence)
    ]

    # The enum is derived *from the stored sentence*, so the two can never disagree.
    for value, patterns in (spec.get("enum") or {}).items():
        if any(re.search(p, sentence, re.IGNORECASE) for p in patterns):
            records.append(
                _record(unit, spec, value, block, (start, end),
                        field=f"{spec['id']}.enum", confidence=block.confidence)
            )
            break
    else:
        # A trajectory sentence the enum does not recognise. Kept as text with the enum
        # marked unclassified — dropping to `absent` would claim nothing was said.
        records.append(
            _record(unit, spec, "unclassified", block, (start, end),
                    field=f"{spec['id']}.enum", derived=True, confidence=block.confidence)
        )
    return records


def _read_cue_span(
    unit: ReportUnit, blocks: list[Block], spec: dict, furniture: frozenset[str]
) -> list[Record]:
    hit = _first_hit(blocks, spec.get("cues", []), furniture=furniture, require_statement=True)
    if hit is None:
        return [_absent(unit, spec)]
    block, match = hit
    start, end = _sentence_around(block.text, match.start())
    sentence = block.text[start:end].strip()

    records = [_record(unit, spec, sentence, block, (start, end), confidence=block.confidence)]
    # The rest of a sentence that runs past its block, each part carrying its own span so
    # the citation invariant still holds for every character (§8.1).
    for index, tail in enumerate(_continuation(blocks, block, sentence), start=1):
        records.append(
            _record(unit, spec, tail.text.strip(), tail, (0, len(tail.text)),
                    field=f"{spec['id']}.cont{index}", confidence=tail.confidence)
        )
    return records


def _read_classify(
    unit: ReportUnit, blocks: list[Block], spec: dict, pack: Pack, furniture: frozenset[str]
) -> list[Record]:
    """Who obtained the report, and what that is worth under this regime."""
    for value, patterns in (spec.get("values") or {}).items():
        hit = _first_hit(blocks, patterns, furniture=furniture)
        if hit is None:
            continue
        block, match = hit
        records = [
            _record(unit, spec, value, block, (match.start(), match.end()),
                    confidence=block.confidence)
        ]
        weight = pack.evidence_weight(value)
        if weight:
            # Derived: the weight is the regime's rule, not a string in the document.
            records.append(
                _record(unit, spec, weight, None, None, field="evidence_weight", derived=True)
            )
        return records

    return [
        _absent(unit, spec),
        _record(unit, spec, pack.evidence_weight("unknown"), None, None,
                field="evidence_weight", derived=True),
    ]


def _read_claimed_but_unrated(
    unit: ReportUnit, blocks: list[Block], spec: dict, prior: dict[str, Record],
    furniture: frozenset[str],
) -> list[Record]:
    """Sequelae argued in the text but absent from the official rating (§8.6).

    Only meaningful where a rating exists to be absent from. On a document carrying no
    barème table the answer is `absent` — not `yes`, which would read as a finding.

    The rating is read from the unit's own text, not from 4a. An expertise carries a
    barème table and matches no template, and that is exactly the document where a
    sequela argued but unrated matters most.
    """
    rated_text = " ".join((prior[f].value or "") for f in prior if f.split(".")[0] == "bareme")
    rated_text += " ".join(
        m.group(0) for b in blocks for m in re.finditer(RATED_CODE, b.text)
    )
    if not rated_text.strip():
        return [_absent(unit, spec)]

    hit = _first_hit(blocks, spec.get("cues", []), furniture=furniture, require_statement=True)
    if hit is None:
        return [_absent(unit, spec)]

    block, match = hit
    start, end = _sentence_around(block.text, match.start())
    claim = block.text[start:end].strip()

    # A claimed sequela whose wording appears in no rated row. Deliberately weak: it flags
    # for a human rather than asserting the rating is incomplete (tag INF-L).
    rated_text = rated_text.lower()
    tokens = [w for w in re.findall(r"\w{5,}", claim.lower()) if w not in rated_text]
    if not tokens:
        return [_absent(unit, spec)]

    return [_record(unit, spec, claim, block, (start, end), confidence=block.confidence)]


def pack_fields(pack: Pack) -> list[dict[str, Any]]:
    return pack.cue_fields
