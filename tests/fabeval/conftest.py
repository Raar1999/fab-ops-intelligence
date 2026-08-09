"""Fixtures for the evaluation tests.

`world` is duplicated from `tests/fabsim/conftest.py` rather than shared,
because pytest conftest files do not inherit sideways and the alternative — a
root-level conftest carrying a FabSim fixture — would put simulator setup in
front of the 27 legacy tests that must stay independent of it.
"""
from __future__ import annotations

import pytest

from fabsim.world import World, load_world

BASELINE_WORLD = "baseline_fab_v1"


@pytest.fixture(scope="session")
def world() -> World:
    return load_world(BASELINE_WORLD)
