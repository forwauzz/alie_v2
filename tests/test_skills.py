"""Regime skills (PRD §5.1).

A pack's rules are data. A skill is the procedural knowledge that cannot be a lookup —
which instrument governs, what pulls a document into scope, where the file is shaped unlike
the others.

Three constraints, and all three are the kind that erode quietly, so all three are tests.
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
from pathlib import Path

import pytest

from alie.packs import available, load, skills


def _pack_with_skill(text: str, pack_id: str = "cnesst"):
    """A real pack with a substituted skill file, so the checks run against real data."""
    root = Path(tempfile.mkdtemp()) / pack_id
    shutil.copytree(f"packs/{pack_id}", root)
    (root / "skill.md").write_text(text, encoding="utf-8")
    return dataclasses.replace(load(pack_id), root=root)


def test_every_regime_has_a_skill():
    for pack_id in available():
        skill = skills.load(load(pack_id))
        assert skill.text
        assert skill.regime == pack_id


def test_a_skill_fits_on_one_page():
    """A long skill dilutes into noise, and the parts that matter stop being read."""
    for pack_id in available():
        assert skills.load(load(pack_id)).chars <= skills.MAX_CHARS


def test_an_oversized_skill_is_refused_rather_than_truncated():
    """Truncating would silently drop whichever paragraph happened to be last."""
    with pytest.raises(skills.SkillTooLong):
        skills.load(_pack_with_skill("x" * (skills.MAX_CHARS + 1)))


def test_a_skill_never_reaches_an_extractor():
    """An extractor sees one report unit and no context on purpose (§5). Regime strategy
    invites it to reason about the case instead of reading the page in front of it."""
    for audience in ("extractor", "composer", "classifier", "anything-else"):
        with pytest.raises(PermissionError):
            skills.load(load("cnesst"), audience=audience)

    for audience in (skills.Audience.ORCHESTRATOR, skills.Audience.ADJUDICATOR):
        assert skills.load(load("cnesst"), audience=audience).text


def test_no_shipped_skill_restates_a_deterministic_rule():
    """The moment a skill says "REM uses the exam date", it can drift from the date table
    and you have two sources of truth disagreeing silently."""
    for pack_id in available():
        offences = skills.restated_rules(load(pack_id))
        assert not offences, "\n".join(offences)


def test_the_detector_catches_a_restatement_in_either_language():
    """The packs are French; the role ids are English. A checker that only catches one
    spelling catches nothing."""
    english = _pack_with_skill("# t\n\nA rapport medical uses the exam date, always.\n")
    french = _pack_with_skill(
        "# t\n\nLe rapport d'évaluation médicale est daté par la date de l'examen.\n"
    )

    assert skills.restated_rules(english)
    assert skills.restated_rules(french)


def test_the_detector_does_not_flag_the_prose_a_skill_is_for():
    """A checker that cries wolf gets ignored, and an ignored checker is no checker. The
    first version fired five times on CNESST because two-letter display codes match inside
    words — `TS` in "asserts" — and the roles are named `report`, `event`, `visit`."""
    innocent = _pack_with_skill(
        "# t\n\n"
        "Surface both with their dates and who procured each.\n"
        "An expertise is the instrument that tries to displace it.\n"
        "The file asserts an RRA relation, so old reports are in scope.\n"
    )

    assert skills.restated_rules(innocent) == []


def test_a_pack_value_is_referenced_never_copied():
    """A skill that needs a pack value writes `{vocabulary.milestone}`, substituted at
    load. There is then one source of truth, and the skill *cannot* drift."""
    raw = (Path("packs/cnesst") / "skill.md").read_text(encoding="utf-8")
    resolved = skills.load(load("cnesst")).text

    assert "{vocabulary.milestone}" in raw
    assert "{vocabulary.milestone}" not in resolved
    assert "consolidation" in resolved

    # And each regime resolves to its own word rather than the neighbour's.
    assert "stabilisation" in skills.load(load("saaq")).text


def test_an_unresolvable_placeholder_is_an_error_not_a_blank():
    """A skill that renders "the milestone is " teaches the orchestrator that the regime
    has no milestone."""
    with pytest.raises(skills.SkillNotFound):
        skills.load(_pack_with_skill("# t\n\nThe milestone is {vocabulary.no_such_key}.\n"))


def test_a_missing_skill_is_never_invented():
    root = Path(tempfile.mkdtemp()) / "cnesst"
    shutil.copytree("packs/cnesst", root)
    (root / "skill.md").unlink()

    with pytest.raises(skills.SkillNotFound):
        skills.load(dataclasses.replace(load("cnesst"), root=root))


def test_each_skill_carries_what_the_prd_says_it_must():
    """§5.1 names the procedural knowledge each regime needs. Absence here is not a style
    question — it is the orchestrator not knowing the thing that decides the file."""
    cnesst = skills.load(load("cnesst")).text.lower()
    saaq = skills.load(load("saaq")).text.lower()
    ivac = skills.load(load("ivac")).text.lower()

    # CNESST: multiple claim events coexist; an RRA pulls older reports into scope.
    assert "rra" in cnesst and "1990" in cnesst

    # SAAQ: classification must be content-first — art. 83.15 fills the file with
    # format-free letters.
    assert "83.15" in saaq and "content-first" in saaq

    # IVAC: the offence date selects the statute, and LATMP precedence routes units out.
    assert "livac" in ivac and "lapvic" in ivac
    assert "precedence" in ivac or "précédence" in ivac
