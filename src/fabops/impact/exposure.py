"""
exposure.py — which material depended on this entity, and how much.

Containment is a ranking problem, not a detection one: given a subject an
engineer wants to act on, which lots carry enough exposure to be worth holding
or re-inspecting. The answer comes entirely from the run log — "which wafer met
which chamber at which step" is what `fact_wafer_step` is.

Two things this deliberately does *not* do. It does not decide whether the
subject is guilty — the subject is supplied, by the engine or by a human, and
this module quantifies the consequence of acting on it. And it does not weight
a lot by anything other than its own dependence: a lot that ran 24 of its 25
wafers through the subject is more exposed than one that ran 3, whatever either
lot yielded, because a containment decision is about material at risk rather
than about material already measured.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["LotExposure", "SUBJECT_KINDS", "lot_exposure", "exposed_wafers",
           "subject_predicate"]

#: The entity kinds exposure can be computed for. Both are columns of the run
#: log; a kind that is not observable exposure is not a containment subject.
SUBJECT_KINDS = ("chamber", "tool")

_COLUMN = {"chamber": "chamber_label", "tool": "tool_name"}


def subject_predicate(kind: str) -> str:
    if kind not in SUBJECT_KINDS:
        raise ValueError(f"unknown subject kind {kind!r}; expected one of "
                         f"{list(SUBJECT_KINDS)}")
    return _COLUMN[kind]


@dataclass(frozen=True)
class LotExposure:
    lot_id: int
    product_name: str
    lot_wafers: int
    exposed_wafers: int

    @property
    def share(self) -> float:
        return self.exposed_wafers / self.lot_wafers if self.lot_wafers else 0.0

    def to_dict(self) -> dict[str, object]:
        return {"lot_id": self.lot_id, "product_name": self.product_name,
                "lot_wafers": self.lot_wafers,
                "exposed_wafers": self.exposed_wafers,
                "share": round(self.share, 4)}


def exposed_wafers(connection: sqlite3.Connection, kind: str, entity: str,
                   step_name: str | None = None) -> tuple[int, ...]:
    """Every wafer that met this entity, at one step or at any of them."""
    column = subject_predicate(kind)
    clause = " AND step_name = ?" if step_name else ""
    params: tuple = (entity,) if step_name is None else (entity, step_name)
    return tuple(row[0] for row in connection.execute(
        f"SELECT DISTINCT wafer_id FROM fact_wafer_step "
        f"WHERE {column} = ?{clause} ORDER BY wafer_id", params))


def lot_exposure(connection: sqlite3.Connection, kind: str, entity: str,
                 step_name: str | None = None) -> list[LotExposure]:
    """Lots ranked by their dependence on the entity, most exposed first."""
    column = subject_predicate(kind)
    clause = " AND f.step_name = ?" if step_name else ""
    params: tuple = (entity,) if step_name is None else (entity, step_name)
    rows = connection.execute(f"""
        SELECT f.lot_id, f.product_name,
               COUNT(DISTINCT f.wafer_id) AS exposed,
               (SELECT COUNT(*) FROM wafers w WHERE w.lot_id = f.lot_id)
        FROM fact_wafer_step f
        WHERE f.{column} = ?{clause}
        GROUP BY f.lot_id, f.product_name
        ORDER BY f.lot_id""", params).fetchall()
    exposures = [LotExposure(lot_id=int(lot), product_name=str(product),
                             lot_wafers=int(total), exposed_wafers=int(exposed))
                 for lot, product, exposed, total in rows]
    exposures.sort(key=lambda row: (-row.share, -row.exposed_wafers,
                                    row.lot_id))
    return exposures
