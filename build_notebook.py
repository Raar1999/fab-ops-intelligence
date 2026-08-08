"""
build_notebook.py — assemble notebooks/investigation.ipynb programmatically.

We generate the notebook from code (rather than hand-editing JSON) so it stays
in sync with the verified src/ logic, then execute it with nbconvert so the
shipped .ipynb carries real tables and inline charts.
"""
from pathlib import Path
import nbformat as nbf

REPO_ROOT = Path(__file__).resolve().parent
OUT = REPO_ROOT / "notebooks" / "investigation.ipynb"

nb = nbf.v4.new_notebook()
cells = []


def md(text):
    cells.append(nbf.v4.new_markdown_cell(text.strip("\n")))


def code(text):
    cells.append(nbf.v4.new_code_cell(text.strip("\n")))


md(r"""
# Fab Operations Analytics — Root-Cause Investigation

**A yield excursion, traced end to end in SQL.**

This notebook walks the standard investigation arc a yield / process-integration
engineer runs during an excursion:

> **symptom → suspect → confirm (independent signals) → size the impact → exposure → recommendation**

The data is a **synthetic 300-wafer fab dataset** (`seed=42`). It is built to contain
*one* discoverable root cause; nothing here is a real-world benchmark. The point of the
project is the **method and the SQL**, not the numbers.
""")

code(r"""
import matplotlib.pyplot as plt
from IPython.display import Image, display

# Requires the package install: pip install -e ".[notebook]"
from fabops.db import run_query, run_view
from fabops import charts

SUSPECT = "ETCH-02"
print("data layer ready")
""")

md(r"""
## Step 1 — Symptom

Start where the business does: **products are missing their yield targets.** The
`v_yield_by_product` view compares actual vs target yield per product.
""")
code(r"""
run_view("v_yield_by_product", order_by="gap_to_target")
""")
md(r"""
Every product sits ~8–10 points below target, and the deficit is strikingly **uniform**.
A product-specific cause (design, a single layer) would hit some products far harder than
others. Uniform loss across the catalogue points instead to **shared infrastructure** — a
tool every lot passes through.
""")
code(r"""
display(Image(charts.chart_product_gap()))
""")

md(r"""
## Step 2 — Suspect

Gate etch (step 4) is a shared, yield-critical step. Split gate-etch yield by the **tool**
each wafer was processed on.
""")
code(r"""
run_view("v_etch_tool_yield", order_by="avg_yield")
""")
code(r"""
display(Image(charts.chart_tool_yield()))
""")
md(r"""
**ETCH-02 yields ~12 points below the best etcher on the same step.** That is our prime
suspect. But a yield gap alone isn't proof — we need independent signals that agree.
""")

md(r"""
## Step 3 — Confirmation #1: defect signature

If ETCH-02 is genuinely faulty, its wafers should carry a **distinctive defect type**.
Edge-ring defects are the classic fingerprint of an etch-chamber problem (edge non-uniformity,
clamp/temperature issues, edge-plasma effects).
""")
code(r"""
run_view("v_edge_ring_by_tool", order_by="edge_ring_pct DESC")
""")
md(r"""
ETCH-02's defects are roughly **3× more edge-ring** than the other etchers'. The defect
*physics* agrees with the yield signal.

### Confirmation #1b: it's a *spatial* (radial) signature

Edge-ring isn't just a label — those defects should sit physically at the **wafer edge**.
The `v_defect_zone` view classifies every defect by radius (center / mid / edge). Compare the
mean defect radius and edge-zone share for suspect vs. other wafers.
""")
code(r"""
run_query('''
    WITH tagged AS (
      SELECT DISTINCT y.wafer_id,
             MAX(CASE WHEN t.tool_name=? THEN 1 ELSE 0 END)
                 OVER (PARTITION BY y.wafer_id) AS on_suspect
      FROM yield_data y
      JOIN run_history rh ON rh.wafer_id=y.wafer_id AND rh.step_id=4
      JOIN tools t ON t.tool_id=rh.tool_id)
    SELECT CASE tg.on_suspect WHEN 1 THEN ? ELSE 'other etchers' END AS tool_group,
           COUNT(*) AS defects,
           ROUND(AVG(dz.radius_mm),1) AS avg_radius_mm,
           ROUND(100.0*SUM(CASE WHEN dz.zone='edge' THEN 1 ELSE 0 END)/COUNT(*),1) AS edge_zone_pct
    FROM tagged tg JOIN v_defect_zone dz ON dz.wafer_id=tg.wafer_id
    GROUP BY tg.on_suspect ORDER BY avg_radius_mm DESC
''', params=(SUSPECT, SUSPECT))
""")
md(r"""
The wafer maps make it unmistakable — a dense red edge ring on ETCH-02 wafers, only baseline
scatter elsewhere:
""")
code(r"""
display(Image(charts.chart_wafer_maps()))
""")

