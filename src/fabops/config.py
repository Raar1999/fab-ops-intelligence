"""
config.py — single source for paths, analysis anchors, and presentation
constants that the audit found duplicated across modules.

Centralizing (not redesigning): every value here is exactly the value the
baseline used; only the definition site changes.
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------- repository
# The project runs from a source checkout (editable install); generated
# artifacts live inside the repo tree. src/fabops/config.py -> parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SQL_DIR = REPO_ROOT / "sql"
DB_PATH = DATA_DIR / "fab.db"
FIGURES_DIR = REPO_ROOT / "reports" / "figures"

# ------------------------------------------------------------ wafer geometry
WAFER_RADIUS_MM = 150.0  # 300 mm wafer

# Radial zone cut-offs. These MUST stay in sync with the CASE expression in
# v_defect_zone (sql/views.sql); they are data-calibrated: EDGE_RING defects
# sit at ~143 mm mean radius, CENTER at ~18 mm, so 50/110 mm separates the
# classes cleanly. Used by the tests that pin the spatial findings.
CENTER_ZONE_MAX_RADIUS_MM = 50.0
EDGE_ZONE_MIN_RADIUS_MM = 110.0

# --------------------------------------------------------- demonstration RCA
# ETCH-02 is the KNOWN, PLANTED root cause of the demo dataset (seed=42).
# The legacy pipeline NARRATES and VERIFIES this conclusion — it does not
# discover it. This constant exists so the demonstration is honest about being
# a demonstration, and so the conclusion lives in exactly one place.
#
# The engine that discovers a candidate instead of being told one now exists:
# `fabops.diagnosis.diagnose(db_path)` (ADR-029), and it may not name an
# entity — a test asserts that this very constant would trip its rule 3. What
# keeps the constant here is ADR-010 rather than ADR-003: the legacy demo owns
# the narrative surface until something is strictly better on it, and the
# engine reads schema v2 datasets while this narrates the schema v1 database.
DEMO_SUSPECT_TOOL = "ETCH-02"

# ---------------------------------------------------------------- chart theme
ALERT = "#d1495b"   # suspect / bad
OK = "#3b7a9e"      # everything else
ACCENT = "#edae49"  # secondary

# defect-type colour map for wafer maps
TYPE_COLORS = {
    "EDGE_RING": "#d1495b",
    "CENTER": "#00798c",
    "SCRATCH": "#edae49",
    "PARTICLE": "#66a182",
    "RANDOM": "#8d99ae",
}
