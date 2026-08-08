"""
investigation.py — the end-to-end root-cause investigation.

Run this and it walks the full arc out loud:
    symptom -> suspect -> confirm (defect signature, spatial, downtime)
    -> converge -> size the impact -> exposure -> recommendation
then renders every chart into reports/figures/.

This is the script form of notebooks/investigation.ipynb. Run with:
    python -m fabops.investigation   (or: fabops-investigate)
"""
from __future__ import annotations

from fabops import charts
from fabops.config import DEMO_SUSPECT_TOOL as SUSPECT, REPO_ROOT
from fabops.db import run_query, run_view


def hr(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def fmt(df, **kw):
    print(df.to_string(index=False, **kw))


def step1_symptom():
    hr("STEP 1 — SYMPTOM: products are missing their yield targets")
    df = run_view("v_yield_by_product", order_by="gap_to_target")
    fmt(df)
    worst = df.iloc[0]
    print(f"\n  Read: all six products sit ~8–10 pts below target. The deficit is "
          f"\n  remarkably uniform (worst: {worst['product_name']} at {worst['gap_to_target']} pts),"
          f"\n  which argues against a product-specific cause and points to shared"
          f"\n  infrastructure touched by every lot.")


def step2_suspect():
    hr("STEP 2 — SUSPECT: split gate-etch yield by the tool each wafer saw")
    df = run_view("v_etch_tool_yield", order_by="avg_yield")
    fmt(df)
    lo, hi = df["avg_yield"].iloc[0], df["avg_yield"].iloc[-1]
    print(f"\n  Read: {df['tool_name'].iloc[0]} yields {lo:.1f}% vs {hi:.1f}% for the best"
          f"\n  etcher — a {hi - lo:.1f}-pt gap on the same process step. Prime suspect.")


def step3_defect_signature():
    hr("STEP 3 — CONFIRMATION #1: defect signature (edge-ring fraction by tool)")
    df = run_view("v_edge_ring_by_tool", order_by="edge_ring_pct DESC")
    fmt(df)
    print(f"\n  Read: {SUSPECT}'s defects are ~3x more edge-ring than the other etchers."
          f"\n  Edge-ring is the classic signature of an etch chamber problem"
          f"\n  (non-uniformity / clamp / edge-plasma), so the defect physics agrees.")


def step4_spatial():
    hr("STEP 4 — CONFIRMATION #1b: spatial — edge-ring is a RADIAL signature")
    df = run_query("""
        WITH tagged AS (
          SELECT DISTINCT y.wafer_id,
                 MAX(CASE WHEN g.tool_name=? THEN 1 ELSE 0 END)
                     OVER (PARTITION BY y.wafer_id) AS on_suspect
          FROM yield_data y
          JOIN v_gate_etch_runs g ON g.wafer_id=y.wafer_id)
        SELECT CASE tg.on_suspect WHEN 1 THEN ? ELSE 'other etchers' END AS tool_group,
               COUNT(*) AS defects,
               ROUND(AVG(dz.radius_mm),1) AS avg_radius_mm,
               ROUND(100.0*SUM(CASE WHEN dz.zone='edge' THEN 1 ELSE 0 END)/COUNT(*),1) AS edge_zone_pct
        FROM tagged tg JOIN v_defect_zone dz ON dz.wafer_id=tg.wafer_id
        GROUP BY tg.on_suspect ORDER BY avg_radius_mm DESC
    """, params=(SUSPECT, SUSPECT))
    fmt(df)
    print(f"\n  Read: {SUSPECT} wafers carry defects at a larger mean radius and a higher"
          f"\n  edge-zone share. The defects sit at the wafer EDGE, exactly where an"
          f"\n  etch-uniformity fault deposits them. See reports/figures/04_wafer_maps.png.")


def step5_downtime():
    hr("STEP 5 — CONFIRMATION #2: independent root signal (unscheduled downtime)")
    df = run_query("SELECT tool_name, unscheduled_hrs, unscheduled_events, pm_hrs "
                   "FROM v_tool_downtime WHERE tool_type='ETCH' ORDER BY unscheduled_hrs DESC")
    fmt(df)
    print(f"\n  Read: {SUSPECT} carries all the unscheduled etch downtime; the other etchers"
          f"\n  have zero. A chamber drifting out of spec also breaks — two independent"
          f"\n  data sources (defects and maintenance) name the same tool.")


def step6_converge():
    hr("STEP 6 — CONVERGENCE: one scorecard, three signals")
    df = run_view("v_tool_rca", order_by="edge_ring_pct DESC")
    fmt(df)
    print("\n  Read: ETCH-02 is simultaneously worst on yield, highest on edge-ring %,"
          "\n  and highest on unscheduled downtime. That convergence is the finding.")


def step7_size_impact():
    hr("STEP 7 — SIZE THE IMPACT: yield gap and estimated good-die lost")
    df = run_query("""
        WITH tagged AS (
          SELECT y.wafer_id, y.yield_pct, y.total_die,
                 MAX(CASE WHEN g.tool_name=? THEN 1 ELSE 0 END) AS on_suspect
          FROM yield_data y
          JOIN v_gate_etch_runs g ON g.wafer_id=y.wafer_id
          GROUP BY y.wafer_id, y.yield_pct, y.total_die),
        benchmark AS (SELECT AVG(yield_pct) AS good_yield FROM tagged WHERE on_suspect=0)
        SELECT
          SUM(CASE WHEN on_suspect=1 THEN 1 ELSE 0 END) AS suspect_wafers,
          ROUND(AVG(CASE WHEN on_suspect=1 THEN yield_pct END),2) AS suspect_avg_yield,
          ROUND((SELECT good_yield FROM benchmark),2) AS good_etcher_yield,
          ROUND(SUM(CASE WHEN on_suspect=1
                THEN total_die*((SELECT good_yield FROM benchmark)-yield_pct)/100.0
                ELSE 0 END)) AS est_good_die_lost
        FROM tagged
    """, params=(SUSPECT,))
    fmt(df)
    row = df.iloc[0]
    print(f"\n  Read: ~{int(row['suspect_wafers'])} wafers ran through {SUSPECT}. Had they matched"
          f"\n  the good etchers ({row['good_etcher_yield']}%), an estimated "
          f"{int(row['est_good_die_lost']):,} additional"
          f"\n  good die would have been recovered (synthetic-data estimate).")


def step8_exposure():
    hr("STEP 8 — EXPOSURE: which lots depend most on the suspect (containment)")
    df = run_query("""
        WITH per_lot AS (
          SELECT l.lot_number,
                 COUNT(DISTINCT g.wafer_id) AS gate_etch_wafers,
                 COUNT(DISTINCT CASE WHEN g.tool_name=? THEN g.wafer_id END) AS on_suspect
          FROM v_gate_etch_runs g
          JOIN wafers w ON w.wafer_id=g.wafer_id
          JOIN lots l ON l.lot_id=w.lot_id
          WHERE g.tool_type='ETCH'
          GROUP BY l.lot_number)
        SELECT lot_number, on_suspect, gate_etch_wafers,
               ROUND(100.0*on_suspect/gate_etch_wafers,1) AS pct_on_suspect
        FROM per_lot ORDER BY pct_on_suspect DESC LIMIT 8
    """, params=(SUSPECT,))
    fmt(df)


def recommendation():
    hr("RECOMMENDATION")
    print(f"""  1. Take {SUSPECT} offline for chamber inspection — the edge-ring signature plus
     unscheduled-downtime cluster indicates an edge-uniformity / clamp fault.
  2. Contain: prioritise the high-exposure lots above for re-inspection; reroute
     in-flight gate-etch to ETCH-01 / ETCH-03 until {SUSPECT} is qualified.
  3. Add an SPC rule on gate-etch edge-ring fraction per chamber so the next
     drift is caught by an alarm, not a yield post-mortem.

  Method demonstrated: symptom -> suspect -> two independent confirmations
  (defect physics + maintenance) -> quantified loss -> exposure -> action.""")


def main():
    print("FAB OPERATIONS ANALYTICS — root-cause investigation")
    print("Synthetic 300-wafer dataset (seed=42). No number here is a real benchmark.")
    print("Demonstration RCA: narrates the KNOWN planted root cause "
          "(fabops.config.DEMO_SUSPECT_TOOL; see docs/audit/RCA_AUDIT.md).")
    step1_symptom()
    step2_suspect()
    step3_defect_signature()
    step4_spatial()
    step5_downtime()
    step6_converge()
    step7_size_impact()
    step8_exposure()
    recommendation()

    hr("RENDERING FIGURES")
    for p in charts.build_all():
        print("  wrote", p.relative_to(REPO_ROOT))
    print("\nInvestigation complete.")


if __name__ == "__main__":
    main()
