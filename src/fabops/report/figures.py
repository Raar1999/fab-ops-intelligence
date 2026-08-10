"""
figures.py — the charts, in one place.

The audit found the dashboard re-implementing figures that `charts.py` already
drew, which is how two surfaces of one project come to disagree about what a
number looks like. There is one chart module for the schema v2 surfaces and
this is it: the app imports these, a notebook can import these, and nothing
draws its own.

Every function takes prepared data from `fabops.report.workspace` rather than a
database, so a figure cannot quietly become a second place where analysis
happens. Matplotlib is used headless (`Agg`) and each function returns a
`Figure` the caller renders or saves.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
from matplotlib.figure import Figure                     # noqa: E402

__all__ = [
    "PALETTE",
    "attainment_trend",
    "control_chart_figure",
    "die_map",
    "product_attainment",
    "state_timeline_figure",
    "wafer_map",
]

#: One palette, inherited from the legacy chart module so the two surfaces of
#: this repository look like one project. `ALERT` is used for a limit or a
#: failure and never to mark a suspect: nothing on a v2 surface knows one.
PALETTE = {"alert": "#d1495b", "ok": "#3b7a9e", "accent": "#edae49",
           "muted": "#8d99ae", "pass": "#66a182"}

_BIN_COLORS = {"PASS": PALETTE["pass"], "OPEN_SHORT": PALETTE["alert"],
               "PARAM": PALETTE["accent"], "LEAK": PALETTE["ok"],
               "OTHER": PALETTE["muted"]}


def _style(axes, title: str, xlabel: str, ylabel: str) -> None:
    axes.set_title(title, fontsize=11)
    axes.set_xlabel(xlabel, fontsize=9)
    axes.set_ylabel(ylabel, fontsize=9)
    axes.grid(alpha=0.25, linewidth=0.6)
    axes.tick_params(labelsize=8)


def attainment_trend(weekly: Sequence[Mapping[str, Any]]) -> Figure:
    """Attainment against the raw mean, on one axis, deliberately.

    Seeing the two series disagree is how a reader learns that the raw one was
    measuring the product mix — the artifact the audit verified in v1.
    """
    figure, axes = plt.subplots(figsize=(8, 3.2))
    weeks = [row["week_index"] for row in weekly]
    axes.plot(weeks, [row["mean_attainment_pts"] for row in weekly],
              marker="o", color=PALETTE["ok"], label="attainment vs target")
    axes.axhline(0.0, color=PALETTE["muted"], linewidth=1.0, linestyle="--")
    twin = axes.twinx()
    twin.plot(weeks, [row["mean_yield_pct"] for row in weekly], marker=".",
              color=PALETTE["accent"], alpha=0.8, label="raw mean yield")
    twin.set_ylabel("raw mean yield %", fontsize=9)
    twin.tick_params(labelsize=8)
    _style(axes, "Weekly yield: target-normalized against raw",
           "week", "attainment (points)")
    handles = axes.get_lines()[:1] + twin.get_lines()[:1]
    axes.legend(handles, [line.get_label() for line in handles], fontsize=8,
                loc="lower right")
    figure.tight_layout()
    return figure


def product_attainment(products: Sequence[Mapping[str, Any]]) -> Figure:
    figure, axes = plt.subplots(figsize=(7, 3.2))
    names = [row["product_name"] for row in products]
    values = [row["mean_attainment_pts"] for row in products]
    colors = [PALETTE["alert"] if value < 0 else PALETTE["ok"]
              for value in values]
    axes.bar(names, values, color=colors)
    axes.axhline(0.0, color=PALETTE["muted"], linewidth=1.0)
    _style(axes, "Attainment against each product's own target",
           "", "points above/below target")
    axes.tick_params(axis="x", rotation=30, labelsize=8)
    figure.tight_layout()
    return figure


def control_chart_figure(chart: Mapping[str, Any]) -> Figure:
    """The chart the rules were evaluated on, with its hits marked."""
    figure, axes = plt.subplots(figsize=(8, 3.2))
    days = chart["days"]
    axes.plot(days, chart["values"], marker="o", markersize=3,
              color=PALETTE["ok"], linewidth=1.0, label="peer-differenced")
    axes.plot(days, chart["upper"], color=PALETTE["alert"], linewidth=0.9,
              linestyle="--", label="action limits")
    axes.plot(days, chart["lower"], color=PALETTE["alert"], linewidth=0.9,
              linestyle="--")
    axes.axhline(chart["centre"], color=PALETTE["muted"], linewidth=0.9)
    axes.axvspan(min(days), chart["baseline_last_day"], color=PALETTE["muted"],
                 alpha=0.12)
    flagged = {signal["day_index"] for signal in chart["signals"]}
    marked = [(day, value) for day, value in zip(days, chart["values"])
              if day in flagged]
    if marked:
        axes.scatter([day for day, _v in marked], [v for _d, v in marked],
                     s=48, facecolors="none", edgecolors=PALETTE["alert"],
                     linewidths=1.4, label="rule hit", zorder=5)
    _style(axes, f"{chart['chamber']} — {chart['channel']}",
           "day", "residual vs same-role peers")
    axes.legend(fontsize=8, loc="upper left")
    figure.tight_layout()
    return figure


def wafer_map(detail: Mapping[str, Any], layer: str | None = None) -> Figure:
    """Defect coordinates on the wafer, coloured by class.

    The class is shown because an analyst reads it; every *zone* on this
    surface is derived from the coordinates, because the class is a noisy draw
    over a hidden origin and a map coloured by an inference would be a map of
    the classifier.
    """
    figure, axes = plt.subplots(figsize=(4.6, 4.6))
    radius = float(detail.get("wafer_radius_mm") or 150.0)
    axes.add_patch(plt.Circle((0, 0), radius, fill=False,
                              color=PALETTE["muted"], linewidth=1.2))
    axes.add_patch(plt.Circle((0, 0), 0.80 * radius, fill=False,
                              color=PALETTE["muted"], linewidth=0.6,
                              linestyle=":"))
    defects = [d for d in detail["defects"]
               if layer is None or d["layer"] == layer]
    classes = sorted({d["classified_type"] for d in defects})
    wheel = [PALETTE["alert"], PALETTE["ok"], PALETTE["accent"],
             PALETTE["pass"], PALETTE["muted"]]
    for index, name in enumerate(classes):
        points = [d for d in defects if d["classified_type"] == name]
        axes.scatter([d["x_mm"] for d in points], [d["y_mm"] for d in points],
                     s=12, alpha=0.75, label=name,
                     color=wheel[index % len(wheel)])
    axes.set_xlim(-radius * 1.05, radius * 1.05)
    axes.set_ylim(-radius * 1.05, radius * 1.05)
    axes.set_aspect("equal")
    title = f"wafer {detail['wafer_id']} — {len(defects)} defects"
    _style(axes, title + (f" ({layer})" if layer else ""), "x (mm)", "y (mm)")
    if classes:
        axes.legend(fontsize=7, loc="upper right", framealpha=0.9)
    figure.tight_layout()
    return figure


def die_map(detail: Mapping[str, Any]) -> Figure:
    """The die grid coloured by tester bin.

    A bin is a *symptom* drawn through a confusion row over a hidden cause, so
    this map shows where a wafer failed and deliberately not why.
    """
    figure, axes = plt.subplots(figsize=(4.6, 4.6))
    for code, color in _BIN_COLORS.items():
        cells = [d for d in detail["die"] if d["bin_code"] == code]
        if cells:
            axes.scatter([d["die_x"] for d in cells],
                         [d["die_y"] for d in cells], s=6, marker="s",
                         color=color, label=code)
    axes.invert_yaxis()
    axes.set_aspect("equal")
    _style(axes,
           f"wafer {detail['wafer_id']} — {detail['good_die']}/"
           f"{detail['total_die']} die pass ({detail['yield_pct']:.1f}%)",
           "die column", "die row")
    axes.legend(fontsize=7, loc="upper right", framealpha=0.9, ncol=2)
    figure.tight_layout()
    return figure


def state_timeline_figure(intervals: Sequence[Mapping[str, Any]],
                          horizon_days: int) -> Figure:
    """One chamber's E10-style state record across the horizon."""
    colors = {"PRODUCTIVE": PALETTE["pass"], "IDLE": PALETTE["muted"],
              "DOWN": PALETTE["alert"], "PM": PALETTE["accent"],
              "QUAL": PALETTE["ok"]}
    figure, axes = plt.subplots(figsize=(8, 1.9))
    if intervals:
        origin = min(row["start_time"] for row in intervals)
        for row in intervals:
            start = _hours_between(origin, row["start_time"]) / 24.0
            width = float(row["minutes"]) / 1440.0
            axes.barh(0, width, left=start, height=0.6,
                      color=colors.get(row["state"], PALETTE["muted"]))
    axes.set_yticks([])
    axes.set_xlim(0, horizon_days)
    _style(axes, "chamber state timeline", "day", "")
    handles = [plt.Line2D([0], [0], color=color, linewidth=6, label=state)
               for state, color in colors.items()]
    axes.legend(handles=handles, fontsize=7, ncol=5, loc="upper center",
                bbox_to_anchor=(0.5, -0.45), frameon=False)
    figure.tight_layout()
    return figure


def _hours_between(origin: str, stamp: str) -> float:
    from datetime import datetime

    return (datetime.fromisoformat(stamp)
            - datetime.fromisoformat(origin)).total_seconds() / 3600.0
