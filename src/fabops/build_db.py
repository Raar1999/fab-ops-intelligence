"""
build_db.py — one command to stand up the database.

Steps:
  1. Generate data/fab.db from the deterministic generator (seed=42).
  2. Apply the star model (fact_yield).
  3. Apply the analytical view layer.

Re-running reapplies the star model and views; delete data/fab.db first to
force full regeneration. Run with `python -m fabops.build_db` (or fabops-build).
"""
from __future__ import annotations

import runpy
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SQL_DIR = REPO_ROOT / "sql"
DB_PATH = DATA_DIR / "fab.db"


def generate_database() -> None:
    """Run the generator script, which writes data/fab.db and fab_database.sql."""
    gen = DATA_DIR / "generate_fab_db.py"
    if not gen.exists():
        sys.exit(f"Generator not found at {gen}")
    print(f"[1/3] Generating {DB_PATH} (seed=42) ...")
    # The generator writes its outputs into its own directory (cwd-independent
    # in our copy), so run it from DATA_DIR to keep paths predictable.
    cwd = Path.cwd()
    try:
        import os
        os.chdir(DATA_DIR)
        runpy.run_path(str(gen), run_name="__main__")
    finally:
        import os
        os.chdir(cwd)


def apply_sql_file(con: sqlite3.Connection, path: Path) -> None:
    print(f"      applying {path.relative_to(REPO_ROOT)}")
    con.executescript(path.read_text())


def main() -> None:
    if not DB_PATH.exists():
        generate_database()
    else:
        print(f"[1/3] {DB_PATH} already present — reusing (delete it to regenerate).")

    con = sqlite3.connect(str(DB_PATH))
    try:
        print("[2/3] Building star model ...")
        apply_sql_file(con, SQL_DIR / "star_model.sql")
        print("[3/3] Building analytical views ...")
        apply_sql_file(con, SQL_DIR / "views.sql")
        con.commit()

        # quick confirmation
        n_views = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view'"
        ).fetchone()[0]
        n_fact = con.execute("SELECT COUNT(*) FROM fact_yield").fetchone()[0]
        print(f"\nDone. {n_views} views created; fact_yield has {n_fact} rows.")
        print(f"Database ready at {DB_PATH}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
