"""
generate.py — the create-a-dataset action, and everything it refuses to invent.

    create(slug, seed) -> Creation

One function, five stages, and no physics of its own:

    scenario selection   the slug the user picked, loaded by `fabsim`
            v
    generation           `fabsim` realizes the world
            v
    emission             both planes are written, as they always are
            v
    validation           the generator's own self-test, which fails the build
            v
    registration         the dataset is discoverable, and the handle names it

**The parameters are the scenario's own, and there are exactly two.** A slug
and a seed. The horizon, the lot count and the world are properties of the
configuration and are shown, not edited: a product that let a user retune them
would be minting configurations whose identity nothing has ever measured, and
every scenario's declared severity is calibrated against the world and the
horizon it ships with. `SCENARIO_SPECIFICATION.md` §5 makes those fields part
of a dataset's identity for exactly that reason.

**Validation is the generator's, not a second opinion.** The emitter runs
`check_dataset` as its last stage and raises rather than returning a dataset
that violates its own invariants, so what this module reports as "validated" is
that stage having passed. Writing a second checker here would be a competing
definition of a well-formed dataset, and the weaker one would eventually win.

**Rebuilding is free and idempotent.** A dataset is fully determined by
(configuration, world, seed, generator version, schema version), so building
the same combination twice produces the same content and lands in the same
directory. `create` says which of the two happened, and offers the existing one
without rebuilding when asked.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fabsim.emit import build_observable
from fabsim.scenario import ScenarioConfigError
from fabsim.selftest import SelfTestError
from fabsim.world import WorldTemplateError, load_world

from fabapp.config import dataset_root
from fabapp.registry import READY, DatasetRecord, inspect
from fabapp.scenarios import config_for

__all__ = ["Creation", "GenerationError", "create", "would_produce"]


class GenerationError(RuntimeError):
    """A dataset could not be built, with a sentence a user can act on.

    Carries `stage` so an interface can say *where* it stopped — selecting a
    scenario, generating, or validating — because those three fail for
    completely different reasons and lead to different next steps.
    """

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(message)


@dataclass(frozen=True)
class Creation:
    """What one create action produced."""

    record: DatasetRecord
    #: The slug the user chose. Their own input, kept here so the surface that
    #: asked can confirm what it built; it is not written into the dataset and
    #: no analysis function ever receives it.
    scenario: str
    seed: int
    #: `True` when the dataset already existed and was offered unchanged.
    reused: bool
    #: Whether the generator's own self-test ran on this build. It is `False`
    #: for a reused dataset, which was validated when it was built rather than
    #: now, and saying so is the honest version of a green tick.
    validated: bool

    def to_dict(self) -> dict[str, Any]:
        return {"scenario": self.scenario, "seed": self.seed,
                "reused": self.reused, "validated": self.validated,
                "dataset": self.record.to_dict()}


def would_produce(slug: str, seed: int | None = None, *,
                  root: Path | None = None,
                  scenario_root: Path | None = None) -> DatasetRecord:
    """Where this (scenario, seed) would land, without building anything.

    Identity is a function of the inputs, so the destination is knowable in
    advance — which is what lets the interface say "this already exists" before
    a user waits for a build rather than after.
    """
    config = _load(slug, scenario_root)
    world = _world(config)
    identity = config.dataset_identity(seed, world_sha256=world.world_sha256)
    directory = (Path(root) if root is not None else dataset_root())
    return inspect(directory / identity.dataset_id / "fab.db")


def create(slug: str, seed: int | None = None, *,
           root: Path | None = None,
           scenario_root: Path | None = None,
           rebuild: bool = False,
           on_stage: Callable[[str], None] | None = None) -> Creation:
    """Build one dataset from a scenario slug and a seed.

    `on_stage` is called with each stage name as it begins, so a progress
    indicator can be honest about which of the five is running. It is the only
    thing this function does that is for the benefit of an interface.
    """
    def announce(stage: str) -> None:
        if on_stage is not None:
            on_stage(stage)

    announce("scenario selection")
    config = _load(slug, scenario_root)
    world = _world(config)
    directory = Path(root) if root is not None else dataset_root()
    identity = config.dataset_identity(seed, world_sha256=world.world_sha256)
    resolved_seed = identity.seed

    existing = inspect(directory / identity.dataset_id / "fab.db")
    if existing.status == READY and not rebuild:
        announce("registration")
        return Creation(record=existing, scenario=slug, seed=resolved_seed,
                        reused=True, validated=False)

    announce("generation")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle = build_observable(config, seed, world=world, root=directory)
    except SelfTestError as exc:
        raise GenerationError(
            "validation",
            f"the generator built {slug!r} at seed {resolved_seed} and its own "
            f"self-test rejected it: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise GenerationError(
            "generation",
            f"{slug!r} at seed {resolved_seed} could not be generated: "
            f"{exc}") from exc

    announce("registration")
    record = inspect(handle.db_path)
    if record.status != READY:
        raise GenerationError(
            "registration",
            f"the build finished but the dataset it produced is not usable "
            f"({record.status}: {record.detail})")
    return Creation(record=record, scenario=slug, seed=resolved_seed,
                    reused=False, validated=True)


def _load(slug: str, scenario_root: Path | None) -> Any:
    try:
        return config_for(slug, scenario_root)
    except FileNotFoundError as exc:
        raise GenerationError("scenario selection", str(exc)) from exc
    except ScenarioConfigError as exc:
        raise GenerationError(
            "scenario selection",
            f"the configuration for {slug!r} is not valid: {exc}") from exc


def _world(config: Any) -> Any:
    try:
        return load_world(config.world)
    except WorldTemplateError as exc:
        raise GenerationError(
            "scenario selection",
            f"the world {config.world!r} this scenario names could not be "
            f"loaded: {exc}") from exc
