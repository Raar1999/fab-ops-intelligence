"""
equipment.py — how is the hardware itself behaving?

Four questions an equipment engineer asks, each answered from the state and
maintenance record rather than from process data:

* **availability and utilization** — what share of the horizon was this chamber
  able to run, and what share did it actually run;
* **MTBF / MTTR** — how often does it break, and how long is it out;
* **degradation between interventions** — is the chamber's own process residual
  climbing between preventive maintenances, which is the shape that precedes a
  failure rather than following one;
* **maintenance effect** — did behaviour actually change after a repair.

The last two are charted against *peers*, not against absolutes: a fab-wide
week affects every chamber, and a chamber that is merely living through a bad
week is not degrading.

One thing this module deliberately does not do is treat a repair as evidence of
a fault. In this fab a healthy chamber draws background alarms, escalates and
gets repaired (ADR-017 §4) — that symmetry exists precisely so a repair cannot
be read backwards into a fault, and a monitor that flagged "was repaired" would
be undoing it.
"""
from __future__ import annotations

import sqlite3
import statistics as st
from collections import defaultdict
from typing import Mapping

from fabops.monitors.model import (DEFAULT_CHART_RULE, MIN_PEERS,
                                   SIGMA_LIMIT, Signal, order_signals,
                                   peer_difference)

__all__ = ["FAMILY", "monitor_equipment"]

FAMILY = "equipment"

#: A maintenance window's before/after contrast is read over this many days on
#: each side. Long enough to average several runs, short enough that a second
#: intervention rarely lands inside it.
CONTRAST_DAYS = 10


def _leave_one_out_z(values: Mapping[str, float], label: str) -> float:
    others = [value for name, value in values.items() if name != label]
    if len(others) < MIN_PEERS:
        return 0.0
    spread = st.pstdev(others)
    return (values[label] - st.fmean(others)) / spread if spread else 0.0


def _states(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    intervals: dict[str, list[tuple]] = defaultdict(list)
    for label, state, start, end, minutes in connection.execute(
            "SELECT chamber_label, state, start_time, end_time, minutes "
            "FROM v_chamber_state_intervals ORDER BY state_id"):
        intervals[label].append((start, end, state, float(minutes)))
    for label in intervals:
        intervals[label].sort(key=lambda row: (row[0], row[1], row[2]))
    return dict(intervals)


def _reliability(intervals: list[tuple]) -> dict[str, float]:
    """MTBF from the gaps between DOWN intervals, MTTR from their lengths."""
    downs = [row for row in intervals if row[2] == "DOWN"]
    mttr = st.fmean([row[3] for row in downs]) / 60.0 if downs else 0.0

    uptimes: list[float] = []
    running = 0.0
    for start, end, state, minutes in intervals:
        if state == "DOWN":
            if running > 0:
                uptimes.append(running)
            running = 0.0
        else:
            running += minutes
    if running > 0:
        uptimes.append(running)
    mtbf = st.fmean(uptimes) / 60.0 if uptimes else 0.0
    return {"mtbf_hours": mtbf, "mttr_hours": mttr, "down_events": len(downs)}


def _residual_series(connection: sqlite3.Connection
                     ) -> dict[str, dict[int, float]]:
    """One peer-differenced daily series per chamber, pooled over its FDC
    channels. Pooled because degradation is a property of the chamber rather
    than of one parameter, and a per-channel view is `process.py`'s job."""
    raw: dict[str, dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list))
    for label, day, value in connection.execute(
            "SELECT chamber_label, day_index, deviation_frac "
            "FROM fact_run_param WHERE deviation_frac IS NOT NULL "
            "ORDER BY run_meas_id"):
        raw[label][int(day)].append(abs(float(value)))
    means = {label: {day: st.fmean(values) for day, values in days.items()}
             for label, days in raw.items()}
    roles = {label: role for label, role in connection.execute(
        "SELECT DISTINCT chamber_label, tool_type FROM fact_wafer_step "
        "ORDER BY chamber_label")}
    return peer_difference(means, roles)


def _slope(days: list[int], values: list[float]) -> float:
    """Ordinary least squares slope, per day. Two points give no trend."""
    if len(days) < 3:
        return 0.0
    mean_x, mean_y = st.fmean(days), st.fmean(values)
    denominator = sum((x - mean_x) ** 2 for x in days)
    if denominator <= 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y)
               for x, y in zip(days, values)) / denominator


