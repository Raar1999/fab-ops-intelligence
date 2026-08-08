"""
db.py — thin data-access layer.

A real analytics codebase keeps SQL out of the UI. Every query goes through
`run_query`, which returns a pandas DataFrame. The dashboard and the
investigation both import from here, so connection handling lives in one place.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

# Repo-root-relative default path so the package works regardless of CWD.
# (src/fabops/db.py -> parents[2] is the repository root; the project is
# designed to run from a source checkout via `pip install -e .`.)
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "fab.db"


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open a connection. Raises a clear error if the DB hasn't been built yet."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"{db_path} not found. Build it first:\n"
            f"    python -m fabops.build_db   (or: fabops-build / make setup)"
        )
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def run_query(sql: str, db_path: str | Path = DEFAULT_DB,
              params: tuple | None = None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame."""
    con = connect(db_path)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


def run_view(view_name: str, db_path: str | Path = DEFAULT_DB,
             order_by: str | None = None) -> pd.DataFrame:
    """Convenience: SELECT * FROM a named view, optionally ordered."""
    sql = f"SELECT * FROM {view_name}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    return run_query(sql, db_path)
