"""
workspace.py — everything the investigation workspace displays, as data.

The dashboard this feeds renders engine output and computes nothing. That
separation is the whole point of `DASHBOARD_AUDIT` §4: the audited dashboard
highlighted a row pink because a constant said so, and the fix is not a better
constant but a surface with nothing to say of its own.

Keeping the preparation here rather than in the Streamlit file buys two things
beyond tidiness. Every number on the screen is reachable from a test that needs
no browser and no Streamlit; and the app is a renderer thin enough that reading
it tells you what it draws rather than what it decides.

Each function takes an open semantic-layer connection or a database path, and
returns plain dictionaries and lists.
"""
from __future__ import annotations

import sqlite3
import statistics as st
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabops.monitors import MONITOR, monitor
from fabops.monitors.model import Series, chart_limits
from fabops.monitors.process import channel_series, channels_of
from fabops.semantic import LAYER_VERSION, iter_dicts, open_layer, read

__all__ = [
    "WORKSPACE_PAGES",
    "control_chart",
    "dataset_summary",
    "equipment_page",
    "fab_today",
    "process_page",
    "wafer_detail",
    "wafer_index",
    "yield_page",
]

#: The information architecture `DASHBOARD_AUDIT` §4 recommends, in its order.
WORKSPACE_PAGES = ("Fab Today", "Process", "Equipment", "Yield",
                   "Investigation", "Wafer explorer")


def dataset_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Provenance, as far as the analysis plane is entitled to read it.

    The generator's own version is a column of `dataset_meta` and is
    deliberately **not** read here. `fabops` may not name the generator — the
    code-plane lint forbids the token outright, and softening a working guard
    to put one more line on a screen is the wrong trade. What a consumer
    actually needs for compatibility is the *schema* version, which is here;
    what an auditor needs is in the dataset's own `manifest.json`, which the
    evaluator reads and this plane does not.
    """
    row = connection.execute(
        "SELECT dataset_id, schema_version, time_origin, horizon_days "
        "FROM dataset_meta").fetchone()
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("lots", "wafers", "runs", "defects", "wafer_yield",
                      "alarms", "maintenance")}
    return {"dataset_id": row[0], "schema_version": row[1],
            "time_origin": row[2], "horizon_days": int(row[3]),
            "counts": counts,
            "semantic_layer": f"fabops.semantic/{LAYER_VERSION}"}


# ------------------------------------------------------------- Fab Today


def fab_today(connection: sqlite3.Connection,
              report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What is happening, in the order an engineer asks it.

    The headline number is **attainment**, not yield: a fab-wide mean over
    products with different targets moves when the mix moves, which the audit
    verified as a 24-point artifact in v1's weekly view.
    """
    trend = [dict(zip(("week_index", "wafers", "products", "mean_yield_pct",
                       "mean_attainment_pts"), row))
             for row in read(connection, "v_yield_trend",
                             order_by="week_index")]
    products = list(iter_dicts(connection, "v_product_attainment",
                               order_by="product_name"))
    attainment = [row["attainment_pts"] for row in
                  iter_dicts(connection, "fact_yield", order_by="yield_id")]

    unavailable = [row for row in iter_dicts(connection,
                                             "v_chamber_utilization",
                                             order_by="chamber_label")
                   if row["down_min"] + row["pm_min"] > 0]
    unavailable.sort(key=lambda row: (-(row["down_min"] + row["pm_min"]),
                                      row["chamber_label"]))

    movers = []
    if report is not None:
        for signal in report.get("signals", ()):
            if signal["family"] == "defect":
                movers.append(signal)
    movers.sort(key=lambda s: (-abs(s["z"]), s["entity"]["id"],
                               s["day_index"]))

    return {
        "weekly_trend": trend,
        "products": products,
        "fab_attainment_pts": round(st.fmean(attainment), 3) if attainment
        else None,
        "wafers_tested": len(attainment),
        "least_available_chambers": unavailable[:8],
        "defect_movers": movers[:12],
        "signal_counts": _signal_counts(report),
        "note": ("attainment is yield minus each product's own declared "
                 "target; the raw fab mean is shown beside it because the two "
                 "disagree exactly when the product mix moved"),
    }


def _signal_counts(report: Mapping[str, Any] | None) -> dict[str, int]:
    if report is None:
        return {}
    counts: dict[str, int] = {}
    for signal in report.get("signals", ()):
        counts[signal["family"]] = counts.get(signal["family"], 0) + 1
    return dict(sorted(counts.items()))


# --------------------------------------------------------------- Process