def monitor_equipment(connection: sqlite3.Connection, horizon_days: int,
                      chart_rule: str = DEFAULT_CHART_RULE
                      ) -> tuple[list[Signal], dict[str, object]]:
    intervals = _states(connection)
    utilization = {label: (float(prod), float(total), float(down))
                   for label, prod, total, down in connection.execute(
                       "SELECT chamber_label, productive_min, total_min, "
                       "down_min FROM v_chamber_utilization "
                       "ORDER BY chamber_label")}

    health: dict[str, dict[str, float]] = {}
    for label in sorted(intervals):
        productive, total, down = utilization.get(label, (0.0, 0.0, 0.0))
        record = _reliability(intervals[label])
        record["utilization"] = productive / total if total else 0.0
        record["availability"] = 1.0 - (down / total) if total else 1.0
        health[label] = record

    signals: list[Signal] = []

    def flag(metric: str, higher_is_worse: bool, rule: str, detail: str
             ) -> None:
        values = {label: record[metric] for label, record in health.items()}
        for label in sorted(values):
            z = _leave_one_out_z(values, label)
            if (z if higher_is_worse else -z) >= SIGMA_LIMIT:
                signals.append(Signal(
                    family=FAMILY, entity_kind="chamber", entity=label,
                    channel=metric, rule=rule, day_index=horizon_days,
                    value=values[label], z=z, limit=SIGMA_LIMIT,
                    support=len(values), detail=detail))

    flag("mttr_hours", True, "mttr_above_peers",
         "repairs on this chamber take longer than on its same-role peers")
    flag("availability", False, "availability_below_peers",
         "this chamber was unavailable for more of the horizon than its peers")

    # Degradation: the slope of the chamber's own peer-differenced residual,
    # standardized against the spread of its peers' slopes.
    residual = _residual_series(connection)
    slopes = {label: _slope(sorted(points), [points[day]
                                             for day in sorted(points)])
              for label, points in residual.items() if len(points) >= 8}
    for label in sorted(slopes):
        z = _leave_one_out_z(slopes, label)
        if abs(z) >= SIGMA_LIMIT:
            signals.append(Signal(
                family=FAMILY, entity_kind="chamber", entity=label,
                channel="fdc_residual_slope", rule="degradation_trend",
                day_index=horizon_days, value=slopes[label], z=z,
                limit=SIGMA_LIMIT, support=len(residual.get(label, {})),
                detail="the chamber's process residual trends against its "
                       "same-role peers over the horizon"))

    # Maintenance effect: did the residual actually change after a repair?
    windows = connection.execute(
        "SELECT chamber_label, maint_type, day_index FROM fact_maintenance "
        "ORDER BY maint_id").fetchall()
    effects: list[dict[str, object]] = []
    for label, maint_type, day in windows:
        points = residual.get(label)
        if not points:
            continue
        before = [value for other, value in points.items()
                  if day - CONTRAST_DAYS <= other < day]
        after = [value for other, value in points.items()
                 if day <= other < day + CONTRAST_DAYS]
        if len(before) < 4 or len(after) < 4:
            continue
        change = st.fmean(after) - st.fmean(before)
        spread = st.pstdev(list(points.values()))
        z = change / spread if spread else 0.0
        effects.append({"chamber": label, "maint_type": maint_type,
                        "day_index": int(day), "change": round(change, 6),
                        "z": round(z, 4)})
        if abs(z) >= SIGMA_LIMIT:
            signals.append(Signal(
                family=FAMILY, entity_kind="chamber", entity=label,
                channel="fdc_residual", rule="maintenance_effect",
                day_index=int(day), value=change, z=z, limit=SIGMA_LIMIT,
                support=len(before) + len(after),
                detail=f"the residual moved across a {maint_type} window"))

    measurements = {
        "chambers": len(health),
        "health": {label: {key: round(value, 6)
                           for key, value in sorted(record.items())}
                   for label, record in sorted(health.items())},
        "maintenance_effects": sorted(
            effects, key=lambda row: (row["chamber"], row["day_index"])),
    }
    return order_signals(signals), measurements
