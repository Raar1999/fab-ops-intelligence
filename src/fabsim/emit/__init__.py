"""
fabsim.emit — one build, two planes, one manifest.

The last stage of `FABSIM_DESIGN.md` §4. Everything upstream of this package
produces in-memory state; this is where a dataset becomes files, and where the
boundary ADR-013 draws stops being a convention about who reads what and
becomes a fact about which directory something is in::

    data/scenarios/<dataset_id>/
    ├── fab.db                  observable plane   ─┐
    ├── fab_database.sql        observable plane    ├─ fabops may read
    ├── manifest.json           provenance         ─┘
    └── truth/
        └── truth.json          hidden plane        ── eval/ only

`build_dataset` runs the whole pipeline and writes all four. It is the only
function in the package that does both planes; the two emitters underneath it
do not import each other, and neither is importable from `fabops` without the
code-plane lint failing.

**Nothing here decides anything.** By the time `emit` is called every number
already exists: the emitters serialize, digest and write. That is what keeps a
build reproducible — the emitter contributes no entropy — and it is what makes
"the observable plane cannot contain an answer" a property of the projection's
*inputs* rather than a promise about its care.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fabsim import SCHEMA_VERSION, __version__
from fabsim.defects import DefectPopulation, inspect_response
from fabsim.die import DiePopulation, probe_response
from fabsim.emit.manifest import (
    build_manifest,
    emit_manifest,
    file_sha256,
    manifest_json,
    stable_view,
)
from fabsim.emit.observable import (
    ObservableDataset,
    content_sha256,
    emit_observable,
)
from fabsim.emit.truth import build_truth, emit_truth, truth_json
from fabsim.observation import ProcessObservations, observe_response
from fabsim.response import FabResponse, respond_scenario
from fabsim.scenario import ScenarioConfig
from fabsim.selftest import check_dataset
from fabsim.world import World, load_world

__all__ = [
    "DATASET_ROOT",
    "Dataset",
    "ObservableHandle",
    "build_dataset",
    "build_observable",
    "realize_dataset",
]

#: Where emitted datasets live, relative to the repository root
#: (`GROUND_TRUTH_CONTRACT.md` §2). Separate from `data/fab.db`, which is the
#: legacy v1 artifact and is never touched (ADR-010).
DATASET_ROOT = Path(__file__).resolve().parents[3] / "data" / "scenarios"


@dataclass(frozen=True)
class Dataset:
    """What one build produced, in memory and on disk.

    Both planes are named on this object because the *builder* legitimately
    holds both — it wrote them. What must never happen is a consumer holding
    both, which is why nothing downstream of `fabsim` is given a `Dataset`.
    """

    directory: Path
    identity: Any
    observable: ObservableDataset
    truth: dict[str, Any]
    manifest: dict[str, Any]
    #: The realized world, for a caller that is still inside `fabsim`.
    response: FabResponse
    observations: ProcessObservations
    defects: DefectPopulation
    die: DiePopulation

    @property
    def dataset_id(self) -> str:
        return self.identity.dataset_id

    @property
    def db_path(self) -> Path:
        """The one path an analytical consumer is ever given."""
        return self.directory / "fab.db"

    @property
    def truth_path(self) -> Path:
        return self.directory / "truth" / "truth.json"


@dataclass(frozen=True)
class ObservableHandle:
    """What a build hands to a caller outside `fabsim`.

    `Dataset` above names both planes because the builder legitimately holds
    both — it wrote them. This is the shape everything downstream gets instead,
    and the difference is not politeness. A product surface that generates a
    dataset and then analyses it is the one place in this architecture where
    both privileges meet in one process, so the object it is handed is the
    place to make the hidden plane **unreachable rather than unread**: there is
    no field it could arrive through, and no directory to join a name onto.

    That is the same move `probe(timeline, observations, population)` makes in
    the kill model (ADR-021) and the same one `diagnose(db_path)` makes at the
    analysis boundary — a signature that cannot express the mistake, rather
    than a rule asking a caller not to make it.

    Every field here is observable-plane provenance the manifest already
    carries in the dataset directory. The manifest's `files` map is *not*
    carried: it inventories what was written, which is the one part of a
    manifest that names the other plane. A digest is not a disclosure and the
    manifest may keep it (`manifest.py`), but a product panel rendering the
    file list has no use for the name and every reason not to print it.
    """

    dataset_id: str
    scenario_id: str
    #: The one path an analytical consumer is ever given.
    db_path: Path
    seed: int
    schema_version: str
    fabsim_version: str
    config_sha256: str
    world_sha256: str
    build_fingerprint: str
    content_sha256: str
    row_counts: Mapping[str, int]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able provenance, for a registry or a details panel."""
        return {
            "dataset_id": self.dataset_id,
            "scenario_id": self.scenario_id,
            "db_path": str(self.db_path),
            "seed": self.seed,
            "schema_version": self.schema_version,
            "fabsim_version": self.fabsim_version,
            "config_sha256": self.config_sha256,
            "world_sha256": self.world_sha256,
            "build_fingerprint": self.build_fingerprint,
            "content_sha256": self.content_sha256,
            "row_counts": dict(sorted(self.row_counts.items())),
            "created_at": self.created_at,
        }


