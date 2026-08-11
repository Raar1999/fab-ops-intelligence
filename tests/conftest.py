"""Shared pytest fixtures. Builds the database once if it isn't present yet.

It also records, before a single test runs, what the generated-dataset root
already contained. The suite must build into `tmp_path` and never into the
repository, and the way that used to be checked was to assert the root was
*empty* — which is a property of whoever's machine the suite is running on
rather than of the suite. Anybody who has created a dataset, which after the
productization gate is the product's ordinary first action, made six tests
fail. The snapshot below turns the intent into something measurable: not "the
root is empty", but "the suite added nothing to it".
"""
import sqlite3
from pathlib import Path

import pytest

from fabops.config import DB_PATH

#: Where generated datasets live (`fabsim.emit.DATASET_ROOT`), named here
#: without importing the simulator — `tests/conftest.py` is loaded for every
#: test in the repository, including the ones that scan for that import.
DATASET_ROOT = Path(__file__).resolve().parents[1] / "data" / "scenarios"

#: What it held before the session started. Filled by `pytest_sessionstart`,
#: which runs before collection, so no test can have written to it first.
DATASETS_AT_SESSION_START: frozenset[str] = frozenset()


def pytest_sessionstart(session):                      # noqa: ARG001
    global DATASETS_AT_SESSION_START
    if DATASET_ROOT.is_dir():
        DATASETS_AT_SESSION_START = frozenset(
            path.name for path in DATASET_ROOT.iterdir())


def datasets_added_during_the_session() -> list[str]:
    """Anything the suite itself dropped into the repository's dataset root."""
    if not DATASET_ROOT.is_dir():
        return []
    return sorted(path.name for path in DATASET_ROOT.iterdir()
                  if path.name not in DATASETS_AT_SESSION_START)


@pytest.fixture(scope="session", autouse=True)
def ensure_db():
    """Build the DB + views once for the whole test session if missing."""
    if not DB_PATH.exists() or _view_count() == 0:
        from fabops.build_db import main as build
        build()
    yield


def _view_count() -> int:
    if not DB_PATH.exists():
        return 0
    con = sqlite3.connect(str(DB_PATH))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view'"
        ).fetchone()[0]
    finally:
        con.close()


@pytest.fixture(scope="session")
def con():
    c = sqlite3.connect(str(DB_PATH))
    yield c
    c.close()
