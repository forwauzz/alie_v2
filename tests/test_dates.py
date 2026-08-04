"""The date model (PRD §8.4). A page has a *set* of dates, each with a role."""

from __future__ import annotations

from datetime import date

import pytest

from alie.manifest import datefind, dateselect
from alie.models import ELIGIBLE_ROLES, INELIGIBLE_ROLES, DateRole, Legibility, RowStatus

TODAY = date(2026, 8, 4)
ANCHORS = (2012, 2023)


@pytest.fixture
def role_for(pack):
    return datefind.RoleResolver(pack.date_roles)


def find(text, role_for, anchors=ANCHORS):
    return datefind.find_in_text(
        text, block_id="b1", pdf_index=1, role_for=role_for, anchors=anchors, today=TODAY
    )


def test_event_and_exam_dates_coexist_with_distinct_roles(role_for):
    """On a REM, `90-05-08` (événement) sits two lines from `92-12-10` (examen). A
    pipeline returning one date has already lost."""
    facts = find("Date de l'événement: 90-05-08", role_for) + find(
        "Date de l'examen: 92-12-10", role_for
    )

    roles = {f.role: f.readings[0] for f in facts}
    assert roles[DateRole.EVENT] == date(1990, 5, 8)
    assert roles[DateRole.EXAM] == date(1992, 12, 10)


def test_event_date_is_structurally_ineligible():
    assert DateRole.EVENT in INELIGIBLE_ROLES
    assert DateRole.EVENT not in ELIGIBLE_ROLES
    for role in (DateRole.BIRTH, DateRole.FAX, DateRole.RECEIVED, DateRole.PRINT):
        assert role in INELIGIBLE_ROLES


def test_ambiguous_date_returns_both_readings(role_for):
    """`02-03-04` returns both readings; the row renders `(?)` (§8.4)."""
    (fact,) = find("Date de l'examen: 02-03-04", role_for)

    assert fact.is_ambiguous
    assert set(fact.readings) == {date(2002, 3, 4), date(2004, 3, 2)}
    assert fact.value is None


def test_two_digit_year_resolves_against_file_anchors_not_a_pivot(role_for):
    """A file spanning 1990-2026 breaks every fixed century pivot (§8.4)."""
    (old,) = find("Date de l'examen: 92-12-10", role_for, anchors=(1990, 1996))
    assert old.readings[0].year == 1992
    assert old.century_inferred

    (recent,) = find("Date de l'examen: 24-01-15", role_for, anchors=(2020, 2025))
    assert recent.readings[0].year == 2024


def test_upper_bound_is_today_never_an_inferred_maximum(role_for):
    facts = find("Date de l'examen: 2099-01-01", role_for)
    assert facts == []


def test_role_cues_do_not_match_inside_longer_words(role_for):
    """`n[ée] le` inside `Signé le` would file a signature date as a date of birth,
    making it structurally ineligible and silently costing the row its date."""
    (fact,) = find("Signé le 12 décembre 1992", role_for)
    assert fact.role is DateRole.SIGNATURE

    (prevu,) = find("Réévaluation prévue le 2024-01-05", role_for)
    assert prevu.role is DateRole.UNKNOWN


def test_rem_row_date_is_the_exam_date(pack, role_for):
    facts = find("Date de l'événement: 90-05-08", role_for) + find(
        "Date de l'examen: 92-12-10", role_for
    )
    chosen = dateselect.select(facts, doc_class="rapport_evaluation_medicale", pack=pack)

    assert chosen.value == date(1992, 12, 10)
    assert chosen.role is DateRole.EXAM
    assert chosen.status is RowStatus.INFERRED  # century resolved from anchors
    assert "ineligible" in chosen.explanation


def test_ambiguous_selection_carries_alternatives_and_renders_a_marker(pack, role_for):
    facts = find("Date de l'examen: 02-03-04", role_for)
    chosen = dateselect.select(facts, doc_class="certificat_medical", pack=pack)

    assert chosen.status is RowStatus.AMBIGUOUS
    assert chosen.alternatives == (date(2004, 3, 2),)
    assert chosen.render().endswith("(?)")


def test_only_ineligible_dates_present_yields_undated_with_a_reason(pack, role_for):
    facts = find("Date de naissance: 1974-08-21", role_for)
    chosen = dateselect.select(facts, doc_class="note_consultation", pack=pack)

    assert chosen.status is RowStatus.UNDATED
    assert chosen.value is None
    assert "ineligible" in chosen.explanation


def test_illegible_unit_short_circuits_and_names_the_model_gate(pack, role_for):
    facts = find("Date de la visite: 2023-08-03", role_for)
    chosen = dateselect.select(
        facts, doc_class="note_consultation", pack=pack, legibility=Legibility.ILLEGIBLE
    )

    assert chosen.status is RowStatus.ILLEGIBLE
    assert chosen.value is None
    assert "model" in chosen.explanation


def test_every_selection_explains_itself_in_one_line(pack, role_for):
    facts = find("Date de la visite: 2023-08-03", role_for)
    chosen = dateselect.select(facts, doc_class="note_consultation", pack=pack)

    assert chosen.explanation
    assert "\n" not in chosen.explanation
    assert chosen.rule.startswith("cnesst.dates.")
