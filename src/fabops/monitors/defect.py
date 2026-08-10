"""
defect.py — what is the defect load doing, and what shape is it?

Two capabilities, and the second is the one the audit called this repository's
strongest verified asset:

* **movers** — a chamber's defect rate per wafer, per layer, charted daily
  against its same-role peers. "Which chamber's wafers got dirtier this week"
  is the question, and the peer difference is what stops a fab-wide inspection
  change from answering it.
* **spatial signature scoring, per wafer** — edge, centre, clustering and
  linearity, all computed from `(x_mm, y_mm)` and the wafer's own radius.

`classified_type` is deliberately not used for any geometric quantity. It is a
draw through a confusion matrix over a hidden origin (ADR-019 §4) — origin and
class disagree on more than 40% of defects — so a signature derived from the
class would be measuring the classifier. The class is reported as a Pareto
because an analyst reads it, and never mixed into a score.

The four signature scores are deliberately simple enough to recompute by hand:

    edge_share        fraction of the wafer's defects outside 0.80 R
    center_share      fraction inside 0.33 R
    clustering        Clark-Evans R: mean nearest-neighbour distance over the
                      distance expected under complete spatial randomness.
                      R < 1 is clumped, R = 1 random, R > 1 dispersed.
    linearity         the larger eigenvalue's share of the coordinate
                      covariance. 0.5 is isotropic; a scratch runs toward 1.
"""
from __future__ import annotations

import math
import sqlite3
import statistics as st
from collections import defaultdict

from fabops.monitors.model import (DEFAULT_CHART_RULE, Signal, order_signals,
                                   peer_difference, series_from, spc_signals)

__all__ = ["FAMILY", "monitor_defects", "clark_evans", "linearity"]

FAMILY = "defect"

#: A wafer with fewer defects than this has no shape worth scoring; the
#: nearest-neighbour statistic is dominated by its own sampling noise.
MIN_DEFECTS_FOR_SIGNATURE = 12

#: How many wafers each signature's leaderboard keeps. A report a human reads
#: is a report that fits on a screen; the full per-wafer table stays available
#: through the semantic layer.
LEADERBOARD = 10


def clark_evans(points: list[tuple[float, float]], radius_mm: float) -> float:
    """Mean nearest-neighbour distance over its expectation under randomness."""
    count = len(points)
    if count < 3 or radius_mm <= 0:
        return 1.0
    nearest = []
    for index, (x, y) in enumerate(points):
        best = math.inf
        for other, (u, v) in enumerate(points):
            if other == index:
                continue
            best = min(best, math.hypot(x - u, y - v))
        nearest.append(best)
    observed = st.fmean(nearest)
    density = count / (math.pi * radius_mm * radius_mm)
    expected = 0.5 / math.sqrt(density)
    return observed / expected if expected else 1.0


def linearity(points: list[tuple[float, float]]) -> float:
    """The larger principal eigenvalue's share of the coordinate covariance."""
    if len(points) < 3:
        return 0.5
    mean_x = st.fmean([x for x, _y in points])
    mean_y = st.fmean([y for _x, y in points])
    sxx = sum((x - mean_x) ** 2 for x, _y in points) / len(points)
    syy = sum((y - mean_y) ** 2 for _x, y in points) / len(points)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points) / len(points)
    trace, determinant = sxx + syy, sxx * syy - sxy * sxy
    root = math.sqrt(max(0.0, trace * trace / 4.0 - determinant))
    larger = trace / 2.0 + root
    return larger / trace if trace > 0 else 0.5


