"""Dataset builders for the diagnosis fixtures.

A module rather than a conftest function, because the fault-free population is
built in a process pool and a worker can only unpickle a callable it can import
by name. `conftest.py` is not importable by name from a worker.

Nothing here is imported by `fabops.diagnosis`: the engine is handed a database
path, and this is the only thing in the test tree that knows a scenario exists.
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPO / "scenarios"


def build_one(job) -> dict:
    """Build one dataset and return only its paths."""
    scenario, seed, root = job
    from fabsim.emit import build_dataset
    from fabsim.scenario import load_scenario
    from fabsim.world import load_world

    dataset = build_dataset(
        load_scenario(SCENARIO_ROOT / f"{scenario}.json"), seed,
        world=load_world("baseline_fab_v1"),
        root=Path(root) / f"{scenario}-{seed}", created_at="diagnosis-tests")
    return {"scenario": scenario, "seed": seed,
            "db_path": str(dataset.db_path),
            "truth_path": str(dataset.truth_path),
            "dataset_id": dataset.dataset_id}


def build_all(jobs) -> list[dict]:
    """Build in parallel where the machine allows it, sequentially otherwise."""
    jobs = list(jobs)
    workers = min(len(jobs), max(1, (os.cpu_count() or 2) - 1), 12)
    if workers <= 1:
        return [build_one(job) for job in jobs]
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(build_one, jobs))
    except Exception:                                   # pragma: no cover
        # A machine that cannot spawn workers still has to be able to run the
        # suite; slower is acceptable, skipped is not.
        return [build_one(job) for job in jobs]
