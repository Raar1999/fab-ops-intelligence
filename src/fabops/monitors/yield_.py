"""
yield_.py — is the fab making its number, and has that changed?

Everything here is charted on **attainment** — a wafer's yield minus its own
product's declared target — and never on raw yield. Products in this world
differ by up to ten points of target, so a raw fab-wide series moves when the
mix moves; the audit verified 24-point swings in v1's weekly view that were
mix and nothing else. Subtracting each product's own target is the smallest
correction that makes two weeks comparable, and the raw series is kept beside
it so a reader can see the two disagree.

**Chamber-grain yield is measured and reported here, and it does not raise a
signal.** ADR-028 retired cohort yield as an attribution criterion after
measuring that on twelve fault-free worlds each etch tool is the worst one
about a third of the time, and that the standing does not move with severity.
It is a real downstream consequence and a real observable — an engineer looks
at it — but a monitor that alarmed on it would be alarming on benign
variation, which is the defect three separate gates of this project have each
found once.
"""
from __future__ import annotations

import sqlite3
import statistics as st
from collections import defaultdict

from fabops.monitors.model import (DEFAULT_CHART_RULE, MIN_PEERS,
                                   SIGMA_LIMIT, Series, Signal, order_signals,
                                   spc_signals)

__all__ = ["FAMILY", "monitor_yield"]

FAMILY = "yield"


def monitor_yield(connection: sqlite3.Connection, horizon_days: int,
                  chart_rule: str = DEFAULT_CHART_RULE
                  ) -> tuple[list[Signal], dict[str, object]]:
    signals: list[Signal] = []

    # --- the fab-wide attainment trend, charted.
    daily = connection.execute(
        "SELECT day_index, wafers, mean_yield_pct, mean_attainment_pts "
        "FROM v_yield_trend_daily ORDER BY day_index").fetchall()
    days = tuple(int(row[0]) for row in daily)
    series = Series(entity="fab", channel="attainment_pts", days=days,
                    values=tuple(float(row[3]) for row in daily),
                    support=tuple(int(row[1]) for row in daily))
    signals.extend(spc_signals(series, horizon_days, family=FAMILY,
                               entity_kind="fab", chart_rule=chart_rule))

    # --- per product: is this product's own attainment off its target?
    products = connection.execute(
        "SELECT product_name, target_yield_pct, wafers, mean_yield_pct, "
        "       mean_attainment_pts FROM v_product_attainment "
        "ORDER BY product_name").fetchall()
    per_wafer: dict[str, list[float]] = defaultdict(list)
    for product, attainment in connection.execute(
            "SELECT product_name, attainment_pts FROM fact_yield "
            "ORDER BY yield_id"):
        per_wafer[product].append(float(attainment))

    attainment_rows = []
    for product, target, wafers, mean_yield, mean_attainment in products:
        values = per_wafer.get(product, [])
        spread = st.stdev(values) if len(values) > 1 else 0.0
        error = spread / (len(values) ** 0.5) if values else 0.0
        z = mean_attainment / error if error else 0.0
        attainment_rows.append({
            "product": product, "target_yield_pct": target,
            "wafers": int(wafers), "mean_yield_pct": round(mean_yield, 4),
            "mean_attainment_pts": round(mean_attainment, 4),
            "standard_error_pts": round(error, 4), "z": round(z, 4)})
        if z <= -SIGMA_LIMIT:
            signals.append(Signal(
                family=FAMILY, entity_kind="product", entity=product,
                channel="attainment_pts", rule="product_below_target",
                day_index=horizon_days, value=float(mean_attainment), z=z,
                limit=-SIGMA_LIMIT * error, support=int(wafers),
                detail="the product's mean attainment is below its own target "
                       "by more than three standard errors of the mean"))

    # --- chamber standing, measured and reported, never alarmed on.
    deficits = connection.execute(
        "SELECT step_name, chamber_label, wafers, deficit_pts "
        "FROM v_chamber_yield_deficit ORDER BY step_name, chamber_label"
    ).fetchall()
    by_step: dict[str, dict[str, float]] = defaultdict(dict)
    support: dict[tuple[str, str], int] = {}
    for step, label, wafers, deficit in deficits:
        by_step[step][label] = float(deficit)
        support[(step, label)] = int(wafers)

    standings = []
    for step in sorted(by_step):
        values = by_step[step]
        for label in sorted(values):
            others = [v for name, v in values.items() if name != label]
            spread = st.pstdev(others) if len(others) >= MIN_PEERS else 0.0
            z = ((values[label] - st.fmean(others)) / spread
                 if spread else 0.0)
            standings.append({"step": step, "chamber": label,
                              "wafers": support[(step, label)],
                              "deficit_pts": round(values[label], 4),
                              "z": round(z, 4)})

    measurements = {
        "fab_attainment_pts": round(
            st.fmean([float(row[3]) for row in daily]), 4) if daily else None,
        "fab_mean_yield_pct": round(
            st.fmean([float(row[2]) for row in daily]), 4) if daily else None,
        "days_charted": len(daily),
        "products": attainment_rows,
        "chamber_yield_standing": standings,
        "chamber_yield_note": (
            "reported, never alarmed on: ADR-028 measured this channel to be "
            "satisfied by chance on a fault-free world and flat across the "
            "severity ladder"),
    }
    return order_signals(signals), measurements
