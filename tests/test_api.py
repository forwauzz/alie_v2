"""The API surface and the agent-QA loop (PRD §13.2).

`make dev` -> `/dev/reset` -> drive -> assert `/dev/state` -> read logs. QA asserts on
facts instead of scraping a progress bar or sleeping.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from alie.devkit import fixtures


@pytest.fixture
def client(store):
    from alie.api.app import app

    with TestClient(app) as c:
        yield c


def _await_run(client, run_id: str, timeout: float = 60.0) -> dict:
    """Wait for a run to reach a terminal state.

    Jobs never run inside a request, so the pipeline is genuinely asynchronous. The test
    helps drain the queue rather than only sleeping, but it must not assume it won the
    race for every job — the API's own background worker is running too.
    """
    from alie.worker import drain

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        drain()
        run = client.get(f"/runs/{run_id}").json()
        terminal = ("done", "failed", "superseded")
        if run["status"] in terminal and run["jobs"]:
            if all(j["status"] in terminal for j in run["jobs"]):
                return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_health_is_a_deterministic_readiness_check(client):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert "cnesst" in body["packs"]


def test_fixtures_are_seeded_when_the_database_is_empty(client):
    """Fresh clone to testable app in one step (§13.2)."""
    names = {c["name"] for c in client.get("/cases").json()}
    assert names == set(fixtures.EXPECTED)


def test_dev_reset_restores_known_state(client):
    client.post("/cases", json={"name": "scratch", "pack": "cnesst"})
    assert any(c["name"] == "scratch" for c in client.get("/cases").json())

    client.post("/dev/reset")
    names = {c["name"] for c in client.get("/cases").json()}
    assert names == set(fixtures.EXPECTED)


def test_dev_state_reports_counts_as_json(client):
    state = client.get("/dev/state").json()
    hard = next(c for c in state["cases"] if c["name"] == "hard")

    assert hard["bundles"] == 1
    assert hard["pages"] == 8
    assert state["actor"] == "local"


def test_unknown_pack_is_rejected(client):
    assert client.post("/cases", json={"name": "x", "pack": "nope"}).status_code == 400


def test_run_records_its_resolved_flag_set(client):
    """A run is immutable and records its resolved flag set (§9 rule 3)."""
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "hard")
    run = client.post(
        f"/cases/{case_id}/runs", json={"flags": {"manifest.orphan_rejoin": True}}
    ).json()

    assert run["flags"]["manifest.orphan_rejoin"] is True
    assert run["flags"]["parse.ocr"] is False  # defaults are captured, not just overrides
    assert run["pack_versions"]["cnesst"]
    assert "manifest.orphan_rejoin" in run["output_affecting_flags"]


def test_unknown_flag_is_rejected_at_the_api(client):
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "hard")
    response = client.post(f"/cases/{case_id}/runs", json={"flags": {"nope": 1}})

    assert response.status_code == 400


def test_end_to_end_run_produces_a_validated_chronology(client):
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "hard")
    run_id = client.post(
        f"/cases/{case_id}/runs", json={"flags": {"manifest.orphan_rejoin": True}}
    ).json()["id"]

    run = _await_run(client, run_id)
    assert run["status"] == "done"
    assert set(run["stage_progress"]) == {"parse", "manifest", "structured", "assemble", "render"}

    payload = client.get(f"/runs/{run_id}/rows").json()
    assert payload["validation"]["passes"] is True
    assert payload["validation"]["uncited"] == 0
    assert payload["validation"]["coverage"] == 1.0
    assert len(payload["rows"]) == 6

    export = client.get(f"/runs/{run_id}/export.md").text
    assert "SANS DATE" in export


def test_plan_is_the_manifest_summary_in_readable_form(client):
    """A request produces a plan, not an answer (§4.1)."""
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "hard")
    run_id = client.post(
        f"/cases/{case_id}/runs", json={"flags": {"manifest.orphan_rejoin": True}}
    ).json()["id"]
    _await_run(client, run_id)

    plan = client.get(f"/cases/{case_id}/plan").json()
    assert plan["units"] == 6
    assert plan["pages"] == 8
    assert plan["flagged"]["illegible"] == 1
    assert plan["flagged"]["ambiguous"] == 1
    assert "report units" in plan["summary"]


def test_why_panel_answers_why_does_this_row_say_this(client):
    """The trust surface: rule that fired and its epistemic tag, the source span, the
    resolved date decision (§7.1)."""
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "hard")
    run_id = client.post(
        f"/cases/{case_id}/runs", json={"flags": {"manifest.orphan_rejoin": True}}
    ).json()["id"]
    _await_run(client, run_id)

    units = client.get(f"/cases/{case_id}/units").json()
    rem = next(u for u in units if u["form_serial"] == "2064")
    why = client.get(f"/units/{rem['id']}/why").json()

    assert why["row_date"]["role"] == "exam"
    assert why["row_date"]["explanation"]
    assert why["form"] == {"serial": "2064", "revision": "2012-06"}

    roles = {d["role"] for d in why["dates_found"]}
    assert {"exam", "event"} <= roles
    assert any(d["role"] == "event" and not d["eligible"] for d in why["dates_found"])

    tagged = [r for r in why["records"] if r["epistemic_tag"]]
    assert tagged and all(r["epistemic_tag"] in {"KEY", "INF-H", "INF-L", "PROP", "GAP"}
                          for r in tagged)
    assert why["audit"]


def test_source_crop_exposes_the_bbox_a_checkbox_read_cites(client):
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "hard")
    run_id = client.post(
        f"/cases/{case_id}/runs", json={"flags": {"manifest.orphan_rejoin": True}}
    ).json()["id"]
    _await_run(client, run_id)

    units = client.get(f"/cases/{case_id}/units").json()
    rem = next(u for u in units if u["form_serial"] == "2064")
    why = client.get(f"/units/{rem['id']}/why").json()
    record = next(r for r in why["records"] if r["field"] == "atteinte_permanente")

    crop = client.get(f"/blocks/{record['block_id']}").json()
    assert crop["type"] == "checkbox"
    assert len(crop["bbox"]) == 4
    assert crop["bbox"][2] > crop["bbox"][0]


def test_correction_targets_the_manifest_and_asks_for_a_rerun(client):
    """Corrections write to the manifest, not the output (§10.2)."""
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "hard")
    run_id = client.post(
        f"/cases/{case_id}/runs", json={"flags": {"manifest.orphan_rejoin": True}}
    ).json()["id"]
    _await_run(client, run_id)

    units = client.get(f"/cases/{case_id}/units").json()
    ambiguous = next(u for u in units if u["date_status"] == "ambiguous")
    response = client.post(
        "/corrections",
        json={
            "subject_type": "unit", "subject_id": ambiguous["id"],
            "field": "row_date", "new_value": "2004-03-02",
        },
    )
    assert response.status_code == 201
    assert response.json()["requires_rerun"] is True

    run_id = client.post(
        f"/cases/{case_id}/runs", json={"flags": {"manifest.orphan_rejoin": True}}
    ).json()["id"]
    _await_run(client, run_id)

    rows = client.get(f"/runs/{run_id}/rows").json()["rows"]
    corrected = next(r for r in rows if r["date"] == "2004-03-02")
    assert corrected["date_status"] == "manual"


def test_flag_register_is_exposed_with_its_metrics(client):
    body = client.get("/flags").json()
    ocr = next(f for f in body["flags"] if f["id"] == "parse.ocr")

    assert ocr["default"] is False
    assert ocr["metric"] == "% pages queued as unparseable"
    assert ocr["requires_rerun"] is True
    assert len(body["safety_invariants"]) == 3


def test_audit_log_records_who_or_what_decided_each_thing(client):
    """Not a debugging convenience; it is what the firm needs when asked how the
    chronology was produced (§4.5)."""
    case_id = next(c["id"] for c in client.get("/cases").json() if c["name"] == "tiny")
    run_id = client.post(f"/cases/{case_id}/runs", json={}).json()["id"]
    _await_run(client, run_id)

    entries = client.get(f"/runs/{run_id}/audit").json()
    actions = {e["action"] for e in entries}

    assert {"approve", "parse", "manifest", "manifest_unit", "render"} <= actions
    assert all(e["actor"] == "local" for e in entries)
    assert all(e["ts"] for e in entries)
