"""
manifest.py — provenance for one dataset, and nothing that discloses it.

The manifest answers "what am I looking at, and can I rebuild it?" and refuses
to answer "what is the answer?". It carries the five reproducibility inputs of
`SCENARIO_SPECIFICATION.md` §5 and their single comparable form, plus the
content hashes `PHASE_1_ACCEPTANCE.md` A1 uses as its portable oracle::

    config_sha256 ┐
    world_sha256  ├─▶ build_fingerprint     the input side of A1
    seed          │
    fabsim_version│
    schema_version┘

    content_sha256   canonical row-level digest of the observable plane
    files{}          sha256 per emitted file, truth included

**No scenario name, ever** (anti-leakage rule D5, ADR-013). `dataset_id` is
`scn-<12 hex of the config digest>-s<seed>`, and the digest is taken with the
scenario's name and description removed, so neither half of the id discloses
what the dataset contains. The slug lives in `truth.json` and in the
maintainers' index, both of which are on the hidden side of the boundary.

`created_at` is the only wall-clock value in the pipeline and is excluded from
every hash, so two builds of one dataset differ in exactly that field and
nowhere else — which is the property A1 states and a test pins.

The truth file's digest is listed here on purpose. A hash is not a disclosure:
it says the hidden plane exists and lets a benchmark prove it was not edited
between generation and scoring, without putting a byte of it in the observable
directory.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "MANIFEST_SCHEMA",
    "build_manifest",
    "emit_manifest",
    "file_sha256",
    "manifest_json",
]

#: Versioned like every other artifact this project emits.
MANIFEST_SCHEMA = "fabsim.manifest/v1"

#: The one field a manifest carries that is not a function of its inputs, and
#: therefore the one field excluded from every comparison (A1).
VOLATILE_FIELDS = ("created_at",)


def file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(identity: Any, *, content_sha256: str,
                   row_counts: Mapping[str, int],
                   files: Mapping[str, str],
                   created_at: str | None = None) -> dict[str, Any]:
    """Assemble the manifest. Pure apart from `created_at`, which is passed in.

    The clock is a parameter rather than a call, so a caller that wants a
    fully deterministic manifest — a test comparing two builds byte for byte
    — can supply one, and the default path still records when the build ran.
    """
    return {
        "schema": MANIFEST_SCHEMA,
        "dataset_id": identity.dataset_id,
        "scenario_id": identity.scenario_id,
        "config_sha256": identity.config_sha256,
        "world_sha256": identity.world_sha256,
        "seed": identity.seed,
        "fabsim_version": identity.fabsim_version,
        "schema_version": identity.schema_version,
        "build_fingerprint": identity.build_fingerprint,
        "content_sha256": content_sha256,
        "row_counts": dict(sorted(row_counts.items())),
        "files": dict(sorted(files.items())),
        "created_at": created_at if created_at is not None else
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def manifest_json(manifest: Mapping[str, Any]) -> str:
    """Canonical JSON, like the truth artifact's: sorted, compact, ASCII."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False) + "\n"


def stable_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest without its clock — what two builds must agree on."""
    return {key: value for key, value in manifest.items()
            if key not in VOLATILE_FIELDS}


def emit_manifest(manifest: Mapping[str, Any], directory: Path) -> None:
    (directory / "manifest.json").write_bytes(
        manifest_json(manifest).encode("utf-8"))