def _rate_signals(connection: sqlite3.Connection, horizon_days: int,
                  chart_rule: str) -> list[Signal]:
    """Defects per wafer per day, per chamber and layer, against peers."""
    roles = {label: role for label, role in connection.execute(
        "SELECT DISTINCT chamber_label, tool_type FROM fact_wafer_step "
        "ORDER BY chamber_label")}
    rows = connection.execute("""
        SELECT f.chamber_label, p.layer, p.day_index, p.defects
        FROM (SELECT DISTINCT chamber_label, wafer_id, operation_type
              FROM fact_wafer_step) f
        JOIN v_wafer_defect_profile p ON p.wafer_id = f.wafer_id
        ORDER BY f.chamber_label, p.layer, p.day_index""").fetchall()

    per_layer: dict[str, dict[str, dict[int, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list)))
    for label, layer, day, defects in rows:
        per_layer[layer][label][int(day)].append(float(defects))

    signals: list[Signal] = []
    for layer in sorted(per_layer):
        means = {label: {day: st.fmean(values)
                         for day, values in days.items()}
                 for label, days in per_layer[layer].items()}
        support = {label: {day: len(values)
                           for day, values in days.items()}
                   for label, days in per_layer[layer].items()}
        for series in series_from(peer_difference(means, roles), support,
                                  f"defects_per_wafer:{layer}"):
            signals.extend(spc_signals(series, horizon_days, family=FAMILY,
                                       entity_kind="chamber",
                                       chart_rule=chart_rule))
    return signals


def monitor_defects(connection: sqlite3.Connection, horizon_days: int,
                    chart_rule: str = DEFAULT_CHART_RULE
                    ) -> tuple[list[Signal], dict[str, object]]:
    signals = _rate_signals(connection, horizon_days, chart_rule)

    # --- per-wafer spatial signatures, from coordinates alone.
    coordinates: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(
        list)
    radii: dict[int, float] = {}
    for wafer, layer, x, y, radius in connection.execute("""
            SELECT d.wafer_id, d.layer, d.x_mm, d.y_mm, p.wafer_size_mm / 2.0
            FROM fact_defect d JOIN wafers w ON w.wafer_id = d.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN products p ON p.product_id = l.product_id
            ORDER BY d.defect_id"""):
        coordinates[(int(wafer), layer)].append((float(x), float(y)))
        radii[int(wafer)] = float(radius)

    profile = {(int(wafer), layer): (int(defects), float(edge), float(centre),
                                     float(mean_radius))
               for wafer, layer, defects, edge, centre, mean_radius
               in connection.execute(
                   "SELECT wafer_id, layer, defects, edge_share, "
                   "center_share, mean_radius_fraction "
                   "FROM v_wafer_defect_profile "
                   "ORDER BY wafer_id, layer")}

    scored: list[dict[str, object]] = []
    for key in sorted(profile):
        wafer, layer = key
        defects, edge, centre, mean_radius = profile[key]
        if defects < MIN_DEFECTS_FOR_SIGNATURE:
            continue
        points = coordinates.get(key, [])
        scored.append({
            "wafer_id": wafer, "layer": layer, "defects": defects,
            "edge_share": round(edge, 4), "center_share": round(centre, 4),
            "mean_radius_fraction": round(mean_radius, 4),
            "clustering": round(clark_evans(points, radii.get(wafer, 150.0)), 4),
            "linearity": round(linearity(points), 4),
        })

    def top(key: str, *, reverse: bool = True) -> list[dict[str, object]]:
        return sorted(scored,
                      key=lambda row: (-row[key] if reverse else row[key],
                                       row["wafer_id"], row["layer"])
                      )[:LEADERBOARD]

    pareto = [{"classified_type": name, "defects": count}
              for name, count in connection.execute(
                  "SELECT classified_type, COUNT(*) FROM fact_defect "
                  "GROUP BY classified_type ORDER BY COUNT(*) DESC, "
                  "classified_type")]

    measurements = {
        "wafers_scored": len(scored),
        "class_pareto": pareto,
        "signature_leaders": {
            "edge_share": top("edge_share"),
            "center_share": top("center_share"),
            "clustering": top("clustering", reverse=False),
            "linearity": top("linearity"),
        },
        "signature_note": (
            "every score is computed from defect coordinates and the wafer's "
            "own radius; `classified_type` is a noisy draw over a hidden "
            "origin and is reported as a Pareto only"),
    }
    return order_signals(signals), measurements
