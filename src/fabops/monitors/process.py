"""
process.py — is any chamber's process drifting?

Two substrates, read through the semantic layer:

* **FDC** (`fact_run_param`) — every run reports the parameters its recipe gave
  a setpoint for, so the residual `value − set_value` is grounded in something
  the fab actually asked for rather than in a fab-wide average.
* **metrology** (`fact_metrology`) — a post-step reading against the recipe's
  own `metric_target`, which never moves. A fault changes the measurement, not
  the specification, so the deviation means "how far off spec did this chamber
  run" and is comparable across products.

Both are charted the same way: a daily mean per chamber, peer-differenced
against same-role chambers, against limits frozen in the baseline window. The
choice of *residual* rather than raw value is what makes a chart over a mixed
product load meaningful — setpoints and targets differ per product, and a chart
over mixed setpoints charts the mix.
"""
from __future__ import annotations

import sqlite3
import statistics as st
from collections import defaultdict
from typing import Mapping

from fabops.monitors.model import (DEFAULT_CHART_RULE, Signal, charted_points,
                                   order_signals, peer_difference, series_from,
                                   spc_signals)

__all__ = ["FAMILY", "monitor_process"]

FAMILY = "process"


def _roles(connection: sqlite3.Connection) -> dict[str, str]:
    """A chamber's role is its tool type — ordinary observable data, and the
    grouping under which chambers are comparable at all."""
    return {label: tool_type for label, tool_type in connection.execute(
        "SELECT DISTINCT chamber_label, tool_type FROM fact_wafer_step "
        "ORDER BY chamber_label")}


def _daily(rows) -> tuple[dict[str, dict[int, float]],
                          dict[str, dict[int, int]]]:
    buckets: dict[tuple[str, int], list[float]] = defaultdict(list)
    for label, day, value in rows:
        buckets[(label, int(day))].append(float(value))
    values: dict[str, dict[int, float]] = defaultdict(dict)
    support: dict[str, dict[int, int]] = defaultdict(dict)
    for (label, day), samples in buckets.items():
        values[label][day] = st.fmean(samples)
        support[label][day] = len(samples)
    return dict(values), dict(support)


def _channel_signals(connection: sqlite3.Connection, horizon_days: int,
                     channel: str, rows, roles: Mapping[str, str],
                     chart_rule: str,
                     points: dict[str, int]) -> list[Signal]:
    values, support = _daily(rows)
    differenced = peer_difference(values, roles)
    signals: list[Signal] = []
    for series in series_from(differenced, support, channel):
        points[series.entity] = points.get(series.entity, 0) + charted_points(
            series, horizon_days, chart_rule)
        signals.extend(spc_signals(series, horizon_days, family=FAMILY,
                                   entity_kind="chamber",
                                   chart_rule=chart_rule))
    return signals


def monitor_process(connection: sqlite3.Connection, horizon_days: int,
                    chart_rule: str = DEFAULT_CHART_RULE
                    ) -> tuple[list[Signal], dict[str, object]]:
    """Chart every chamber on every parameter its recipes gave it."""
    roles = _roles(connection)
    signals: list[Signal] = []
    charted: dict[str, int] = {}
    points: dict[str, int] = {}

    parameters = [name for (name,) in connection.execute(
        "SELECT DISTINCT param_name FROM fact_run_param ORDER BY param_name")]
    for name in parameters:
        rows = connection.execute(
            "SELECT chamber_label, day_index, deviation_frac "
            "FROM fact_run_param WHERE param_name = ? "
            "AND deviation_frac IS NOT NULL ORDER BY run_meas_id",
            (name,)).fetchall()
        found = _channel_signals(connection, horizon_days, f"fdc:{name}",
                                 rows, roles, chart_rule, points)
        charted[f"fdc:{name}"] = len({row[0] for row in rows})
        signals.extend(found)

    metrics = [name for (name,) in connection.execute(
        "SELECT DISTINCT param_name FROM fact_metrology ORDER BY param_name")]
    for name in metrics:
        rows = connection.execute(
            "SELECT chamber_label, day_index, deviation_frac "
            "FROM fact_metrology WHERE param_name = ? "
            "AND deviation_frac IS NOT NULL ORDER BY metrology_id",
            (name,)).fetchall()
        found = _channel_signals(connection, horizon_days,
                                 f"metrology:{name}", rows, roles, chart_rule,
                                 points)
        charted[f"metrology:{name}"] = len({row[0] for row in rows})
        signals.extend(found)

    per_chamber = {}
    counted: dict[str, int] = {}
    for signal in signals:
        counted[signal.entity] = counted.get(signal.entity, 0) + 1
    for entity in sorted(points):
        judged = points[entity]
        per_chamber[entity] = {
            "signals": counted.get(entity, 0),
            "charted_points": judged,
            "rate": round(counted.get(entity, 0) / judged, 5) if judged else None,
        }

    measurements = {
        "channels_charted": len(charted),
        "chambers_seen": len(roles),
        "chambers_per_channel": dict(sorted(charted.items())),
        "chart_rule": chart_rule,
        "per_chamber": per_chamber,
        "per_chamber_note": (
            "a count with its denominator, and NOT a ranking. Measured on "
            "twelve fault-free worlds, one chamber routinely carries four to "
            "five times its peers' signal rate with nothing wrong in the fab: "
            "a chart whose baseline window happened to be quiet has tight "
            "limits for the rest of the horizon, and with about a dozen "
            "baseline days that luck varies a lot between chambers. Attributing "
            "a fault is `fabops.diagnosis`, which calibrates against a "
            "permutation of the candidate label instead."),
    }
    return order_signals(signals), measurements
