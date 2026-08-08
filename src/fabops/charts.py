"""
charts.py — every figure in the investigation, rendered with matplotlib.

Design choices:
  * The suspect tool (ETCH-02) is always drawn in a warm "alert" colour so the
    eye lands on it across every chart.
  * Figures are saved to reports/figures/ at 150 dpi for README embedding.
  * Each function returns the saved path so the driver can collect them.

All data is synthetic (seed=42); these charts visualise a planted RCA story.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless / file output
import matplotlib.pyplot as plt
import numpy as np

from fabops.config import (
    ACCENT,
    ALERT,
    FIGURES_DIR as FIG_DIR,
    OK,
    REPO_ROOT,
    TYPE_COLORS,
    WAFER_RADIUS_MM,
    DEMO_SUSPECT_TOOL as SUSPECT,
)
from fabops.db import run_query, run_view

FIG_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 10,
})


def _save(fig, name: str) -> Path:
    path = FIG_DIR / name
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def chart_product_gap() -> Path:
    """Bar chart: actual yield vs target per product (the symptom)."""
    df = run_view("v_yield_by_product", order_by="gap_to_target")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(df))
    w = 0.38
    ax.bar(x - w / 2, df["actual_yield"], w, label="Actual", color=OK)
    ax.bar(x + w / 2, df["target_yield_pct"], w, label="Target", color=ACCENT)
    ax.set_xticks(x)
    ax.set_xticklabels(df["product_name"], rotation=20, ha="right")
    ax.set_ylabel("Yield (%)")
    ax.set_title("Symptom: every product misses its yield target (~9 pts each)")
    ax.legend()
    return _save(fig, "01_product_gap.png")


def chart_tool_yield() -> Path:
    """Bar chart: gate-etch yield per etch tool (the suspect surfaces)."""
    df = run_view("v_etch_tool_yield", order_by="avg_yield")
    colors = [ALERT if t == SUSPECT else OK for t in df["tool_name"]]
    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(df["tool_name"], df["avg_yield"], color=colors)
    ax.set_ylabel("Avg yield (%)")
    ax.set_title("Suspect: gate-etch yield by tool — one etcher lags ~12 pts")
    for b, v in zip(bars, df["avg_yield"]):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.5, f"{v:.1f}", ha="center")
    return _save(fig, "02_tool_yield.png")


def chart_rca_scorecard() -> Path:
    """Normalised three-signal scorecard: yield deficit, edge-ring %, downtime."""
    df = run_view("v_tool_rca", order_by="tool_name")
    tools = df["tool_name"].tolist()
    # normalise each signal to 0–1 for side-by-side comparison
    yield_deficit = (df["avg_yield"].max() - df["avg_yield"]) / df["avg_yield"].max()
    edge = df["edge_ring_pct"] / df["edge_ring_pct"].max()
    down = df["unscheduled_hrs"] / max(df["unscheduled_hrs"].max(), 1)

    x = np.arange(len(tools))
    w = 0.26
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(x - w, yield_deficit, w, label="Yield deficit (norm.)", color=OK)
    ax.bar(x, edge, w, label="Edge-ring % (norm.)", color=ALERT)
    ax.bar(x + w, down, w, label="Unscheduled downtime (norm.)", color=ACCENT)
    ax.set_xticks(x)
    ax.set_xticklabels(tools)
    ax.set_ylabel("Normalised signal (0–1)")
    ax.set_title("Convergence: three independent signals all point at ETCH-02")
    ax.legend(fontsize=8)
    return _save(fig, "03_rca_scorecard.png")


def _wafer_panel(ax, wafer_ids, title):
    """Scatter every defect on the given wafers over a wafer outline."""
    placeholders = ",".join("?" for _ in wafer_ids)
    df = run_query(
        f"SELECT x_coord_mm, y_coord_mm, defect_type FROM defects "
        f"WHERE wafer_id IN ({placeholders})",
        params=tuple(wafer_ids),
    )
    # sample for legibility if very dense
    if len(df) > 1800:
        df = df.sample(1800, random_state=42)
    # wafer outline
    circle = plt.Circle((0, 0), WAFER_RADIUS_MM, fill=False, color="#333", lw=1.2)
    ax.add_patch(circle)
    for dtype, sub in df.groupby("defect_type"):
        ax.scatter(sub["x_coord_mm"], sub["y_coord_mm"], s=6, alpha=0.55,
                   color=TYPE_COLORS.get(dtype, "#999"), label=dtype)
    ax.set_xlim(-160, 160)
    ax.set_ylim(-160, 160)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("x (mm)")


def chart_wafer_maps() -> Path:
    """Showpiece: defect maps for suspect wafers vs the rest, side by side."""
    suspect_ids = run_query(
        "SELECT DISTINCT wafer_id FROM v_gate_etch_runs WHERE tool_name=?",
        params=(SUSPECT,),
    )["wafer_id"].tolist()
    other_ids = run_query(
        "SELECT DISTINCT wafer_id FROM v_gate_etch_runs "
        "WHERE tool_type='ETCH' AND tool_name<>?", params=(SUSPECT,)
    )["wafer_id"].tolist()

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.6))
    _wafer_panel(axes[0], suspect_ids, f"{SUSPECT} wafers — dense edge ring")
    _wafer_panel(axes[1], other_ids, "Other etchers — sparse edge ring (baseline)")
    axes[0].set_ylabel("y (mm)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, fontsize=8,
               frameon=False, bbox_to_anchor=(0.5, -0.04))
    fig.suptitle("Spatial confirmation: edge-ring signature isolated to ETCH-02",
                 fontsize=12, y=1.02)
    return _save(fig, "04_wafer_maps.png")


def chart_downtime_timeline() -> Path:
    """Unscheduled maintenance events over time, per etch tool."""
    df = run_query("""
        SELECT t.tool_name, m.start_time, m.duration_hours
        FROM maintenance m JOIN tools t ON t.tool_id=m.tool_id
        WHERE t.tool_type='ETCH' AND m.maint_type='UNSCHEDULED'
        ORDER BY m.start_time
    """)
    fig, ax = plt.subplots(figsize=(9, 4))
    if df.empty:
        ax.text(0.5, 0.5, "no unscheduled etch events", ha="center")
        return _save(fig, "05_downtime_timeline.png")
    df["start_time"] = df["start_time"].astype(str).str.slice(0, 10)
    tools = sorted(df["tool_name"].unique())
    ymap = {t: i for i, t in enumerate(tools)}
    for t in tools:
        sub = df[df["tool_name"] == t]
        color = ALERT if t == SUSPECT else OK
        ax.scatter(sub["start_time"], [ymap[t]] * len(sub),
                   s=sub["duration_hours"] * 30, color=color, alpha=0.7,
                   edgecolor="black", linewidth=0.4)
    ax.set_yticks(list(ymap.values()))
    ax.set_yticklabels(tools)
    ax.set_xlabel("Date")
    ax.set_title("Independent signal: unscheduled etch downtime clusters on ETCH-02\n"
                 "(marker area ∝ event duration)")
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right", fontsize=8)
    return _save(fig, "05_downtime_timeline.png")


def chart_defect_yield_corr() -> Path:
    """Scatter: per-wafer defect count vs yield, with Pearson r in the title."""
    df = run_query("""
        SELECT y.wafer_id, y.yield_pct, COALESCE(d.n,0) AS defects
        FROM yield_data y
        LEFT JOIN (SELECT wafer_id, COUNT(*) n FROM defects GROUP BY wafer_id) d
               ON d.wafer_id=y.wafer_id
    """)
    r = np.corrcoef(df["defects"], df["yield_pct"])[0, 1]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(df["defects"], df["yield_pct"], s=14, alpha=0.5, color=OK)
    # least-squares trend line
    m, b = np.polyfit(df["defects"], df["yield_pct"], 1)
    xs = np.array([df["defects"].min(), df["defects"].max()])
    ax.plot(xs, m * xs + b, color=ALERT, lw=2, label=f"fit (slope {m:.2f})")
    ax.set_xlabel("Defects per wafer")
    ax.set_ylabel("Yield (%)")
    ax.set_title(f"Defect load vs yield — Pearson r = {r:.2f}")
    ax.legend()
    return _save(fig, "06_defect_yield_corr.png")


def build_all() -> list[Path]:
    """Render every figure; return the list of paths."""
    return [
        chart_product_gap(),
        chart_tool_yield(),
        chart_rca_scorecard(),
        chart_wafer_maps(),
        chart_downtime_timeline(),
        chart_defect_yield_corr(),
    ]


if __name__ == "__main__":
    for p in build_all():
        print("wrote", p.relative_to(REPO_ROOT))
