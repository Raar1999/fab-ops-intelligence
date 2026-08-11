"""
registry.py — finding datasets, and refusing the ones that are not.

The product's answer to "open a dataset" is a list, not a path box. This module
builds that list: it walks the dataset root, opens each candidate through the
semantic layer, and returns a record per dataset carrying what a person needs
to choose between them and what an auditor needs to trust one.

**Validation is by declared schema, never by filename.** The legacy schema v1
database is the hazard this repository has always had to design around: v1 and
v2 share table names where the intent carries over, so a v2 surface that opened
one would answer about a different fab with no error anywhere. `open_layer`
already refuses anything that does not declare schema 2.0, so a v1 database is
rejected by the same rule that rejects a text file — and the path of the legacy
demo is used only to *explain* the rejection, never to detect it. Renaming that
file, or pointing the product at some other v1 database, changes nothing.

**A record is provenance, not content.** Where a manifest exists it supplies
the reproducibility inputs; where it does not, the database still supplies its
own identity, and the record says which is missing. The manifest's file
inventory is read past rather than carried: this package projects the manifest
onto a fixed list of keys, so a field naming the other plane has no route onto
a screen.

Four statuses, and each one is a different sentence to a user:

    READY     the layer opened it and it has material in it
    EMPTY     the layer opened it and it has none — a build that was cut short
    INVALID   the layer refused it, with the reason it gave
    MISSING   there is no database at that path at all
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from fabops.semantic import open_layer, schema_version_of

from fabapp.config import dataset_root, legacy_database
from fabapp.scenarios import slug_for_scenario_id

__all__ = [
    "EMPTY",
    "INVALID",
    "MANIFEST_FIELDS",
    "MISSING",
    "READY",
    "STATUSES",
    "DatasetRecord",
    "discover",
    "inspect",
    "ready_datasets",
    "resolve",
]

READY = "ready"
EMPTY = "empty"
INVALID = "invalid"
MISSING = "missing"
STATUSES = (READY, EMPTY, INVALID, MISSING)

#: The manifest keys this package will carry. An allowlist rather than a
#: blocklist, so a manifest that grows a field does not silently start
#: appearing on a screen — and so the one key that inventories the other
#: plane is excluded by construction rather than by being remembered.
MANIFEST_FIELDS = ("dataset_id", "scenario_id", "config_sha256",
                   "world_sha256", "seed", "fabsim_version", "schema_version",
                   "build_fingerprint", "content_sha256", "row_counts",
                   "created_at")

#: Tables whose emptiness means the dataset cannot be explored or diagnosed.
#: A build that was interrupted leaves a well-formed database with nothing in
#: it, and every screen downstream would render blank panels without saying why.
_MATERIAL_TABLES = ("wafers", "runs", "wafer_yield")


@dataclass(frozen=True)
class DatasetRecord:
    """One dataset, as the browser and the details panel see it."""

    db_path: Path
    status: str
    detail: str = ""
    dataset_id: str = ""
    #: The scenario slug, recovered from the manifest identity against the
    #: library. `None` when nothing matches — never a guess.
    scenario: str | None = None
    seed: int | None = None
    horizon_days: int | None = None
    schema_version: str = ""
    fabsim_version: str = ""
    scenario_id: str = ""
    config_sha256: str = ""
    world_sha256: str = ""
    build_fingerprint: str = ""
    content_sha256: str = ""
    created_at: str = ""
    row_counts: Mapping[str, int] = field(default_factory=dict)
    size_bytes: int = 0
    has_manifest: bool = False

    @property
    def usable(self) -> bool:
        """May this dataset be opened by the workspace?"""
        return self.status == READY

    @property
    def label(self) -> str:
        """One line for a selector: identity first, provenance after."""
        scenario = self.scenario or "unknown scenario"
        if self.status != READY:
            return f"{self.db_path.name} — {self.status}: {self.detail}"
        return (f"{self.dataset_id} — {scenario}"
                f"{'' if self.seed is None else f', seed {self.seed}'}"
                f"{'' if self.horizon_days is None else f', {self.horizon_days} d'}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "horizon_days": self.horizon_days,
            "schema_version": self.schema_version,
            "fabsim_version": self.fabsim_version,
            "scenario_id": self.scenario_id,
            "config_sha256": self.config_sha256,
            "world_sha256": self.world_sha256,
            "build_fingerprint": self.build_fingerprint,
            "content_sha256": self.content_sha256,
            "created_at": self.created_at,
            "status": self.status,
            "detail": self.detail,
            "db_path": str(self.db_path),
            "size_bytes": self.size_bytes,
            "has_manifest": self.has_manifest,
            "row_counts": dict(sorted(self.row_counts.items())),
        }


def _read_manifest(directory: Path) -> dict[str, Any]:
    """The manifest, projected onto `MANIFEST_FIELDS`. `{}` if unreadable."""
    path = directory / "manifest.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in MANIFEST_FIELDS if key in raw}


def _legacy_note(path: Path) -> str:
    """The extra sentence a user gets when they picked the v1 demo database."""
    try:
        if path.resolve() == legacy_database().resolve():
            return (" This is the legacy schema v1 demo database, which holds "
                    "a different fab and is read by the legacy dashboard.")
    except OSError:                                        # pragma: no cover
        pass
    return ""


def inspect(db_path: Path | str) -> DatasetRecord:
    """Validate one path and describe whatever is there.

    Never raises for a bad dataset: an unusable dataset is a thing the product
    has to *display*, and a browser that threw would take the whole list down
    because one directory was half-written.
    """
    path = Path(db_path)
    if not path.is_file():
        return DatasetRecord(db_path=path, status=MISSING,
                             detail=f"no database at {path}")

    size = path.stat().st_size
    manifest = _read_manifest(path.parent)
    try:
        connection = open_layer(path)
    except ValueError:
        # `open_layer` refuses anything not declaring schema 2.0. Re-open
        # plainly to quote what it *does* declare, because "this is a v1
        # database" is far more use to a reader than "the layer said no".
        declared = ""
        try:
            probe = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                declared = schema_version_of(probe)
            finally:
                probe.close()
        except sqlite3.Error:                              # pragma: no cover
            declared = ""
        detail = (f"not a schema v2 dataset (declares "
                  f"{declared or 'no schema version'}).{_legacy_note(path)}")
        return DatasetRecord(db_path=path, status=INVALID, detail=detail,
                             schema_version=declared, size_bytes=size,
                             has_manifest=bool(manifest))
    except (sqlite3.Error, OSError) as exc:
        return DatasetRecord(db_path=path, status=INVALID,
                             detail=f"could not be opened: {exc}",
                             size_bytes=size, has_manifest=bool(manifest))

    try:
        row = connection.execute(
            "SELECT dataset_id, schema_version, horizon_days "
            "FROM dataset_meta").fetchone()
        counts = dict(manifest.get("row_counts") or {})
        if not counts:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in _MATERIAL_TABLES}
        thin = [table for table in _MATERIAL_TABLES if not counts.get(table)]
    except sqlite3.Error as exc:
        connection.close()
        return DatasetRecord(db_path=path, status=INVALID,
                             detail=f"schema v2 declared but unreadable: {exc}",
                             size_bytes=size, has_manifest=bool(manifest))
    finally:
        connection.close()

    if row is None:
        return DatasetRecord(db_path=path, status=INVALID,
                             detail="no dataset_meta row; the dataset does not "
                                    "declare its own identity",
                             size_bytes=size, has_manifest=bool(manifest))

    scenario_id = str(manifest.get("scenario_id", ""))
    return DatasetRecord(
        db_path=path,
        status=EMPTY if thin else READY,
        detail=("no material in " + ", ".join(thin) + "; the build did not "
                "finish") if thin else "",
        dataset_id=str(row[0]),
        scenario=slug_for_scenario_id(scenario_id) if scenario_id else None,
        seed=_as_int(manifest.get("seed")),
        horizon_days=_as_int(row[2]),
        schema_version=str(row[1]),
        fabsim_version=str(manifest.get("fabsim_version", "")),
        scenario_id=scenario_id,
        config_sha256=str(manifest.get("config_sha256", "")),
        world_sha256=str(manifest.get("world_sha256", "")),
        build_fingerprint=str(manifest.get("build_fingerprint", "")),
        content_sha256=str(manifest.get("content_sha256", "")),
        created_at=str(manifest.get("created_at", "")),
        row_counts=counts,
        size_bytes=size,
        has_manifest=bool(manifest),
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def discover(root: Path | None = None) -> tuple[DatasetRecord, ...]:
    """Every dataset under `root`, newest build first, unusable ones last.

    A dataset is a directory holding a `fab.db`. Directories that hold neither
    are skipped in silence — the root is a place a user also keeps other
    things — but a directory that holds a *broken* one is reported, because
    that is the case somebody needs to see.
    """
    directory = Path(root) if root is not None else dataset_root()
    if not directory.is_dir():
        return ()
    records = [inspect(child / "fab.db")
               for child in sorted(directory.iterdir())
               if child.is_dir() and (child / "fab.db").is_file()]
    records.sort(key=lambda record: (record.status != READY,
                                     record.created_at == "",
                                     _descending(record.created_at),
                                     record.dataset_id or record.db_path.name))
    return tuple(records)


def _descending(created_at: str) -> tuple:
    """Sort key that puts the most recent build first without parsing a date.

    ISO-8601 timestamps sort lexically, so reversing the characters' order is
    a total order on the same strings and needs no clock, no timezone and no
    failure mode on a manifest that predates the field.
    """
    return tuple(-ord(character) for character in created_at)


def ready_datasets(root: Path | None = None) -> tuple[DatasetRecord, ...]:
    """The subset a workspace can actually open."""
    return tuple(record for record in discover(root) if record.usable)


def resolve(reference: str, root: Path | None = None) -> DatasetRecord:
    """Turn what a user typed into a record: a dataset id, a directory, a file.

    Accepting all three is the point. A person who has read the README types a
    dataset id; a person who has a path pastes it; a person who found the
    directory in a file browser drags that. Each resolves to the same record,
    and an unrecognised reference says what was searched rather than that
    something failed.
    """
    text = reference.strip().strip('"').strip("'")
    if not text:
        return DatasetRecord(db_path=Path(), status=MISSING,
                             detail="no dataset was named")
    path = Path(text).expanduser()
    if path.is_dir():
        return inspect(path / "fab.db")
    if path.is_file():
        return inspect(path)
    directory = Path(root) if root is not None else dataset_root()
    candidate = directory / text / "fab.db"
    if candidate.is_file():
        return inspect(candidate)
    return DatasetRecord(
        db_path=path, status=MISSING,
        detail=(f"no dataset called {text!r}: it is not a path, and "
                f"{directory} holds no directory of that name"))
