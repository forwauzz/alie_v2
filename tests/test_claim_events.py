"""The claim-event dimension (PRD §8.6).

1990 / 2011 / 2022 coexist in one file. A chronology that flattens them loses the question
the file is about: which claim does this document belong to.
"""

from __future__ import annotations

from datetime import date

from alie.manifest import claimevents
from alie.models import DateRole, Legibility, RowDate, RowStatus, UnitKind
from alie.models.unit import ReportUnit
from alie.stores import db
from helpers import build_case


def _unit(unit_id: str, row_date: date | None) -> ReportUnit:
    u = ReportUnit(
        id=unit_id, bundle_id="b", case_id="c", pages=(1,), doc_class="rapport_medical",
        class_confidence=1.0, class_source="zones", regime="cnesst",
        legibility=Legibility.LEGIBLE, kind=UnitKind.PRIMARY,
    )
    u.row_date = RowDate(
        value=row_date,
        status=RowStatus.RESOLVED if row_date else RowStatus.UNDATED,
        role=DateRole.EXAM,
        rule="test",
        explanation="fixture",
    )
    return u


EVENTS = [
    claimevents.ClaimEvent(date(1990, 5, 8), ("u_rem_1992",)),
    claimevents.ClaimEvent(date(2011, 3, 2), ()),
    claimevents.ClaimEvent(date(2022, 3, 14), ()),
]


def test_the_event_date_a_unit_states_wins_over_any_inference():
    """Prefer the file's own words. A 1992 REM that names the 1990 accident belongs to the
    1990 claim however the surrounding pages are dated."""
    got = claimevents.attribute(
        _unit("u1", date(1992, 12, 10)), [date(1990, 5, 8)], EVENTS
    )

    assert got.event == date(1990, 5, 8)
    assert got.declared
    assert got.rule == "claim_event.declared"


def test_a_unit_is_placed_on_the_most_recent_claim_event_before_it():
    """A report cannot document an accident that has not happened yet."""
    got = claimevents.attribute(_unit("u2", date(2012, 6, 1)), [], EVENTS)

    assert got.event == date(2011, 3, 2)
    assert not got.declared


def test_a_report_predating_every_claim_event_is_left_unattributed():
    """An RRA of a 1990 claim pulls older reports into a 2022 file (§5.1). Assigning them
    to the nearest event because it is closer would be confidently wrong."""
    got = claimevents.attribute(_unit("u3", date(1988, 1, 4)), [], EVENTS)

    assert got.unattributed
    assert got.rule == "claim_event.precedes_all"


def test_an_undated_unit_is_never_guessed_onto_a_claim():
    got = claimevents.attribute(_unit("u4", None), [], EVENTS)

    assert got.unattributed
    assert got.rule == "claim_event.undated_unit"


def test_a_file_with_no_event_date_says_so_rather_than_inventing_one():
    got = claimevents.attribute(_unit("u5", date(2022, 4, 2)), [], [])

    assert got.unattributed
    assert got.rule == "claim_event.none_in_file"


def test_events_are_read_from_the_role_the_date_model_already_assigns(store):
    """`date de l'événement` is extracted with role EVENT and is structurally ineligible
    to become a row date (§8.4). It was never a competitor — it was always this."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        events = claimevents.events_for_case(conn, case_id)

    assert [e.value for e in events] == [date(2022, 3, 14)]
    # The unit whose text states it is recorded as the evidence for it.
    assert events[0].declared_by


def test_every_unit_in_a_real_fixture_gets_an_attribution(store):
    """Nothing is dropped: a unit the engine cannot place is reported unattributed with a
    rule, not omitted from the dimension (§3.4)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "tiny")
        attributions = claimevents.attribute_case(conn, case_id)

    assert len(attributions) == 4
    assert all(a.rule for a in attributions)
    assert all(a.event == date(2022, 3, 14) for a in attributions if not a.unattributed)


def test_a_two_digit_event_year_resolves_against_the_file_not_a_pivot(store):
    """`90-05-08` on a 1992 REM is 1990 (§8.4)."""
    with db.session(store.db_path) as conn:
        case_id = build_case(conn, "hard")
        events = claimevents.events_for_case(conn, case_id)

    assert date(1990, 5, 8) in [e.value for e in events]