def realize_dataset(config: ScenarioConfig, seed: int | None = None, *,
                    world: World | None = None
                    ) -> tuple[FabResponse, ProcessObservations,
                               DefectPopulation, DiePopulation]:
    """Run the physics for one scenario. No files, no digests, no identity.

    Split out from `build_dataset` so a caller can realize a world without
    writing one — which is what most of the test suite does, and what keeps
    the emitters testable against a realization they did not have to write to
    disk first.
    """
    resolved = world if world is not None else load_world(config.world)
    response = respond_scenario(config, seed, world=resolved)
    observations = observe_response(response)
    defects = inspect_response(response)
    die = probe_response(response, observations, defects)
    return response, observations, defects, die


def build_dataset(config: ScenarioConfig, seed: int | None = None, *,
                  world: World | None = None,
                  root: Path | None = None,
                  created_at: str | None = None,
                  selftest: bool = True) -> Dataset:
    """Build one dataset end to end and write both planes.

    Stages 1–7 of `FABSIM_DESIGN.md` §4: resolve identity, realize the world,
    emit the observable plane, emit the hidden truth, write the manifest, and
    run the generation self-test — which fails the build rather than shipping
    a dataset that violates its own invariants.

    `root` defaults to `data/scenarios/`; tests pass a temporary directory, so
    nothing writes into the repository by accident. `created_at` may be pinned
    to make a manifest byte-deterministic.
    """
    resolved = world if world is not None else load_world(config.world)
    identity = config.dataset_identity(seed,
                                       world_sha256=resolved.world_sha256)

    response, observations, defects, die = realize_dataset(
        config, seed, world=resolved)

    directory = (root if root is not None else DATASET_ROOT) / identity.dataset_id
    directory.mkdir(parents=True, exist_ok=True)

    observable = emit_observable(
        response.timeline, observations, defects, die, response.alarms,
        directory, dataset_id=identity.dataset_id,
        fabsim_version=identity.fabsim_version,
        schema_version=identity.schema_version)

    truth = emit_truth(
        config, response, defects, die, directory,
        dataset_id=identity.dataset_id, scenario_id=identity.scenario_id,
        seed=identity.seed, fabsim_version=identity.fabsim_version,
        schema_version=identity.schema_version)

    files = {
        "fab.db": file_sha256(directory / "fab.db"),
        "fab_database.sql": file_sha256(directory / "fab_database.sql"),
        "truth/truth.json": file_sha256(directory / "truth" / "truth.json"),
    }
    manifest = build_manifest(
        identity, content_sha256=observable.content_sha256(),
        row_counts=observable.row_counts(), files=files,
        created_at=created_at)
    emit_manifest(manifest, directory)

    dataset = Dataset(directory=directory, identity=identity,
                      observable=observable, truth=truth, manifest=manifest,
                      response=response, observations=observations,
                      defects=defects, die=die)
    if selftest:
        check_dataset(dataset)
    return dataset


def build_observable(config: ScenarioConfig, seed: int | None = None, *,
                     world: World | None = None,
                     root: Path,
                     created_at: str | None = None,
                     selftest: bool = True) -> ObservableHandle:
    """Build one dataset and return **only** its observable provenance.

    The same build as `build_dataset` — both planes are written, because a
    dataset without its answer key could never be scored — followed by a
    projection onto the observable side. What the caller receives is an
    `ObservableHandle`: a database path and the provenance a consumer is
    entitled to, with no truth object, no `Dataset`, and no directory.

    This is the entry point for the **product plane**, which is the only place
    in this architecture that both generates a dataset and then analyses one.
    `build_dataset` stays where it is for `fabsim`'s own callers and for the
    evaluator, which is the one component allowed to hold both planes at once.

    `root` is keyword-only and has **no default**, unlike `build_dataset`'s.
    The default there is `data/scenarios/`, so a call that omitted it wrote
    into the repository; requiring it makes the destination a decision at every
    call site, which is the rule `fabeval` already holds itself to and for the
    same reason.
    """
    dataset = build_dataset(config, seed, world=world, root=root,
                            created_at=created_at, selftest=selftest)
    manifest = dataset.manifest
    return ObservableHandle(
        dataset_id=str(manifest["dataset_id"]),
        scenario_id=str(manifest["scenario_id"]),
        db_path=dataset.db_path,
        seed=int(manifest["seed"]),
        schema_version=str(manifest["schema_version"]),
        fabsim_version=str(manifest["fabsim_version"]),
        config_sha256=str(manifest["config_sha256"]),
        world_sha256=str(manifest["world_sha256"]),
        build_fingerprint=str(manifest["build_fingerprint"]),
        content_sha256=str(manifest["content_sha256"]),
        row_counts=dict(manifest["row_counts"]),
        created_at=str(manifest["created_at"]),
    )
