"""
test_queries.py — guards the data layer and the investigation's core findings.

These tests assert two things:
  1. Structural integrity: the build produces the expected tables/views and they
     are all queryable (catches a broken view definition immediately).
  2. Analytical integrity: the planted root-cause story actually holds in the
     data — ETCH-02 is the worst etcher on every signal, the edge-ring defects
     are radially at the edge, and defect load anti-correlates with yield.

All thresholds are deliberately loose so the tests check *direction and shape*,
not brittle exact values.
"""
import sqlite3
from pathlib import Path

import pytest

from src.db import run_query, run_view

REPO_ROOT = Path(__file__).resolve().parents[1]
SUSPECT = "ETCH-02"

EXPECTED_VIEWS = {
    "v_yield_by_product", "v_yield_by_node", "v_etch_tool_yield", "v_defect_zone",
    "v_edge_ring_by_tool", "v_tool_downtime", "v_tool_rca", "v_weekly_yield",
    "v_scrap_by_lot", "v_shift_yield", "v_defect_pareto", "v_loss_decomposition",
}
EXPECTED_TABLES = {
    "products", "tools", "process_steps", "operators", "lots", "wafers",
    "run_history", "inspections", "defects", "yield_data", "maintenance",
    "fact_yield",
}


# ---------------------------------------------------------------- structural

def test_expected_tables_exist(con):
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = EXPECTED_TABLES - have
    assert not missing, f"missing tables: {missing}"


def test_expected_views_exist(con):
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='view'")}
    missing = EXPECTED_VIEWS - have
    assert not missing, f"missing views: {missing}"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_every_view_is_queryable(view):
    """A broken view definition raises here."""
    df = run_view(view)
    assert df is not None  # query executed
    assert len(df.columns) > 0


def test_fact_yield_row_count(con):
    n = con.execute("SELECT COUNT(*) FROM fact_yield").fetchone()[0]
    assert n == 223, f"expected 223 fact_yield rows, got {n}"


def test_fact_yield_reconciles_with_yield_data(con):
    a = con.execute("SELECT COUNT(*) FROM fact_yield").fetchone()[0]
    b = con.execute("SELECT COUNT(*) FROM yield_data").fetchone()[0]
    assert a == b


# ---------------------------------------------------------------- analytical

def test_suspect_has_lowest_etch_yield():
    df = run_view("v_etch_tool_yield", order_by="avg_yield")
    assert df.iloc[0]["tool_name"] == SUSPECT


def test_suspect_has_highest_edge_ring():
    df = run_view("v_edge_ring_by_tool", order_by="edge_ring_pct DESC")
    assert df.iloc[0]["tool_name"] == SUSPECT
    # and it should be markedly higher than the runner-up
    assert df.iloc[0]["edge_ring_pct"] > 1.5 * df.iloc[1]["edge_ring_pct"]


def test_suspect_has_most_unscheduled_downtime():
    df = run_query("SELECT tool_name, unscheduled_hrs FROM v_tool_downtime "
                   "WHERE tool_type='ETCH' ORDER BY unscheduled_hrs DESC")
    assert df.iloc[0]["tool_name"] == SUSPECT
    assert df.iloc[0]["unscheduled_hrs"] > 0


def test_rca_scorecard_converges_on_one_tool():
    """The worst-yield, highest-edge-ring, highest-downtime tool is the same."""
    rca = run_view("v_tool_rca")
    worst_yield = rca.loc[rca["avg_yield"].idxmin(), "tool_name"]
    worst_edge = rca.loc[rca["edge_ring_pct"].idxmax(), "tool_name"]
    worst_down = rca.loc[rca["unscheduled_hrs"].idxmax(), "tool_name"]
    assert worst_yield == worst_edge == worst_down == SUSPECT


def test_edge_ring_defects_are_radially_at_edge():
    """Edge-ring is a spatial signature: its mean radius must exceed center-type."""
    df = run_query("""
        SELECT defect_type, AVG(radius_mm) avg_r
        FROM v_defect_zone GROUP BY defect_type
    """).set_index("defect_type")["avg_r"]
    assert df["EDGE_RING"] > df["CENTER"]
    assert df["EDGE_RING"] > 110  # in the 'edge' zone by construction


def test_all_edge_ring_defects_in_edge_zone():
    df = run_query("""
        SELECT zone, COUNT(*) n FROM v_defect_zone
        WHERE defect_type='EDGE_RING' GROUP BY zone
    """).set_index("zone")["n"]
    # essentially all edge-ring defects classify into the 'edge' zone
    total = df.sum()
    assert df.get("edge", 0) / total > 0.95


def test_suspect_wafers_have_larger_mean_defect_radius():
    df = run_query("""
        WITH tagged AS (
          SELECT DISTINCT y.wafer_id,
                 MAX(CASE WHEN t.tool_name=? THEN 1 ELSE 0 END)
                     OVER (PARTITION BY y.wafer_id) AS on_suspect
          FROM yield_data y
          JOIN run_history rh ON rh.wafer_id=y.wafer_id AND rh.step_id=4
          JOIN tools t ON t.tool_id=rh.tool_id)
        SELECT tg.on_suspect, AVG(dz.radius_mm) avg_r
        FROM tagged tg JOIN v_defect_zone dz ON dz.wafer_id=tg.wafer_id
        GROUP BY tg.on_suspect
    """, params=(SUSPECT,)).set_index("on_suspect")["avg_r"]
    assert df[1] > df[0]  # suspect wafers' defects sit farther out


def test_defect_load_anticorrelates_with_yield():
    import numpy as np
    df = run_query("""
        SELECT y.yield_pct, COALESCE(d.n,0) AS defects
        FROM yield_data y
        LEFT JOIN (SELECT wafer_id, COUNT(*) n FROM defects GROUP BY wafer_id) d
               ON d.wafer_id=y.wafer_id
    """)
    r = np.corrcoef(df["defects"], df["yield_pct"])[0, 1]
    assert r < -0.2, f"expected clear negative correlation, got r={r:.2f}"


def test_all_products_miss_target():
    df = run_view("v_yield_by_product")
    assert (df["gap_to_target"] < 0).all()


def test_estimated_die_loss_is_positive():
    df = run_query("""
        WITH tagged AS (
          SELECT y.wafer_id, y.yield_pct, y.total_die,
                 MAX(CASE WHEN t.tool_name=? THEN 1 ELSE 0 END) AS on_suspect
          FROM yield_data y
          JOIN run_history rh ON rh.wafer_id=y.wafer_id AND rh.step_id=4
          JOIN tools t ON t.tool_id=rh.tool_id
          GROUP BY y.wafer_id, y.yield_pct, y.total_die),
        benchmark AS (SELECT AVG(yield_pct) good_yield FROM tagged WHERE on_suspect=0)
        SELECT SUM(CASE WHEN on_suspect=1
                   THEN total_die*((SELECT good_yield FROM benchmark)-yield_pct)/100.0
                   ELSE 0 END) AS lost
        FROM tagged
    """, params=(SUSPECT,))
    assert df.iloc[0]["lost"] > 0
