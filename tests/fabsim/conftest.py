"""Shared fixtures for the FabSim world/timeline tests.

The baseline world and one realization of it are session-scoped: building a
world is pure, simulating 84 days takes a fraction of a second, and every test
that only *inspects* a realization should look at the same one — a test that
disagrees with another about what happened is then a real disagreement, not two
different random worlds.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from fabsim.latent import Realization, realize
from fabsim.timeline import Timeline, simulate
from fabsim.world import World, build_world, load_world, load_world_template

#: The scenario shape of the Phase 1 library: 20 lots over 12 weeks
#: (`SCENARIO_SPECIFICATION.md` §4).
BASELINE_WORLD = "baseline_fab_v1"
BASELINE_LOTS = 20
BASELINE_HORIZON_DAYS = 84
BASELINE_SEED = 42


@pytest.fixture(scope="session")
def template() -> dict[str, Any]:
    """The parsed baseline template, straight off disk."""
    return load_world_template(BASELINE_WORLD)


@pytest.fixture()
def make_template(template: dict[str, Any]):
    """Return a fresh, mutable copy of the baseline template each call."""
    def factory(**overrides: Any) -> dict[str, Any]:
        raw = copy.deepcopy(template)
        raw.update(overrides)
        return raw
    return factory


@pytest.fixture()
def make_world(make_template):
    """Build a world from the baseline template with top-level overrides."""
    def factory(**overrides: Any) -> World:
        return build_world(make_template(**overrides))
    return factory


@pytest.fixture(scope="session")
def world() -> World:
    return load_world(BASELINE_WORLD)


@pytest.fixture(scope="session")
def timeline(world: World) -> Timeline:
    """One realization of the baseline world — the reference realization."""
    return simulate(world, lots=BASELINE_LOTS,
                    horizon_days=BASELINE_HORIZON_DAYS, seed=BASELINE_SEED)


@pytest.fixture(scope="session")
def realization(timeline: Timeline) -> Realization:
    """The **null** latent plane of the reference realization.

    No events and no distractors: the baseline every fault scenario is a
    departure from, and the world requirement F10 is asserted against.
    """
    return realize(timeline)
