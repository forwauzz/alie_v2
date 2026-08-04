"""Shared test helpers."""

from __future__ import annotations


def build_case(conn, name: str, *, pack_id: str = "cnesst", rejoin: bool = True) -> str:
    """Ingest, parse, manifest and structured-read a whole fixture. Returns the case id."""
    from alie import flags
    from alie.devkit import fixtures
    from alie.stages import ingest, manifest_build, parse, structured
    from alie.stores import cases, manifest

    resolved = flags.resolve(run_flags={"manifest.orphan_rejoin": rejoin})
    case_id = cases.create_case(conn, name, pack_id)
    for folder, filename in fixtures.EXPECTED[name]["bundles"].items():
        bundle_id = ingest.add_pdf_path(
            conn, case_id=case_id, path=fixtures.fixture_path(name, filename),
            folder_label=folder,
        )
        parse.run(conn, bundle_id)
        manifest_build.run(conn, bundle_id, flags=resolved)
        for unit in manifest.units_for_bundle(conn, bundle_id):
            structured.run_unit(conn, unit.id)
    return case_id
