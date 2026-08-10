"""
loss.py — what did this cost, and is that number distinguishable from nothing?

The counterfactual an engineer wants is "how much good die would we have had if
the wafers that ran through this chamber had run through its peers instead". The
audited v1 answered a version of that and answered it correctly; what it could
not do is answer it for *any* subject, honestly weighted, with an interval.

Three properties are what make this module's number worth printing.

**Within product, always.** Products in this world differ by up to ten yield
points, so a raw exposed-versus-rest comparison mostly measures which products
the subject happened to run. Each product is compared with itself and the
per-product differences are pooled by exposed wafer count.

**With an interval, always.** The estimate ships with the standard error of the
difference in means. A loss estimate without one invites a reader to treat 0.4
points and 4 points as the same kind of fact.

**Against the benign spread, always.** ADR-028 measured that on twelve
fault-free worlds each etch tool is the worst one on cohort yield about a third
of the time, and that the between-tool benign spread on this channel is 0.410
points against a mechanism-attributable effect of 0.058. So the subject's
*standing among its peers* is reported beside the loss, and a caller that
prints the die count without it is printing benign variation with a dollar sign
in front. `distinguishable` is that judgement, made once, here.

The estimator is deliberately the same one the semantic layer's
`v_chamber_yield_deficit` uses, so a reader who checks the view and a reader who
reads this module get the same number.
"""
from __future__ import annotations

import math
import sqlite3
import statistics as st
from collections import defaultdict
from dataclasses import dataclass, field

from fabops.impact.exposure import subject_predicate

__all__ = ["ImpactEstimate", "MIN_EXPOSED_WAFERS", "MIN_PEER_WAFERS",
           "estimate_loss"]

#: The same support floors the semantic layer's deficit view applies. A chamber
#: that ran four wafers of a product has no cohort, and pretending otherwise is
#: how a two-wafer coincidence becomes a containment decision.
MIN_EXPOSED_WAFERS = 5
MIN_PEER_WAFERS = 20

#: How far the subject's standing must sit from its peers' before the estimate
#: is called distinguishable from benign variation. The fab's own control-limit
#: convention, borrowed for the same reason ADR-027 §2 borrowed it: this is an
#: *action* threshold — somebody holds material because of it.
STANDING_LIMIT = 3.0


@dataclass(frozen=True)
class ImpactEstimate:
    subject_kind: str
    subject: str
    step_name: str | None
    exposed_wafers: int
    peer_wafers: int
    deficit_pts: float
    standard_error_pts: float
    exposed_die: int
    die_delta: float
    optimistic_recoverable_die: float
    standing_z: float
    peers_compared: int
    per_product: tuple[dict[str, object], ...] = ()

    @property
    def distinguishable(self) -> bool:
        """Is the subject's standing outside what benign variation produces?"""
        return abs(self.standing_z) >= STANDING_LIMIT

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": {"kind": self.subject_kind, "id": self.subject},
            "step_name": self.step_name,
            "exposed_wafers": self.exposed_wafers,
            "peer_wafers": self.peer_wafers,
            "within_product_deficit_pts": round(self.deficit_pts, 4),
            "standard_error_pts": round(self.standard_error_pts, 4),
            "exposed_die": self.exposed_die,
            "estimated_die_delta": round(self.die_delta, 1),
            "optimistic_recoverable_die": round(
                self.optimistic_recoverable_die, 1),
            "standing_z_among_peers": round(self.standing_z, 3),
            "peers_compared": self.peers_compared,
            "distinguishable_from_benign_variation": self.distinguishable,
            "per_product": list(self.per_product),
            "note": ("a negative die delta is a shortfall against the "
                     "subject's own within-product peers; the optimistic "
                     "figure compares against the best peer and is an upper "
                     "bound, not an estimate"),
        }


