"""
fabops.impact — what a subject cost, and which material depended on it.

    with open_layer(db_path) as connection:
        estimate = estimate_loss(connection, "chamber", label, "GATE_ETCH")
        holds    = lot_exposure(connection, "chamber", label, "GATE_ETCH")

Decision support, downstream of a conclusion rather than part of reaching one.
The subject is always **supplied** — by `fabops.diagnosis`, or by an engineer
with a hypothesis of their own — and this package quantifies the consequence of
acting on it. Nothing here ranks candidates, and nothing here decides that a
subject is guilty; that separation is what lets an engineer ask "what would this
cost if it were true" without the tool pretending the answer to a different
question.
"""
from __future__ import annotations

from fabops.impact.exposure import (SUBJECT_KINDS, LotExposure, exposed_wafers,
                                    lot_exposure)
from fabops.impact.loss import (MIN_EXPOSED_WAFERS, MIN_PEER_WAFERS,
                                STANDING_LIMIT, ImpactEstimate, estimate_loss)

__all__ = [
    "IMPACT_VERSION",
    "ImpactEstimate",
    "LotExposure",
    "MIN_EXPOSED_WAFERS",
    "MIN_PEER_WAFERS",
    "STANDING_LIMIT",
    "SUBJECT_KINDS",
    "estimate_loss",
    "exposed_wafers",
    "lot_exposure",
]

#: Moves whenever an estimator changes — anything that could alter the number a
#: fixed database produces.
IMPACT_VERSION = "1.0.0"
