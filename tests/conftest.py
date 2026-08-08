"""Shared pytest fixtures. Builds the database once if it isn't present yet."""
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "fab.db"


@pytest.fixture(scope="session", autouse=True)
def ensure_db():
    """Build the DB + views once for the whole test session if missing."""
    if not DB_PATH.exists() or _view_count() == 0:
        from src.build_db import main as build
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
