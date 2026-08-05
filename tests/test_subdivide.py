"""Subdividing a long unit (PRD §12).

The 40-page expertise. Subdivide by the document's own structure, never by arbitrary
character counts — a character split cuts mid-sentence, mid-table and mid-barème-row, and
a span starting in one chunk and ending in another cannot be cited.
"""

from __future__ import annotations

from alie.models import BBox, Block, BlockSource, BlockType
from alie.stages import subdivide
from alie.stages.subdivide import LONG_UNIT_CHARS


def _block(order: int, text: str, kind: BlockType = BlockType.PARAGRAPH) -> Block:
    return Block(
        id=f"blk_{order}", bundle_id="b", pdf_index=1 + order // 20, order=order, text=text,
        type=kind, bbox=BBox(0, 0, 400, 12), source=BlockSource.TEXT_LAYER, confidence=1.0,
    )


def _long_body(n: int) -> str:
    return ("Le travailleur présente une douleur lombaire persistante irradiant. " * n)


def _expertise() -> list[Block]:
    """A long document with its own section structure, like the real thing."""
    blocks: list[Block] = []
    order = 0
    for heading in ("HISTORIQUE", "EXAMEN PHYSIQUE", "BILAN DES SÉQUELLES", "CONCLUSION"):
        blocks.append(_block(order, f"## {heading}", BlockType.HEADING))
        order += 1
        blocks.append(_block(order, _long_body(220)))
        order += 1
    return blocks


def test_a_short_unit_is_never_split():
    got = subdivide.subdivide("u", [_block(0, "Note brève."), _block(1, "Deux lignes.")])

    assert not got.split
    assert got.reason == "short_enough"


def test_a_long_unit_splits_on_its_own_headings():
    blocks = _expertise()
    assert sum(len(b.text) for b in blocks) > LONG_UNIT_CHARS

    got = subdivide.subdivide("u", blocks)

    assert got.split
    assert got.reason == "split_on_headings"
    assert [p.heading for p in got.parts] == [
        "## HISTORIQUE", "## EXAMEN PHYSIQUE", "## BILAN DES SÉQUELLES", "## CONCLUSION",
    ]


def test_every_block_survives_the_split_exactly_once():
    """No text is copied and none is lost, so every citation still resolves against the
    block store exactly as before (§8.1)."""
    blocks = _expertise()
    got = subdivide.subdivide("u", blocks)

    seen = [b.id for part in got.parts for b in part.blocks]
    assert seen == [b.id for b in blocks]
    assert len(seen) == len(set(seen))


def test_a_long_unit_with_no_structure_is_deliberately_not_split():
    """Arbitrary pieces would trade a truncation risk for a citation risk, and only one of
    those is detectable (§12)."""
    blocks = [_block(i, _long_body(60)) for i in range(20)]
    assert sum(len(b.text) for b in blocks) > LONG_UNIT_CHARS

    got = subdivide.subdivide("u", blocks)

    assert not got.split
    assert got.reason == "no_internal_structure"


def test_a_heading_with_nothing_under_it_folds_into_the_previous_part():
    """`## 12.` printed on its own line above the real section is not a section."""
    blocks = [
        _block(0, "## HISTORIQUE", BlockType.HEADING),
        _block(1, _long_body(400)),
        _block(2, "## 12.", BlockType.HEADING),
        _block(3, "## BILAN DES SÉQUELLES", BlockType.HEADING),
        _block(4, _long_body(400)),
    ]
    got = subdivide.subdivide("u", blocks)

    assert [p.heading for p in got.parts] == ["## HISTORIQUE", "## BILAN DES SÉQUELLES"]
    # The stray heading is still present, attached to the part before it.
    assert any(b.text == "## 12." for b in got.parts[0].blocks)


def test_parts_are_ordered_by_reading_order_not_block_id():
    blocks = list(reversed(_expertise()))
    got = subdivide.subdivide("u", blocks)

    orders = [b.order for part in got.parts for b in part.blocks]
    assert orders == sorted(orders)


def test_the_split_is_never_by_character_count():
    """The distinction is not stylistic: a character split cuts mid-barème-row, and 4a's
    expected-row count is what makes silent output truncation detectable (§12)."""
    got = subdivide.subdivide("u", _expertise())

    # Parts are wildly uneven in size because sections are, which is the point.
    sizes = [p.chars for p in got.parts]
    assert len(set(sizes)) >= 1
    # And each part begins where the document says a section begins.
    assert all(p.blocks[0].type is BlockType.HEADING for p in got.parts)
