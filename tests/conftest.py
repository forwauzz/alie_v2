"""Test fixtures. Every test runs against a throwaway store and the synthetic PDFs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _fixture_pdfs():
    from alie.devkit import fixtures

    fixtures.build()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated database and blob directory per test."""
    monkeypatch.setenv("ALIE_VAR_DIR", str(tmp_path))

    import alie.config as config

    settings = config.load_settings()
    monkeypatch.setattr(config, "SETTINGS", settings)
    for module in (
        "alie.stores.db", "alie.stores.blobs", "alie.stores.audit", "alie.api.app",
        "alie.cli",
        # These write eval artifacts and the tracking store. Without them a test run
        # scribbles into the real `var/`, which is how `var/eval` appeared in a working
        # tree that had never run a real sweep.
        "alie.eval.mlflow_sink", "alie.eval.tracking",
    ):
        mod = __import__(module, fromlist=["SETTINGS"])
        if hasattr(mod, "SETTINGS"):
            monkeypatch.setattr(mod, "SETTINGS", settings)

    from alie.stores import db

    db.migrate(settings.db_path)
    return settings


@pytest.fixture
def pack():
    from alie.packs import load

    return load("cnesst")


@pytest.fixture
def env_actor(monkeypatch):
    monkeypatch.setenv("ALIE_ACTOR", "test")
    yield "test"
    os.environ.pop("ALIE_ACTOR", None)
