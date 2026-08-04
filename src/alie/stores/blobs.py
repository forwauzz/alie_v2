"""Blob-store seam (PRD §13.4): `get/put(key)`. Content-addressed, local filesystem.

Swapping this for object storage is a deployment detail; nothing above it knows the
difference.
"""

from __future__ import annotations

from pathlib import Path

from ..config import SETTINGS, ensure_dirs
from ..provenance import hash_bytes


def _path(key: str) -> Path:
    return SETTINGS.blob_dir / key[:2] / key


def put(data: bytes, *, key: str | None = None) -> str:
    ensure_dirs()
    key = key or hash_bytes(data)
    dest = _path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return key


def put_file(src: Path) -> str:
    return put(src.read_bytes())


def get(key: str) -> bytes:
    dest = _path(key)
    if not dest.exists():
        raise KeyError(f"blob not found: {key}")
    return dest.read_bytes()


def path_for(key: str) -> Path:
    """Direct path, for readers that stream rather than load (the PDF parser)."""
    dest = _path(key)
    if not dest.exists():
        raise KeyError(f"blob not found: {key}")
    return dest


def exists(key: str) -> bool:
    return _path(key).exists()