def control_chart(connection: sqlite3.Connection, channel: str,
                  chamber: str, horizon_days: int,
                  signals: Sequence[Mapping[str, Any]] = ()
                  ) -> dict[str, Any] | None:
    """One chamber's chart on one channel, with the rule hits marked.

    The series and the limits come from the monitor's own functions, so the
    picture is the decision rather than a second computation that resembles it.
    """
    match = [series for series in channel_series(connection, channel)
             if series.entity == chamber]
    if not match:
        return None
    series: Series = match[0]
    built = chart_limits(series, horizon_days)
    if built is None:
        return None
    chart, upper, lower = built
    hits = [signal for signal in signals
            if signal["entity"]["id"] == chamber
            and signal["channel"] == channel]
    return {
        "chamber": chamber, "channel": channel,
        "days": list(series.days), "values": list(series.values),
        "support": list(series.support),
        "centre": chart.centre, "upper": list(upper), "lower": list(lower),
        "baseline_last_day": chart.baseline_last_day,
        "baseline_points": chart.baseline_points,
        "inflation": round(chart.inflation, 4),
        "signals": sorted(hits, key=lambda s: (s["day_index"], s["rule"])),
        "note": ("the series is this chamber's daily mean residual minus the "
                 "contemporaneous mean of its same-role peers; the limits were "
                 "frozen in the baseline window and widened for the "
                 "uncertainty of estimating a spread from it"),
    }


def process_page(connection: sqlite3.Connection,
                 report: Mapping[str, Any]) -> dict[str, Any]:
    signals = [s for s in report.get("signals", ())
               if s["family"] == "process"]
    chambers: dict[str, int] = {}
    for signal in signals:
        chambers[signal["entity"]["id"]] = chambers.get(
            signal["entity"]["id"], 0) + 1
    per_chamber = report.get("measurements", {}).get("process", {}).get(
        "per_chamber", {})
    return {
        "channels": list(channels_of(connection)),
        "chambers": sorted(set(per_chamber) | set(chambers)),
        "signals": signals,
        "per_chamber": per_chamber,
        "per_chamber_note": report.get("measurements", {}).get(
            "process", {}).get("per_chamber_note", ""),
    }


# ------------------------------------------------------------- Equipment


def equipment_page(connection: sqlite3.Connection,
                   report: Mapping[str, Any]) -> dict[str, Any]:
    health = report.get("measurements", {}).get("equipment", {}).get(
        "health", {})
    utilization = list(iter_dicts(connection, "v_chamber_utilization",
                                  order_by="chamber_label"))
    maintenance = list(iter_dicts(connection, "fact_maintenance",
                                  order_by="maint_id"))
    alarms = list(iter_dicts(connection, "fact_alarm", order_by="alarm_id"))
    defects = {row[0]: row[1] for row in connection.execute("""
        SELECT f.chamber_label,
               COUNT(*) * 1.0 / COUNT(DISTINCT f.wafer_id)
        FROM (SELECT DISTINCT chamber_label, wafer_id FROM fact_wafer_step) f
        JOIN fact_defect d ON d.wafer_id = f.wafer_id
        GROUP BY f.chamber_label ORDER BY f.chamber_label""")}
    return {
        "health": health, "utilization": utilization,
        "maintenance": maintenance, "alarms": alarms,
        "defects_per_wafer": defects,
        "signals": [s for s in report.get("signals", ())
                    if s["family"] == "equipment"],
        "note": ("a repair is not evidence of a fault in this fab: a healthy "
                 "chamber draws background alarms, escalates and gets "
                 "repaired, which is what keeps a repair from being readable "
                 "backwards into a fault"),
    }


def state_timeline(connection: sqlite3.Connection, chamber: str
                   ) -> list[dict[str, Any]]:
    return [dict(zip(("chamber_label", "state_id", "state", "start_time",
                      "end_time", "minutes"), row))
            for row in read(connection, "v_chamber_state_intervals",
                            order_by="state_id",
                            where="chamber_label = ?", params=(chamber,))]


# ----------------------------------------------------------------- Yield