md(r"""
## Step 4 — Confirmation #2: an independent root signal

Defects and yield both come from the *measurement* side. For a second, **independent**
line of evidence, look at the **maintenance** log: a chamber drifting out of spec also breaks
down more often.
""")
code(r"""
run_query('''
    SELECT tool_name, unscheduled_hrs, unscheduled_events, pm_hrs
    FROM v_tool_downtime WHERE tool_type='ETCH'
    ORDER BY unscheduled_hrs DESC
''')
""")
code(r"""
display(Image(charts.chart_downtime_timeline()))
""")
md(r"""
ETCH-02 carries **all** of the unscheduled etch downtime; the other etchers have none. Two
independent data sources — defect inspection and equipment maintenance — name the **same tool**.
""")

md(r"""
## Step 5 — Convergence

One scorecard, three signals. The finding is the **convergence**: the same tool is worst on
yield, highest on edge-ring %, and highest on unscheduled downtime.
""")
code(r"""
run_view("v_tool_rca", order_by="edge_ring_pct DESC")
""")
code(r"""
display(Image(charts.chart_rca_scorecard()))
""")

md(r"""
## Step 6 — Size the impact

Quantify the cost: average yield on the suspect vs. the good etchers, and the estimated extra
good die we would have recovered had the suspect's wafers matched the others. *(Synthetic-data
estimate — illustrative of the method, not a real figure.)*
""")
code(r"""
run_query('''
    WITH tagged AS (
      SELECT y.wafer_id, y.yield_pct, y.total_die,
             MAX(CASE WHEN t.tool_name=? THEN 1 ELSE 0 END) AS on_suspect
      FROM yield_data y
      JOIN run_history rh ON rh.wafer_id=y.wafer_id AND rh.step_id=4
      JOIN tools t ON t.tool_id=rh.tool_id
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
''', params=(SUSPECT,))
""")

md(r"""
## Step 7 — Exposure

For containment, find the lots most dependent on the suspect chamber so re-inspection can be
prioritised.
""")
code(r"""
run_query('''
    WITH per_lot AS (
      SELECT l.lot_number,
             COUNT(DISTINCT rh.wafer_id) AS gate_etch_wafers,
             COUNT(DISTINCT CASE WHEN t.tool_name=? THEN rh.wafer_id END) AS on_suspect
      FROM run_history rh
      JOIN tools t ON t.tool_id=rh.tool_id AND t.tool_type='ETCH'
      JOIN wafers w ON w.wafer_id=rh.wafer_id
      JOIN lots l ON l.lot_id=w.lot_id
      WHERE rh.step_id=4 GROUP BY l.lot_number)
    SELECT lot_number, on_suspect, gate_etch_wafers,
           ROUND(100.0*on_suspect/gate_etch_wafers,1) AS pct_on_suspect
    FROM per_lot ORDER BY pct_on_suspect DESC
''', params=(SUSPECT,))
""")

md(r"""
## Recommendation

1. **Take ETCH-02 offline for chamber inspection.** The edge-ring signature plus the
   unscheduled-downtime cluster points to an edge-uniformity / clamp fault.
2. **Contain.** Re-inspect the high-exposure lots above; reroute in-flight gate-etch to
   ETCH-01 / ETCH-03 until ETCH-02 is re-qualified.
3. **Prevent.** Add an SPC rule on per-chamber gate-etch edge-ring fraction so the next drift
   trips an alarm instead of surfacing in a yield post-mortem.

**Method demonstrated:** symptom isolation → suspect identification → two *independent*
confirmations (defect physics + maintenance) → quantified loss → exposure sizing → action.
That arc — not any single query — is what a yield/process-data interview is really testing.

---
*Synthetic data, `seed=42`. Reproduce everything with `python -m fabops.investigation`.*
""")

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
}
OUT.parent.mkdir(parents=True, exist_ok=True)
nbf.write(nb, str(OUT))
print(f"wrote {OUT.relative_to(REPO_ROOT)} ({len(cells)} cells)")