def _cohorts(connection: sqlite3.Connection, kind: str, entity: str,
             step_name: str | None):
    """(product -> exposed rows, product -> peer rows, per-peer-entity rows)."""
    column = subject_predicate(kind)
    clause = " AND f.step_name = ?" if step_name else ""
    params: tuple = () if step_name is None else (step_name,)
    rows = connection.execute(f"""
        SELECT f.{column}, f.product_name, f.wafer_id, y.yield_pct,
               y.total_die
        FROM (SELECT DISTINCT {column}, product_name, wafer_id, step_name
              FROM fact_wafer_step) f
        JOIN fact_yield y ON y.wafer_id = f.wafer_id
        WHERE 1 = 1{clause}
        ORDER BY f.wafer_id, f.{column}""", params).fetchall()

    exposed_wafers = {wafer for label, _p, wafer, _y, _d in rows
                      if label == entity}
    exposed: dict[str, list[tuple[float, int]]] = defaultdict(list)
    peers: dict[str, list[tuple[float, int]]] = defaultdict(list)
    by_peer: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list))
    seen: set[tuple[str, int]] = set()
    for label, product, wafer, yield_pct, total_die in rows:
        key = (label, int(wafer))
        if key in seen:
            continue
        seen.add(key)
        if label == entity:
            exposed[product].append((float(yield_pct), int(total_die)))
        elif int(wafer) not in exposed_wafers:
            peers[product].append((float(yield_pct), int(total_die)))
            by_peer[label][product].append(float(yield_pct))
    return exposed, peers, by_peer


def _standing(connection: sqlite3.Connection, kind: str, entity: str,
              step_name: str | None) -> tuple[float, int]:
    """Leave-one-out z of the subject's deficit among the other entities.

    Read from the semantic layer's own view so the reference and the estimate
    cannot be two different definitions of the same quantity.
    """
    if kind != "chamber" or step_name is None:
        return 0.0, 0
    rows = connection.execute(
        "SELECT chamber_label, deficit_pts FROM v_chamber_yield_deficit "
        "WHERE step_name = ? ORDER BY chamber_label", (step_name,)).fetchall()
    values = {label: float(value) for label, value in rows}
    if entity not in values:
        return 0.0, max(0, len(values) - 1)
    others = [value for label, value in values.items() if label != entity]
    if len(others) < 2:
        return 0.0, len(others)
    spread = st.pstdev(others)
    if spread <= 0:
        return 0.0, len(others)
    return (values[entity] - st.fmean(others)) / spread, len(others)


def estimate_loss(connection: sqlite3.Connection, kind: str, entity: str,
                  step_name: str | None = None) -> ImpactEstimate:
    """The within-product yield consequence of one subject's exposure."""
    exposed, peers, by_peer = _cohorts(connection, kind, entity, step_name)

    weighted_deficit = 0.0
    weight_total = 0
    variance = 0.0
    exposed_die = 0
    die_delta = 0.0
    optimistic = 0.0
    peer_total = 0
    per_product: list[dict[str, object]] = []

    for product in sorted(exposed):
        mine = exposed[product]
        theirs = peers.get(product, [])
        peer_total += len(theirs)
        if len(mine) < MIN_EXPOSED_WAFERS or len(theirs) < MIN_PEER_WAFERS:
            continue
        my_yields = [value for value, _die in mine]
        their_yields = [value for value, _die in theirs]
        deficit = st.fmean(my_yields) - st.fmean(their_yields)
        error = math.sqrt(st.variance(my_yields) / len(my_yields)
                          + st.variance(their_yields) / len(their_yields))
        die = sum(die for _value, die in mine)

        best = None
        for label, products in by_peer.items():
            values = products.get(product, [])
            if len(values) >= MIN_EXPOSED_WAFERS:
                mean = st.fmean(values)
                if best is None or mean > best:
                    best = mean
        gap = (best - st.fmean(my_yields)) if best is not None else 0.0

        weighted_deficit += len(mine) * deficit
        weight_total += len(mine)
        variance += (len(mine) ** 2) * (error ** 2)
        exposed_die += die
        die_delta += die * deficit / 100.0
        optimistic += die * max(0.0, gap) / 100.0
        per_product.append({
            "product_name": product, "exposed_wafers": len(mine),
            "peer_wafers": len(theirs),
            "exposed_mean_yield_pct": round(st.fmean(my_yields), 4),
            "peer_mean_yield_pct": round(st.fmean(their_yields), 4),
            "deficit_pts": round(deficit, 4),
            "standard_error_pts": round(error, 4),
            "exposed_die": die})

    deficit = weighted_deficit / weight_total if weight_total else 0.0
    error = math.sqrt(variance) / weight_total if weight_total else 0.0
    standing, compared = _standing(connection, kind, entity, step_name)

    return ImpactEstimate(
        subject_kind=kind, subject=entity, step_name=step_name,
        exposed_wafers=weight_total, peer_wafers=peer_total,
        deficit_pts=deficit, standard_error_pts=error,
        exposed_die=exposed_die, die_delta=die_delta,
        optimistic_recoverable_die=optimistic, standing_z=standing,
        peers_compared=compared, per_product=tuple(per_product))
