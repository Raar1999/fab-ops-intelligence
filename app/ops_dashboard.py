"""
ops_dashboard.py — Fab Operations Analytics dashboard.

Run with:
    streamlit run app/ops_dashboard.py

Four tabs:
  • Overview      — fab KPI tiles + weekly yield trend
  • Yield         — product target attainment, node breakdown, loss decomposition
  • Tool RCA      — the three-signal scorecard + per-tool drill-down
  • Defect Maps   — wafer-map explorer (the spatial signature showpiece)

All data is synthetic (seed=42); nothing here is a real-world benchmark.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

# Requires the package to be installed (pip install -e ".[app]"); no path hacks.
from fabops.config import (
    ACCENT,
    ALERT,
    OK,
    TYPE_COLORS,
    WAFER_RADIUS_MM,
    DEMO_SUSPECT_TOOL as SUSPECT,
)
from fabops.db import run_query, run_view

st.set_page_config(page_title="Fab Operations Analytics", layout="wide")


@st.cache_data
def q(sql, params=None):
    return run_query(sql, params=params)


@st.cache_data
def v(view, order_by=None):
    return run_view(view, order_by=order_by)


# --------------------------------------------------------------------- header
st.title("Fab Operations Analytics")
st.caption("Synthetic 300-wafer fab dataset (seed=42). Yield · defect · tool · "
           "maintenance intelligence, ending in a root-cause investigation. "
           "No metric here is a real-world benchmark.")

tab_overview, tab_yield, tab_rca, tab_maps = st.tabs(
    ["Overview", "Yield", "Tool RCA", "Defect Maps"]
)

# ------------------------------------------------------------------- overview
with tab_overview:
    kpis = q("""
        SELECT (SELECT COUNT(*) FROM wafers) AS wafers,
               (SELECT COUNT(*) FROM lots) AS lots,
               (SELECT ROUND(AVG(yield_pct),2) FROM yield_data) AS avg_yield,
               (SELECT ROUND(100.0*SUM(CASE WHEN status='SCRAP' THEN 1 ELSE 0 END)/COUNT(*),1)
                  FROM wafers) AS scrap_pct,
               (SELECT COUNT(*) FROM defects WHERE killer_flag=1) AS killer_defects,
               (SELECT ROUND(SUM(duration_hours),1) FROM maintenance
                  WHERE maint_type='UNSCHEDULED') AS unsched_hrs
    """).iloc[0]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Wafers", int(kpis["wafers"]))
    c2.metric("Lots", int(kpis["lots"]))
    c3.metric("Avg yield", f"{kpis['avg_yield']}%")
    c4.metric("Scrap rate", f"{kpis['scrap_pct']}%")
    c5.metric("Killer defects", int(kpis["killer_defects"]))
    c6.metric("Unsched. downtime", f"{kpis['unsched_hrs']} h")

    st.subheader("Weekly yield trend")
    wk = v("v_weekly_yield", order_by="week")
    if not wk.empty:
        fig, ax = plt.subplots(figsize=(10, 3.5))
        ax.plot(wk["week"], wk["avg_yield"], marker="o", color=OK, label="avg")
        ax.fill_between(wk["week"], wk["worst"], wk["best"], color=OK, alpha=0.12,
                        label="min–max range")
        ax.set_ylabel("Yield (%)")
        ax.set_xticks(range(len(wk)))
        ax.set_xticklabels(wk["week"], rotation=45, ha="right", fontsize=7)
        ax.legend()
        ax.grid(alpha=0.25)
        st.pyplot(fig)

# ---------------------------------------------------------------------- yield
with tab_yield:
    st.subheader("Target attainment by product")
    yp = v("v_yield_by_product", order_by="gap_to_target")
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(yp)); w = 0.38
    ax.bar(x - w/2, yp["actual_yield"], w, label="Actual", color=OK)
    ax.bar(x + w/2, yp["target_yield_pct"], w, label="Target", color=ACCENT)
    ax.set_xticks(x); ax.set_xticklabels(yp["product_name"], rotation=20, ha="right")
    ax.set_ylabel("Yield (%)"); ax.legend(); ax.grid(alpha=0.25)
    st.pyplot(fig)
    st.dataframe(yp, use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Yield by technology node")
        st.dataframe(v("v_yield_by_node", order_by="technology_node_nm"),
                     use_container_width=True, hide_index=True)
    with c2:
        st.subheader("Loss decomposition (avg fail die)")
        st.dataframe(v("v_loss_decomposition"), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------- tool RCA
with tab_rca:
    st.subheader("Three-signal tool scorecard")
    st.write("The marginal etcher is the tool that is simultaneously **worst on "
             "yield**, **highest on edge-ring %**, and **highest on unscheduled "
             "downtime**. Those three independent signals converging is the finding.")
    rca = v("v_tool_rca", order_by="edge_ring_pct DESC")
    st.dataframe(
        rca.style.apply(
            lambda row: [f"background-color: #f7d7dd" if row["tool_name"] == SUSPECT else ""
                         for _ in row], axis=1),
        use_container_width=True, hide_index=True,
    )

    tools = rca["tool_name"].tolist()
    yield_def = (rca["avg_yield"].max() - rca["avg_yield"]) / rca["avg_yield"].max()
    edge = rca["edge_ring_pct"] / rca["edge_ring_pct"].max()
    down = rca["unscheduled_hrs"] / max(rca["unscheduled_hrs"].max(), 1)
    xx = np.arange(len(tools)); w = 0.26
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(xx - w, yield_def, w, label="Yield deficit", color=OK)
    ax.bar(xx, edge, w, label="Edge-ring %", color=ALERT)
    ax.bar(xx + w, down, w, label="Unsched. downtime", color=ACCENT)
    ax.set_xticks(xx); ax.set_xticklabels(tools)
    ax.set_ylabel("Normalised (0–1)"); ax.legend(fontsize=8); ax.grid(alpha=0.25)
    st.pyplot(fig)

    st.subheader("Lot exposure to the suspect chamber")
    exposure = q("""
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
    """, params=(SUSPECT,))
    st.dataframe(exposure, use_container_width=True, hide_index=True)

# ----------------------------------------------------------------- wafer maps
with tab_maps:
    st.subheader("Wafer-map explorer")
    etch_tools = q("SELECT DISTINCT tool_name FROM tools WHERE tool_type='ETCH' "
                   "ORDER BY tool_name")["tool_name"].tolist()
    choice = st.selectbox("Gate-etch tool", etch_tools,
                          index=etch_tools.index(SUSPECT) if SUSPECT in etch_tools else 0)
    pts = q("""
        SELECT d.x_coord_mm, d.y_coord_mm, d.defect_type
        FROM defects d
        JOIN run_history rh ON rh.wafer_id=d.wafer_id AND rh.step_id=4
        JOIN tools t ON t.tool_id=rh.tool_id
        WHERE t.tool_name=?
    """, params=(choice,))
    if len(pts) > 2500:
        pts = pts.sample(2500, random_state=42)
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    ax.add_patch(plt.Circle((0, 0), WAFER_RADIUS_MM, fill=False, color="#333", lw=1.2))
    for dtype, sub in pts.groupby("defect_type"):
        ax.scatter(sub["x_coord_mm"], sub["y_coord_mm"], s=7, alpha=0.55,
                   color=TYPE_COLORS.get(dtype, "#999"), label=dtype)
    ax.set_xlim(-160, 160); ax.set_ylim(-160, 160); ax.set_aspect("equal")
    ax.set_xlabel("x (mm)"); ax.set_ylabel("y (mm)")
    ax.set_title(f"Defects on wafers processed by {choice}")
    ax.legend(fontsize=8, loc="upper right")
    st.pyplot(fig)
    st.caption("Select ETCH-02 to see the dense edge-ring; the other etchers show "
               "only the baseline scatter.")
