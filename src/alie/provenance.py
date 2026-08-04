"""Producer stamping and derived-artifact keying (PRD §9, rules 1 and 2).

Rule 1: stamp the producer on every artifact.
Rule 2: key derived artifacts by input hash + producer config, so switching OCR engines
recomputes only affected pages and leaves the born-digital majority untouched.

A case whose pages were parsed by two engines with no record of which is which is worse
than never having had the flag.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Bumped when a component's output changes in a way that invalidates stored artifacts.
PARSER_VERSION = "textlayer-1"
OCR_VERSION = "none"


@dataclass(frozen=True)
class Producer:
    """The `{parser, ocr, model, prompt}` version set that produced an artifact."""

    parser: str = PARSER_VERSION
    ocr: str = OCR_VERSION
    model: str | None = None
    prompt: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_json(raw: str | None) -> Producer:
        if not raw:
            return Producer()
        d = json.loads(raw)
        return Producer(
            parser=d.get("parser", PARSER_VERSION),
            ocr=d.get("ocr", OCR_VERSION),
            model=d.get("model"),
            prompt=d.get("prompt"),
            extra=d.get("extra", {}),
        )


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8"))


def derived_key(input_hash: str, producer: Producer, *, salt: str = "") -> str:
    """Cache key for a derived artifact: input content + the config that produced it."""
    payload = f"{input_hash}|{producer.to_json()}|{salt}"
    return hash_text(payload)[:32]
