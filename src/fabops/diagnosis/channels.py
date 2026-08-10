"""
channels.py — the evidence plane: what the fab measured, minus what it asked for.

Every channel yields **residuals**: an observation minus the fab's own
reference for it. For a process summary that reference is the setpoint the
recipe recorded on the same row; for a post-process measurement it is the
recipe's declared metric target. Subtracting it is what removes product mix,
because the reference is per (product, step) — and the variation stack of this
world is absolute in a channel's own scale, so subtracting is the whole of the
normalization and dividing would put product mix back in.

Two properties are load-bearing and neither is an optimization:

* **An observation carries every entity it is unambiguously attributable to.**
  A process reading indicts a chamber, a tool, a recipe, a step, a product and
  an operator simultaneously, and which of those is responsible is exactly
  what the investigation is for. A channel that could only speak about
  equipment would answer "which chamber?" to a question whose answer is a
  recipe.
* **An observation carries no entity it is only *plausibly* attributable to.**
  A defect found at an inspection is attributable to the chambers the wafer
  actually visited, not to one of them; so the defect channels carry chamber
  and tool and product, and deliberately not step, recipe or operator.

This module reads one database. It opens nothing else, derives no path, and
knows no entity name.

**Every multi-row query here carries a total `ORDER BY`, and that is a
correctness requirement rather than tidiness.** SQL licenses a database to
return rows in any order unless the query states one, and every value below is
reached by accumulating those rows in float: a bin mean, a product mean, a
per-wafer defect share. Two orders of the same rows therefore give two answers
differing in their last bits, and the decision plane downstream turns values
into *ranks*, where a last-bit difference is a tie broken by whichever plan the
query planner chose. Measured before this was fixed: reversing the row order of
one dataset moved 383 of the peer-differenced series while leaving the report
intact — a defect that was real, latent, and would have surfaced as an
unreproducible rank the first time two candidates sat close together. The order
is always a primary key, because ordering on a non-unique column leaves the
ties back where they started.
"""
from __future__ import annotations

import math
import sqlite3
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

__all__ = [
    "EDGE_RADIUS_FRACTION",
    "FAMILIES",
    "NO_ROLE",
    "Observation",
    "family_of",
    "load_observations",
    "read_meta",
    "read_roles",
]

#: The stratum an entity kind falls in when its members share no role
#: distinction - every product is compared with every other product.
NO_ROLE = ""

#: Where "the edge" starts, as a fraction of the wafer radius. Declared once
#: so every geometric channel means the same thing by it.
EDGE_RADIUS_FRACTION = 0.80

#: The evidence families. A family groups channels that read the same physics,
#: so they are dependent within and near-independent across — which is what
#: makes combining across families defensible and combining across channels
#: inside one family not.
FAMILIES = ("metrology", "fdc", "defects", "yield", "alarms")

_PREFIX_FAMILY = MappingProxyType(
    {"met": "metrology", "fdc": "fdc", "def": "defects",
     "yld": "yield", "alm": "alarms"})


def family_of(channel: str) -> str:
    prefix = channel.split(":", 1)[0]
    try:
        return _PREFIX_FAMILY[prefix]
    except KeyError:                                    # pragma: no cover
        raise ValueError(f"channel {channel!r} declares no evidence family")


@dataclass(frozen=True)
class Observation:
    """One residual, with every entity it is unambiguously attributable to."""

    channel: str
    day: float
    value: float
    keys: Mapping[str, str]


@dataclass(frozen=True)
class Meta:
    dataset_id: str
    time_origin: datetime
    horizon_days: float


def read_meta(connection: sqlite3.Connection) -> Meta:
    row = connection.execute(
        "SELECT dataset_id, time_origin, horizon_days FROM dataset_meta"
    ).fetchone()
    if row is None:                                     # pragma: no cover
        raise ValueError("this database has no dataset_meta row; it is not a "
                         "schema v2 observable dataset")
    return Meta(dataset_id=row[0],
                time_origin=datetime.fromisoformat(row[1]),
                horizon_days=float(row[2]))


def _days(origin: datetime, stamp: str) -> float:
    return (datetime.fromisoformat(stamp) - origin).total_seconds() / 86400.0


