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
a channel or a value, and its parameter list has nowhere to put one. That
guarantee is unchanged by anything below.

**Which fractions was the question Phase 6 answered, and the answer is that
this one stays (ADR-031).** It could not be asked before, and the reason was
recorded here as a limitation: "the current scenario library cannot measure
[it] because every fault in it begins in the middle of the horizon." The
library now contains a fault at day 12 of 84 and one at day 63, so it can be
asked. Pooled over **660 fault-free worlds from three disjoint seed ranges**,
with `own_scale_step`:

    grid                            a=.01     a=.05     a=.10     a=.20
    (0.50,)            midpoint     .0106     .0667     .1303     .2197
    (0.25, .50, .75)   quarters     .0258     .0697     .1197     .2167
                                   (+4.1sd)

A three-anchor grid runs at **2.6x nominal at a = 0.01**, and +4.1 standard
deviations is not a draw. The single mid-horizon anchor is exact there
(.0106 against .010). At the other three levels the two are indistinguishable
and both sit around 1.3x nominal, which is the pre-existing state ADR-029
already recorded.

**The grid finds more, and that is exactly the trade the architecture already
decided.** On the eight development scenarios the grid fires on 6 of 21 faulted
datasets at a = 0.05 where this anchor fires on **0**. An abstention is a
claim; a mis-calibrated claim is worse than a weak one. So the anchor does not
move, and what Phase 6 changes is that its cost is now measured rather than
suspected: at a mid-horizon anchor, against a library that finally contains
early and late faults, the fab-wide bar is cleared by none of them.

**Two recorded figures corrected, both by measuring rather than by argument.**

*One that does not reproduce.* This module previously said a three-anchor grid
"reads 0.270 at a nominal 0.20 (+2.5 standard errors)". Four grids were
re-measured and read 0.185 to 0.225 at that level; the grid's real cost is at
a = 0.01, which that figure did not mention. The conclusion it supported turns
out to be right for a reason it did not state.

*One that was nearly read off too small a sample — this gate's own.* The first
Phase 6 measurement used 200 fault-free worlds and put the grid at .005 at
a = 0.01, below nominal, and the grid was briefly adopted on it. The suite's own
60-world population then failed, at .083. Neither sample was large enough to
resolve a 1-in-100 tail, and picking the one that agreed would have been the
mistake four earlier gates in this repository each made once. An independent
400-world population settled it. **The lesson is the reference-distribution
lesson again, at a new address: a level of 0.01 cannot be validated on the
population sizes a test suite can afford, and only a pooled measurement
recorded outside the suite can say whether it holds.**

*What is true of any grid, and is why a denser one is not automatically better:*
a candidate's statistic is a maximum over the anchors offered, so more anchors
lift every candidate together and the permutation absorbs that only partly.
"""
from __future__ import annotations

from typing import Sequence

from fabops.diagnosis.model import Anchor

__all__ = ["DECLARED_FRACTIONS", "MIN_SEGMENT_BINS", "select"]

#: Fewest bins either side of a change point. Two keeps the contrast defined
#: and stops a single bin from being read as a change.
MIN_SEGMENT_BINS = 2

#: Where the shared anchors sit, as fractions of the horizon.
#:
#: One, in the middle — **confirmed rather than assumed** by the >=10-scenario
#: benchmark ADR-029 §5 said would have to choose it, on 660 fault-free worlds
#: against a library that now contains an early fault and a late one. Every
#: multi-anchor grid tried is hot at a = 0.01 and this one is exact there. The
#: measurement, and what the choice costs in detection, are in this module's
#: docstring and in ADR-031.
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
