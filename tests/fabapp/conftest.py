"""Fixtures for the product plane.

One faulted dataset and one fault-free one, built **through the product's own
generation path** rather than by calling the emitter directly. That is
deliberate: `fabapp.generate.create` is the thing under test on half these
modules, and a fixture that bypassed it would leave the product's own five
stages unexercised by everything downstream.

Both land in a temporary root, so nothing is written into the repository, and
the root is what `FABOPS_DATASET_ROOT` points at for the tests that discover.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPO / "scenarios"

#: The two members every product test needs: one with something in it, and the
#: fault-free control the engine must stay quiet on.
FAULTED = "chamber_edge_uniformity"
NULL = "null_baseline"
SEED = 42


@pytest.fixture(scope="session")
def product_root(tmp_path_factory) -> Path:
    """A dataset root of the product's own shape: `<root>/<dataset_id>/`.

    **It holds exactly the two datasets below, and a test that builds a third
    must use its own root.** Discovery is counted exactly rather than loosely —
    "at least two" would stop noticing a dataset the registry invented or
    dropped — so a test that quietly adds one to this root breaks two later
    files instead of its own. That is not hypothetical: it is how this comment
    came to be written.
    """
    return tmp_path_factory.mktemp("fabapp-datasets")


@pytest.fixture(scope="session")
def faulted(product_root):
    from fabapp.generate import create

    return create(FAULTED, SEED, root=product_root,
                  scenario_root=SCENARIO_ROOT)


@pytest.fixture(scope="session")
def null(product_root):
    from fabapp.generate import create

    return create(NULL, SEED, root=product_root, scenario_root=SCENARIO_ROOT)


@pytest.fixture(scope="session")
def populated_root(product_root, faulted, null) -> Path:
    """The root with both datasets in it, for discovery."""
    return product_root


@pytest.fixture()
def product_env(monkeypatch, populated_root):
    """Point the product's configuration at the temporary root.

    Function-scoped and explicit, so a test that wants the *repository's* root
    simply does not ask for this fixture.
    """
    monkeypatch.setenv("FABOPS_DATASET_ROOT", str(populated_root))
    monkeypatch.setenv("FABOPS_SCENARIO_ROOT", str(SCENARIO_ROOT))
    return populated_root