def load_observations(connection: sqlite3.Connection,
                      meta: Meta) -> tuple[Observation, ...]:
    """Every declared channel, from the observable tables alone."""
    origin = meta.time_origin
    out: list[Observation] = []

    # ---- process summaries: value minus the setpoint the recipe recorded.
    for (chamber, tool, product, recipe, step, operator, when,
         param, value, reference) in connection.execute("""
            SELECT t.tool_name || '/' || c.chamber_name, t.tool_name,
                   p.product_name, rc.recipe_name, s.step_name, o.operator_name,
                   r.start_time, rm.param_name, rm.value, rm.set_value
            FROM run_measurements rm
            JOIN runs r ON r.run_id = rm.run_id
            JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
            JOIN process_steps s ON s.step_id = f.step_id
            JOIN chambers c ON c.chamber_id = r.chamber_id
            JOIN tools t ON t.tool_id = c.tool_id
            JOIN recipes rc ON rc.recipe_id = r.recipe_id
            JOIN operators o ON o.operator_id = r.operator_id
            JOIN wafers w ON w.wafer_id = r.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN products p ON p.product_id = l.product_id
            ORDER BY rm.run_meas_id"""):
        out.append(Observation(
            channel=f"fdc:{param}", day=_days(origin, when),
            value=value - reference,
            keys={"chamber": chamber, "tool": tool, "product": product,
                  "recipe": recipe, "step": step, "operator": operator}))

    # ---- post-process measurements: value minus the recipe's metric target,
    #      attributed to the chamber that ran the *measured* step.
    zones: dict[tuple[int, int], dict[str, float]] = {}
    zone_keys: dict[tuple[int, int], tuple[Mapping[str, str], float]] = {}
    for (chamber, tool, product, recipe, step, operator, when, param,
         value, target, wafer, flow_step) in connection.execute("""
            SELECT t.tool_name || '/' || c.chamber_name, t.tool_name,
                   p.product_name, rc.recipe_name, s.step_name, o.operator_name,
                   m.meas_time, m.param_name, m.value, rc.metric_target,
                   m.wafer_id, m.flow_step_id
            FROM metrology m
            JOIN flow_steps f ON f.flow_step_id = m.flow_step_id
            JOIN process_steps s ON s.step_id = f.step_id
            JOIN runs r ON r.wafer_id = m.wafer_id
                       AND r.flow_step_id = m.flow_step_id
            JOIN chambers c ON c.chamber_id = r.chamber_id
            JOIN tools t ON t.tool_id = c.tool_id
            JOIN operators o ON o.operator_id = r.operator_id
            JOIN wafers w ON w.wafer_id = m.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN products p ON p.product_id = l.product_id
            JOIN recipes rc ON rc.step_id = f.step_id
                           AND rc.product_id = l.product_id
            ORDER BY m.metrology_id"""):
        day = _days(origin, when)
        keys = {"chamber": chamber, "tool": tool, "product": product,
                "recipe": recipe, "step": step, "operator": operator}
        residual = value - target if target is not None else value
        out.append(Observation(channel=f"met:{param}", day=day,
                               value=residual, keys=keys))
        _base, _sep, zone = param.rpartition("_")
        zones.setdefault((wafer, flow_step), {})[zone] = value
        zone_keys[(wafer, flow_step)] = (keys, day)

    # ---- derived: the within-wafer radial contrast. Both readings share the
    #      wafer, the lot, the week, the tool, the chamber and the recipe
    #      target, so all of those cancel and no reference is needed. It is a
    #      schema-level derivation - the zone list is `metrology.param_name` -
    #      and it names no physics.
    for key, by_zone in zones.items():
        if "edge" in by_zone and "center" in by_zone:
            keys, day = zone_keys[key]
            out.append(Observation(channel="met:cd_nm_radial", day=day,
                                   value=by_zone["edge"] - by_zone["center"],
                                   keys=keys))

    # ---- defects and yield: attributable to the equipment a wafer actually
    #      visited, and to its product; not to one step among many.
    radius = {name: size / 2.0 for name, size in connection.execute(
        "SELECT product_name, wafer_size_mm FROM products "
        "ORDER BY product_id")}
    wafer_product: dict[int, str] = {}
    wafer_equipment: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for wafer, chamber, tool, product in connection.execute("""
            SELECT r.wafer_id, t.tool_name || '/' || c.chamber_name,
                   t.tool_name, p.product_name
            FROM runs r
            JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
            JOIN process_steps s ON s.step_id = f.step_id
            JOIN chambers c ON c.chamber_id = r.chamber_id
            JOIN tools t ON t.tool_id = c.tool_id
            JOIN wafers w ON w.wafer_id = r.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN products p ON p.product_id = l.product_id
            WHERE s.is_inspection = 0
            ORDER BY r.run_id"""):
        wafer_equipment[wafer].append((chamber, tool))
        wafer_product[wafer] = product

    inspection_time = dict(connection.execute(
        "SELECT inspection_id, inspection_time FROM inspections "
        "ORDER BY inspection_id"))
    inspection_area = dict(connection.execute(
        "SELECT inspection_id, scan_area_mm2 FROM inspections "
        "ORDER BY inspection_id"))
    counted: dict[tuple[int, int, str], list[int]] = defaultdict(
        lambda: [0, 0])
    for inspection, wafer, x_mm, y_mm, layer in connection.execute(
            "SELECT inspection_id, wafer_id, x_mm, y_mm, layer FROM defects "
            "ORDER BY defect_id"):
        limit = EDGE_RADIUS_FRACTION * radius.get(
            wafer_product.get(wafer, ""), 150.0)
        bucket = counted[(inspection, wafer, layer)]
        bucket[1] += 1
        if math.hypot(x_mm, y_mm) >= limit:
            bucket[0] += 1

    for (inspection, wafer, layer), (outer, total) in counted.items():
        day = _days(origin, inspection_time[inspection])
        area = inspection_area.get(inspection) or 1.0
        product = wafer_product.get(wafer, "")
        for chamber, tool in wafer_equipment.get(wafer, ()):
            keys = {"chamber": chamber, "tool": tool, "product": product}
            out.append(Observation(f"def:rate:{layer}", day,
                                   total / area, keys))
            if total >= 5:
                out.append(Observation(f"def:edge_share:{layer}", day,
                                       outer / total, keys))

    yields = list(connection.execute("""
        SELECT y.wafer_id, p.product_name, y.yield_pct, y.test_time
        FROM wafer_yield y
        JOIN lots l ON l.lot_id = y.lot_id
        JOIN products p ON p.product_id = l.product_id
        ORDER BY y.yield_id"""))
    by_product: dict[str, list[float]] = defaultdict(list)
    for _wafer, product, value, _when in yields:
        by_product[product].append(value)
    product_mean = {k: st.mean(v) for k, v in by_product.items()}
    for wafer, product, value, when in yields:
        day = _days(origin, when)
        residual = value - product_mean[product]
        for chamber, tool in wafer_equipment.get(wafer, ()):
            out.append(Observation("yld:wafer_yield", day, residual,
                                   {"chamber": chamber, "tool": tool,
                                    "product": product}))

    # ---- alarms: coded point events on a chamber. Counted per bin later.
    for chamber, tool, when in connection.execute("""
            SELECT t.tool_name || '/' || c.chamber_name, t.tool_name,
                   a.alarm_time
            FROM alarms a
            JOIN chambers c ON c.chamber_id = a.chamber_id
            JOIN tools t ON t.tool_id = c.tool_id
            ORDER BY a.alarm_id"""):
        out.append(Observation("alm:count", _days(origin, when), 1.0,
                               {"chamber": chamber, "tool": tool}))

    return tuple(out)


