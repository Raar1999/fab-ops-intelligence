"""
anchors.py — the shared change points, declared rather than discovered.

This module exists to make two properties structural.

**A candidate's evidence is evaluated at change points it was not consulted
about.** Letting each candidate pick its own is what the architecture gate
rejected on measurement: a candidate that maximizes over its own splits wins
that maximization on the strength of its own ordinary wander, and the effect
grows with the length of the record, so a benign entity beats a faulted one
more often the longer you watch.

**And the anchor is not chosen from the data either.** That is the harder
lesson and it was learned during implementation rather than before it. A
change point read out of the fab's own aggregate is read out of a series the
candidates *are* — the entity that moved the fab created the anchor it is then
scored at — and the permutation null, which starts after the anchor exists,
cannot reproduce that selection. Measured on 200 fault-free worlds, a
fab-chosen anchor made the engine fire at 0.200 against a nominal 0.05. A
declared anchor at the same level fires at 0.075, which is 1.6 standard errors
from nominal at that population size, and is calibrated at every other level.

So `select` is handed the horizon and nothing else: it cannot see a candidate,
a channel or a value, and its parameter list has nowhere to put one. That is a
stronger guarantee than the previous version could make, and it costs the
ability to adapt to a world whose faults arrive somewhere unusual — a real
limitation, recorded here and in `DIAGNOSIS_CONTRACT.md` §8.1, and one the
current scenario library cannot measure because every fault in it begins in
the middle of the horizon.
"""
from __future__ import annotations

from typing import Sequence

from fabops.diagnosis.model import Anchor

__all__ = ["DECLARED_FRACTIONS", "MIN_SEGMENT_BINS", "select"]

#: Fewest bins either side of a change point. Two keeps the contrast defined
#: and stops a single bin from being read as a change.
MIN_SEGMENT_BINS = 2

#: Where the shared anchors sit, as fractions of the horizon. One, in the
#: middle, is what measurement supports today: a three-anchor grid covering
#: early and late faults reads 0.270 at a nominal 0.20 on the same population
#: (+2.5 standard errors), because a maximum over anchors interacts with each
#: candidate's own bin availability and is then only partly absorbed by the
#: permutation. Widening this is a benchmark question, not a preference.
DECLARED_FRACTIONS: tuple[float, ...] = (0.50,)


def select(horizon_days: float,
           fractions: Sequence[float] = DECLARED_FRACTIONS
           ) -> tuple[Anchor, ...]:
    """The shared anchors for a horizon. Sees no candidate and no observation."""
    if horizon_days <= 0:
        raise ValueError("a horizon must be positive to carry an anchor")
    return tuple(
        Anchor(day=round(horizon_days * fraction, 6), channel="declared",
               statistic=float(fraction))
        for fraction in sorted(fractions))