def yield_page(connection: sqlite3.Connection,
               report: Mapping[str, Any]) -> dict[str, Any]:
    lots = [dict(zip(("lot_id", "product_name", "wafers", "mean_yield_pct",
                      "mean_attainment_pts", "lost_die"), row))
            for row in connection.execute("""
                SELECT y.lot_id, y.product_name, COUNT(*),
                       AVG(y.yield_pct), AVG(y.attainment_pts),
                       SUM(y.total_die - y.good_die)
                FROM fact_yield y
                GROUP BY y.lot_id, y.product_name
                ORDER BY y.lot_id""")]
    lots.sort(key=lambda row: (row["mean_attainment_pts"], row["lot_id"]))
    return {
        "products": list(iter_dicts(connection, "v_product_attainment",
                                    order_by="product_name")),
        "daily": list(iter_dicts(connection, "v_yield_trend_daily",
                                 order_by="day_index")),
        "chamber_standing": report.get("measurements", {}).get(
            "yield", {}).get("chamber_yield_standing", []),
        "chamber_note": report.get("measurements", {}).get("yield", {}).get(
            "chamber_yield_note", ""),
        "lots": lots,
        "signals": [s for s in report.get("signals", ())
                    if s["family"] == "yield"],
    }


# -------------------------------------------------------- Wafer explorer


def wafer_index(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every tested wafer with enough context to choose one."""
    return [dict(zip(("wafer_id", "lot_id", "product_name", "yield_pct",
                      "attainment_pts", "defects"), row))
            for row in connection.execute("""
                SELECT y.wafer_id, y.lot_id, y.product_name, y.yield_pct,
                       y.attainment_pts,
                       (SELECT COUNT(*) FROM fact_defect d
                        WHERE d.wafer_id = y.wafer_id)
                FROM fact_yield y ORDER BY y.wafer_id""")]


def wafer_detail(connection: sqlite3.Connection, wafer_id: int
                 ) -> dict[str, Any]:
    """One wafer: its route, its defects, its die map and its number."""
    runs = [dict(zip(("run_id", "step_sequence", "step_name", "operation_type",
                      "tool_name", "chamber_label", "start_time", "end_time"),
                     row))
            for row in connection.execute("""
                SELECT run_id, step_sequence, step_name, operation_type,
                       tool_name, chamber_label, start_time, end_time
                FROM fact_wafer_step WHERE wafer_id = ?
                ORDER BY step_sequence, run_id""", (wafer_id,))]
    defects = [dict(zip(("defect_id", "x_mm", "y_mm", "size_um",
                         "classified_type", "layer", "radius_fraction"), row))
               for row in connection.execute("""
                   SELECT defect_id, x_mm, y_mm, size_um, classified_type,
                          layer, radius_fraction
                   FROM fact_defect WHERE wafer_id = ?
                   ORDER BY defect_id""", (wafer_id,))]
    die = [dict(zip(("die_x", "die_y", "bin_code"), row))
           for row in connection.execute("""
               SELECT die_x, die_y, bin_code FROM fact_die
               WHERE wafer_id = ? ORDER BY die_y, die_x""", (wafer_id,))]
    row = connection.execute("""
        SELECT product_name, yield_pct, attainment_pts, total_die, good_die,
               target_yield_pct FROM fact_yield WHERE wafer_id = ?
        ORDER BY yield_id""", (wafer_id,)).fetchone()
    radius = connection.execute("""
        SELECT p.wafer_size_mm / 2.0 FROM wafers w
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN products p ON p.product_id = l.product_id
        WHERE w.wafer_id = ? ORDER BY w.wafer_id""", (wafer_id,)).fetchone()
    return {
        "wafer_id": wafer_id,
        "product_name": row[0] if row else None,
        "yield_pct": row[1] if row else None,
        "attainment_pts": row[2] if row else None,
        "total_die": row[3] if row else None,
        "good_die": row[4] if row else None,
        "target_yield_pct": row[5] if row else None,
        "wafer_radius_mm": radius[0] if radius else 150.0,
        "runs": runs, "defects": defects, "die": die,
    }


# ------------------------------------------------------------- assembly


def load_workspace(db_path: Path | str) -> dict[str, Any]:
    """Everything the six pages need, from one dataset, in one pass.

    The engine, the monitors and the decision artifact are each run exactly
    once; the app never recomputes and never caches a stale result, which is
    the audited dashboard's other defect (`st.cache_data` that was never
    invalidated after a rebuild).
    """
    from fabops.report import build_report

    monitor_report = monitor(db_path).to_dict()
    decision = build_report(db_path).to_dict()
    connection = open_layer(db_path)
    try:
        return {
            "dataset": dataset_summary(connection),
            "monitor": monitor_report,
            "generated_by": {"monitors": MONITOR,
                             "engine": decision["provenance"]["engine"],
                             "report": decision["generated_by"]},
            "fab_today": fab_today(connection, monitor_report),
            "process": process_page(connection, monitor_report),
            "equipment": equipment_page(connection, monitor_report),
            "yield": yield_page(connection, monitor_report),
            "investigation": decision,
            "wafers": wafer_index(connection),
        }
    finally:
        connection.close()
