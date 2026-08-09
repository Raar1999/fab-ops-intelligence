"""
queries.py — the reference analytical queries, over the observable plane only.

`PHASE_1_ACCEPTANCE.md` A6 asks whether each scenario's intended evidence is
*recoverable* from the emitted dataset, and L7/L11 ask whether the same
queries stay quiet on the null and rise with severity. Both questions are only
meaningful if the queries are answerable by someone who has the database and
nothing else — so every function here takes a **path to `fab.db`** and there
is no parameter by which truth could arrive. A test pins that.

These are *reference* queries, not a diagnosis engine. They compute the
engineering quantities an analyst would compute — a per-chamber yield split,
an edge-zone defect share, a CD deviation, a before/after-maintenance
contrast — and they rank chambers by them. They do not weigh evidence, do not
combine channels, do not decide anything, and do not know that a fault
exists. Turning them into a scorer is the next gate's work and would make this
one unable to grade it.

Everything is stdlib: `sqlite3` and arithmetic. `fabeval` does not need
pandas to compute a mean, and staying dependency-free keeps the evaluation
layer as portable as the thing it evaluates.
"""
from __future__ import annotations

import math
import sqlite3
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "EDGE_RADIUS_FRACTION",
    "ChamberScore",
    "alarm_counts",
    "chamber_edge_cd_deviation",
    "chamber_edge_defect_share",
    "chamber_yield_split",
    "maintenance_contrast",
    "metric_trend",
    "product_tool_exposure",
    "rank",
    "wafer_yields",
    "zscore",
]

#: Where "the edge" starts, as a fraction of the wafer radius. Declared once
#: here because every edge query must mean the same thing by it, and set to
#: 0.80 to match the world's own `defects.edge_ring.inner_fraction` of 0.80 —
#: the annulus the geometry actually places a ring in. Choosing it to make a
#: result come out would be choosing the answer.
EDGE_RADIUS_FRACTION = 0.80

#: The wafer radius every product in the Phase 1 world shares (300 mm). Read
#: from `products` per query rather than assumed; this is the fallback for a
#: dataset that somehow has no product row, which the self-test would already
#: have rejected.
_DEFAULT_RADIUS_MM = 150.0


