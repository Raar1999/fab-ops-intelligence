"""Fixtures for the diagnosis tests.

Two populations are built once per session and shared:

* `library` — one dataset per library scenario at the published seed, which is
  what the behavioural tests read;
* `null_population` — enough fault-free worlds to *measure* the permutation
  null rather than assert it. `DIAGNOSIS_CONTRACT.md` §5.1 asks for at least
  thirty, so thirty is what is built; they are built in a process pool because
  a check the suite cannot afford to run is a check that stops being run.

The datasets go to a temporary directory and are never written into the
repository. Nothing here is imported by `fabops.diagnosis`: the engine is
handed a database path and the fixtures are the only thing that knows a
scenario exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fabops.datasets import build_all

REPO = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPO / "scenarios"

#: The fault-free worlds the null-validity criterion is measured on. The
#: contract's floor is thirty; sixty is what the guard needs in order to be
#: able to *fail*. At thirty, a realized rate of 0.20 - which two earlier
#: designs of this engine actually produced - is still an ordinary draw under
#: the declared tolerance, so a thirty-world check would have passed both.
NULL_SEEDS = tuple(range(900001, 900061))

#: The library, at its published default seed.
LIBRARY = ("null_baseline", "chamber_edge_uniformity", "parameter_drift",
           "confounded_chamber_vs_product", "fault_repair_recovery")
DEFAULT_SEED = 42


@pytest.fixture(scope="session")
def library(tmp_path_factory):
    """One dataset per library scenario, keyed by slug."""
    root = tmp_path_factory.mktemp("diagnosis-library")
    built = build_all([(name, DEFAULT_SEED, str(root))
                       for name in LIBRARY])
    return {record["scenario"]: record for record in built}


@pytest.fixture(scope="session")
def null_population(tmp_path_factory):
    """Fault-free worlds, enough of them to measure a false-positive rate."""
    root = tmp_path_factory.mktemp("diagnosis-nulls")
    return build_all([("null_baseline", seed, str(root))
                      for seed in NULL_SEEDS])


@pytest.fixture(scope="session")
def null_dataset(library):
    return library["null_baseline"]


@pytest.fixture(scope="session")
def demo_dataset(library):
    return library["chamber_edge_uniformity"]


def truth_of(record) -> dict:
    """Evaluation-side only. Never handed to the engine."""
    return json.loads(Path(record["truth_path"]).read_text(encoding="utf-8"))


def planted_of(record) -> str | None:
    truth = truth_of(record)
    if not truth["events"]:
        return None
    target = truth["events"][0]["target"]
    return f"{target['tool']}/{target['chamber']}"
