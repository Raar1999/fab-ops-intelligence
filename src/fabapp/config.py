"""
config.py — where the product keeps things, and how a user moves them.

Three locations and one rule. The rule is that **nothing here is discovered by
guessing**: each location is either an environment variable the user set or a
path derived from the installed package, and `describe()` prints all of them so
that "where did my dataset go?" is answerable from the application itself.

    workspace root      the checkout this package was installed from
      ├── scenarios/    the scenario configurations   FABOPS_SCENARIO_ROOT
      ├── data/scenarios/       generated datasets    FABOPS_DATASET_ROOT
      └── data/fab.db   the legacy v1 demo database (never a v2 dataset)

**This application writes no state of its own, and that is a design result
rather than an omission.** The obvious way to show a user which scenario a
dataset came from is to record their choice in a catalog. It is not needed: a
manifest carries `scenario_id`, which is the digest of the configuration, so
`fabapp.scenarios` recovers the slug by digesting the library it can already
read. Nothing is written, nothing can fall out of sync, and a dataset built by
the command line, the benchmark or the test suite resolves exactly as well as
one built here. The emitted dataset directory stays opaque either way, which is
what `ANTI_LEAKAGE_DESIGN.md` §4 rule 4 requires of the unit that travels.

**Why a source checkout is required, and why that is not this package's
choice.** `fabsim.world` resolves its world registry relative to the checkout,
so the simulator has always needed one; a product that pretended otherwise
would fail later and less clearly. This module therefore locates the checkout
once, verifies it, and fails with a sentence a user can act on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DATASET_ROOT_ENV",
    "SCENARIO_ROOT_ENV",
    "WORKSPACE_ENV",
    "Locations",
    "WorkspaceError",
    "dataset_root",
    "legacy_database",
    "locations",
    "scenario_root",
    "workspace_root",
]

#: Environment overrides. Named for the product rather than for the package, so
#: that a user setting one is setting something they can see in the interface.
WORKSPACE_ENV = "FABOPS_HOME"
SCENARIO_ROOT_ENV = "FABOPS_SCENARIO_ROOT"
DATASET_ROOT_ENV = "FABOPS_DATASET_ROOT"

#: What makes a directory recognisably this project's checkout. Both, not
#: either: a bare `pyproject.toml` is any Python project, and a bare
#: `scenarios/` directory is a coincidence.
_MARKERS = ("pyproject.toml", "scenarios")


class WorkspaceError(RuntimeError):
    """The product could not locate the checkout it needs to run."""


def _from_environment(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def workspace_root() -> Path:
    """The checkout this installation reads its scenarios and world from.

    `FABOPS_HOME` wins if it is set. Otherwise it is the directory the
    installed package sits in — `src/fabapp/config.py` -> parents[2] — which is
    the same derivation `fabops.config` and `fabsim.world` already use.
    """
    override = _from_environment(WORKSPACE_ENV)
    if override is not None:
        _verify(override, f"{WORKSPACE_ENV}={override}")
        return override
    root = Path(__file__).resolve().parents[2]
    _verify(root, f"derived from the installed package at {root}")
    return root


def _verify(root: Path, how: str) -> None:
    missing = [name for name in _MARKERS if not (root / name).exists()]
    if missing:
        raise WorkspaceError(
            f"{root} does not look like a Fab Ops checkout ({how}); it is "
            f"missing {', '.join(missing)}. FabOps reads its scenario "
            f"configurations and its world template from the repository, so "
            f"it needs one: clone the repository, run "
            f'`pip install -e ".[app]"` inside it, or point '
            f"{WORKSPACE_ENV} at the checkout.")


def scenario_root() -> Path:
    """Where the scenario configurations live."""
    return _from_environment(SCENARIO_ROOT_ENV) or workspace_root() / "scenarios"


def dataset_root() -> Path:
    """Where generated datasets are written and discovered.

    This is `fabsim.emit.DATASET_ROOT` by default and is deliberately not
    imported from there: the product owns where *its* datasets go, and every
    call it makes into the emitter passes the root explicitly.
    """
    override = _from_environment(DATASET_ROOT_ENV)
    return override if override is not None \
        else workspace_root() / "data" / "scenarios"


def legacy_database() -> Path:
    """The schema v1 demo database.

    Named here so the application can *recognise* it and say what it is. It is
    never opened as a v2 dataset — validation is by declared schema version, so
    the rejection does not depend on this path being right — but a user who
    types it deserves to be told which fab it holds rather than that a table is
    missing.
    """
    return workspace_root() / "data" / "fab.db"


@dataclass(frozen=True)
class Locations:
    """Every path the product uses, with how each one was decided."""

    workspace: Path
    scenarios: Path
    datasets: Path
    legacy_database: Path
    overrides: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace": str(self.workspace),
            "scenarios": str(self.scenarios),
            "datasets": str(self.datasets),
            "legacy_database": str(self.legacy_database),
            "overrides": {name: value for name, value in self.overrides},
        }


def locations() -> Locations:
    """Resolve everything at once, for a details panel or a diagnostic."""
    return Locations(
        workspace=workspace_root(),
        scenarios=scenario_root(),
        datasets=dataset_root(),
        legacy_database=legacy_database(),
        overrides=tuple(
            (name, os.environ[name]) for name in
            (WORKSPACE_ENV, SCENARIO_ROOT_ENV, DATASET_ROOT_ENV)
            if os.environ.get(name, "").strip()),
    )