def channels_present(observations: Iterable[Observation]) -> tuple[str, ...]:
    return tuple(sorted({o.channel for o in observations}))


def is_count_channel(channel: str) -> bool:
    """A channel whose bin value is how many events fell in it."""
    return channel.startswith("alm:")


def read_roles(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Which entities are comparable with which, per kind.

    The permutation null rests on candidates being exchangeable under the null
    hypothesis, and equipment is exchangeable within a *role* rather than
    across a fab: a chamber every wafer passes through and a chamber a third of
    them reach are not draws from one distribution. Ranking them against each
    other made the engine fire on 20% of fault-free worlds at a nominal 5%;
    grouping them by the role the tool table already records brought it to the
    declared level.

    The role comes from `tools.tool_type`, which is ordinary observable data.
    Kinds with no role distinction get one stratum.
    """
    chamber: dict[str, str] = {}
    tool: dict[str, str] = {}
    for tool_name, chamber_name, tool_type in connection.execute("""
            SELECT t.tool_name, c.chamber_name, t.tool_type
            FROM chambers c JOIN tools t ON t.tool_id = c.tool_id
            ORDER BY c.chamber_id"""):
        chamber[f"{tool_name}/{chamber_name}"] = tool_type
        tool[tool_name] = tool_type
    return {"chamber": chamber, "tool": tool}