def _connect(db_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def _rows(db_path: Path | str, sql: str,
          params: Sequence[Any] = ()) -> list[tuple]:
    connection = _connect(db_path)
    try:
        return connection.execute(sql, tuple(params)).fetchall()
    finally:
        connection.close()


@dataclass(frozen=True)
class ChamberScore:
    """One chamber's standing on one observable signal.

    `label` is `TOOL/CHAMBER` — the grain the audit said attribution has to
    reach, and the grain every query here reports at.
    """

    label: str
    value: float
    support: int


def zscore(scores: Mapping[str, float], label: str) -> float:
    """How far one chamber stands out from *the others* on one signal.

    Leave-one-out on purpose: including the chamber in its own reference
    spread shrinks exactly the deviation being measured, which flatters a
    strong signal and hides a weak one.
    """
    others = [value for name, value in scores.items() if name != label]
    if len(others) < 2:
        return 0.0
    spread = st.pstdev(others)
    return (scores[label] - st.mean(others)) / spread if spread else 0.0


def rank(scores: Mapping[str, float], label: str, *,
         descending: bool = True) -> int:
    """1-based rank of one chamber on one signal."""
    ordered = sorted(scores, key=lambda k: scores[k], reverse=descending)
    return ordered.index(label) + 1


# --------------------------------------------------------------- the queries


def wafer_yields(db_path: Path | str) -> list[tuple[int, str, float]]:
    """(wafer_id, product_name, yield_pct) for every tested wafer."""
    return [(w, p, y) for w, p, y in _rows(db_path, """
        SELECT y.wafer_id, p.product_name, y.yield_pct
        FROM wafer_yield y
        JOIN lots l ON l.lot_id = y.lot_id
        JOIN products p ON p.product_id = l.product_id""")]


def chamber_yield_split(db_path: Path | str, operation: str = "ETCH"
                        ) -> dict[str, ChamberScore]:
    """Within-product yield deficit of the wafers each chamber processed.

    "Which chamber's wafers yield worse than their peers?" — the classic
    commonality question, asked *within product* because products differ by up
    to ten yield points in this world and a raw cohort mean would mostly
    measure the product mix. That is not a sophistication: it is the minimum
    needed for the number to mean anything, and scenario G exists precisely
    because even this is not enough.
    """
    rows = _rows(db_path, """
        SELECT t.tool_name || '/' || c.chamber_name, p.product_name,
               y.wafer_id, y.yield_pct
        FROM wafer_yield y
        JOIN runs r ON r.wafer_id = y.wafer_id
        JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
        JOIN process_steps s ON s.step_id = f.step_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        JOIN lots l ON l.lot_id = y.lot_id
        JOIN products p ON p.product_id = l.product_id
        WHERE s.operation_type = ?""", (operation,))

    by_chamber_product: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_product: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for label, product, _wafer, value in rows:
        by_chamber_product[(label, product)].append(value)
        by_product[product].append((label, value))

    deficits: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for (label, product), values in by_chamber_product.items():
        peers = [v for other, v in by_product[product] if other != label]
        if len(values) < 5 or len(peers) < 20:
            continue
        deficits[label].append((len(values),
                                st.mean(values) - st.mean(peers)))
    return {
        label: ChamberScore(
            label=label,
            value=sum(n * d for n, d in parts) / sum(n for n, _d in parts),
            support=sum(n for n, _d in parts))
        for label, parts in deficits.items()}


def chamber_edge_defect_share(db_path: Path | str, layer: str = "GATE",
                              operation: str = "ETCH"
                              ) -> dict[str, ChamberScore]:
    """Share of a chamber's defects landing in the outer annulus.

    Geometry, never a label: the share is computed from `(x_mm, y_mm)` and the
    product's own wafer radius. `classified_type` is deliberately not read —
    it is a noisy draw over the hidden origin (ADR-019 §4), and a query that
    trusted it would be measuring the classifier.
    """
    rows = _rows(db_path, """
        SELECT t.tool_name || '/' || c.chamber_name, d.x_mm, d.y_mm,
               pr.wafer_size_mm
        FROM defects d
        JOIN runs r ON r.wafer_id = d.wafer_id
        JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
        JOIN process_steps s ON s.step_id = f.step_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        JOIN wafers w ON w.wafer_id = d.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN products pr ON pr.product_id = l.product_id
        WHERE s.operation_type = ? AND d.layer = ?""", (operation, layer))

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for label, x_mm, y_mm, wafer_size in rows:
        radius = (wafer_size or 2 * _DEFAULT_RADIUS_MM) / 2.0
        bucket = counts[label]
        bucket[1] += 1
        if math.hypot(x_mm, y_mm) >= EDGE_RADIUS_FRACTION * radius:
            bucket[0] += 1
    return {label: ChamberScore(label, outer / total, total)
            for label, (outer, total) in counts.items() if total >= 200}


def chamber_edge_cd_deviation(db_path: Path | str, param: str = "cd_nm_edge"
                              ) -> dict[str, ChamberScore]:
    """Mean |measured − recipe target| at one metrology zone, per chamber.

    Referenced to the *recipe's* target, which is per product and which a
    fault never moves — so this is "how far off spec did this chamber run?"
    and not "how did this chamber differ from the fab today?".
    """
    rows = _rows(db_path, f"""
        SELECT t.tool_name || '/' || c.chamber_name, m.value, rc.metric_target
        FROM metrology m
        JOIN flow_steps f ON f.flow_step_id = m.flow_step_id
        JOIN runs r ON r.wafer_id = m.wafer_id
                   AND r.flow_step_id = m.flow_step_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        JOIN wafers w ON w.wafer_id = m.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN recipes rc ON rc.step_id = f.step_id
                       AND rc.product_id = l.product_id
        WHERE m.param_name = ?""", (param,))
    values: dict[str, list[float]] = defaultdict(list)
    for label, value, target in rows:
        if target:
            values[label].append(abs(value - target) / target)
    return {label: ChamberScore(label, st.mean(v), len(v))
            for label, v in values.items() if len(v) >= 20}


def metric_trend(db_path: Path | str, cut: str, param: str = "cd_nm_center"
                 ) -> dict[str, ChamberScore]:
    """Late-window mean minus early-window mean, per chamber.

    A difference in differences: every chamber shares the fab-week wander, so
    comparing each chamber's own shift against its peers' cancels it and
    leaves whatever moved on its own. `cut` is a timestamp the caller chooses
    — for a ramped mechanism the honest choice is its onset, not whichever
    boundary maximizes the result.
    """
    rows = _rows(db_path, f"""
        SELECT t.tool_name || '/' || c.chamber_name, m.meas_time, m.value,
               rc.metric_target
        FROM metrology m
        JOIN flow_steps f ON f.flow_step_id = m.flow_step_id
        JOIN runs r ON r.wafer_id = m.wafer_id
                   AND r.flow_step_id = m.flow_step_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        JOIN wafers w ON w.wafer_id = m.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN recipes rc ON rc.step_id = f.step_id
                       AND rc.product_id = l.product_id
        WHERE m.param_name = ?""", (param,))
    early: dict[str, list[float]] = defaultdict(list)
    late: dict[str, list[float]] = defaultdict(list)
    for label, when, value, target in rows:
        if not target:
            continue
        (late if when >= cut else early)[label].append((value - target) / target)
    return {label: ChamberScore(
                label, st.mean(late[label]) - st.mean(early[label]),
                len(early[label]) + len(late[label]))
            for label in early
            if len(early[label]) >= 10 and len(late.get(label, ())) >= 10}


def alarm_counts(db_path: Path | str, prefix: str | None = None
                 ) -> dict[str, ChamberScore]:
    """Coded alarms per chamber. Codes only; the message text is templated."""
    rows = _rows(db_path, """
        SELECT t.tool_name || '/' || c.chamber_name, COUNT(*)
        FROM alarms a
        JOIN chambers c ON c.chamber_id = a.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        GROUP BY 1""")
    return {label: ChamberScore(label, float(count), count)
            for label, count in rows
            if prefix is None or label.startswith(prefix)}


def maintenance_contrast(db_path: Path | str, chamber_label: str,
                         layer: str = "METAL") -> dict[str, Any]:
    """Defect rate on one chamber's wafers before and after its first repair.

    Scenario I's question — "did behaviour change after maintenance?" — asked
    the way an analyst would: split the chamber's own inspections at its first
    unscheduled window and compare, with the rest of the fab over the same two
    stretches as a control so a fab-wide drift cannot masquerade as a repair
    effect.
    """
    tool, _, chamber = chamber_label.partition("/")
    repairs = _rows(db_path, """
        SELECT m.start_time, m.end_time FROM maintenance m
        JOIN chambers c ON c.chamber_id = m.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        WHERE t.tool_name = ? AND c.chamber_name = ?
          AND m.maint_type = 'UNSCHEDULED'
        ORDER BY m.start_time""", (tool, chamber))
    if not repairs:
        return {"repairs": 0}

    rows = _rows(db_path, """
        SELECT i.inspection_time, i.total_defect_count,
               EXISTS(SELECT 1 FROM runs r
                      JOIN chambers c2 ON c2.chamber_id = r.chamber_id
                      JOIN tools t2 ON t2.tool_id = c2.tool_id
                      WHERE r.wafer_id = i.wafer_id
                        AND t2.tool_name = ? AND c2.chamber_name = ?)
        FROM inspections i
        WHERE EXISTS(SELECT 1 FROM defects d
                     WHERE d.inspection_id = i.inspection_id
                       AND d.layer = ?)""", (tool, chamber, layer))

    def mean_of(exposed: bool, before: bool, boundary: str) -> float | None:
        values = [n for when, n, flag in rows
                  if bool(flag) is exposed and ((when < boundary) is before)]
        return st.mean(values) if len(values) >= 5 else None

    boundary = repairs[0][1]
    return {
        "repairs": len(repairs),
        "first_repair_end": boundary,
        "exposed_before": mean_of(True, True, boundary),
        "exposed_after": mean_of(True, False, boundary),
        "control_before": mean_of(False, True, boundary),
        "control_after": mean_of(False, False, boundary),
    }


def product_tool_exposure(db_path: Path | str, start: str, end: str,
                          operation: str = "ETCH"
                          ) -> dict[str, dict[str, dict[str, float]]]:
    """Each product's tool mix inside and outside a window, from `runs`.

    The confounder of scenario G is *observable data* (ADR-015): the routing
    shift is in the run log and a diagnosis engine is expected to see it and
    control for it. This is the query that sees it.
    """
    rows = _rows(db_path, """
        SELECT p.product_name, t.tool_name, r.start_time
        FROM runs r
        JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
        JOIN process_steps s ON s.step_id = f.step_id
        JOIN tools t ON t.tool_id = r.tool_id
        JOIN wafers w ON w.wafer_id = r.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN products p ON p.product_id = l.product_id
        WHERE s.operation_type = ?""", (operation,))
    inside: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    outside: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for product, tool, when in rows:
        bucket = inside if start <= when < end else outside
        bucket[product][tool] += 1

    def shares(bucket: Mapping[str, Mapping[str, int]]
               ) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for product, tools in bucket.items():
            total = sum(tools.values())
            out[product] = {tool: count / total
                            for tool, count in tools.items()} if total else {}
            out[product]["_runs"] = float(total)
        return out

    return {"inside": shares(inside), "outside": shares(outside)}
