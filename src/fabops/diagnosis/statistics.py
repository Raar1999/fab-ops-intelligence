"""
statistics.py — what "how far did this candidate depart?" means, per channel.

Deliberately a **registry**, not a decision. Four measurement gates could not
distinguish the candidate statistics at the size of the benchmark that exists:
the paired comparisons sit at p = 0.22-0.38 and the spread across arbitrary
anchor choices is wider than the effect being estimated. Freezing one here
would be fitting the architecture to five scenarios, so the architecture makes
the statistic replaceable and lets the benchmark choose it later
(`DIAGNOSIS_CONTRACT.md` §8.1).

What is *not* replaceable is the shape of the call. A statistic is handed a
candidate's peer-differenced series and an anchor **it did not choose**, and
returns one non-negative number. It cannot search for a better anchor, because
it is not given the others.
"""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from fabops.diagnosis.anchors import MIN_SEGMENT_BINS
from fabops.diagnosis.candidates import BIN_DAYS, MIN_BINS

__all__ = [
    "DEFAULT_STATISTIC",
    "STATISTICS",
    "Statistic",
    "evaluate",
    "own_scale_step",
    "standardized_step",
    "trend_contrast",
]

#: A statistic: (series, bin indices, anchor day, pooled scale) -> float >= 0.
Statistic = Callable[[Sequence[float], Sequence[int], float, float], float]


def _split_at(indices: Sequence[int], anchor_day: float,
              bin_days: float) -> int:
    """How many of this candidate's bins fall before the shared anchor."""
    return sum(1 for k in indices if (k + 1) * bin_days <= anchor_day)


def own_scale_step(series: Sequence[float], indices: Sequence[int],
                   anchor_day: float, scale: float,
                   bin_days: float = BIN_DAYS) -> float:
    """The same contrast, standardized by the candidate's OWN spread.

    A pooled denominator makes the statistic comparable across candidates only
    if their spreads are comparable. They are not: exposure differs by routing,
    so a lightly-used candidate's series is noisier and its standardized step
    is systematically larger on *every* channel at once - which is precisely
    the shape cross-family convergence is built to detect, and therefore a way
    for benign heterogeneity to look like a converging signal. Dividing by the
    candidate's own spread costs precision on a short series and buys back the
    exchangeability the null needs.
    """
    if len(series) < MIN_BINS:
        return 0.0
    split = _split_at(indices, anchor_day, bin_days)
    if split < MIN_SEGMENT_BINS or len(series) - split < MIN_SEGMENT_BINS:
        return 0.0
    before, after = series[:split], series[split:]
    mean_before = sum(before) / len(before)
    mean_after = sum(after) / len(after)
    residual = (sum((v - mean_before) ** 2 for v in before)
                + sum((v - mean_after) ** 2 for v in after))
    degrees = len(series) - 2
    if degrees <= 0 or residual <= 0:
        return 0.0
    own = (residual / degrees) ** 0.5
    error = own * math.sqrt(1.0 / len(before) + 1.0 / len(after))
    return abs(mean_after - mean_before) / error if error > 0 else 0.0


def standardized_step(series: Sequence[float], indices: Sequence[int],
                      anchor_day: float, scale: float,
                      bin_days: float = BIN_DAYS) -> float:
    """A two-sample contrast across the shared anchor, in the pooled spread.

    `scale` is pooled across the entities of the channel rather than estimated
    from this one series, which is more precise per candidate and measurably
    more powerful - and measurably **not calibrated**: pooling lets a candidate
    whose exposure makes its series noisier be systematically extreme on every
    channel at once, which is the shape convergence is built to detect. Kept
    registered, not default, with its measured cost recorded beside it.
    """
    if len(series) < MIN_BINS or scale <= 0:
        return 0.0
    split = _split_at(indices, anchor_day, bin_days)
    if split < MIN_SEGMENT_BINS or len(series) - split < MIN_SEGMENT_BINS:
        return 0.0
    before, after = series[:split], series[split:]
    contrast = (sum(after) / len(after)) - (sum(before) / len(before))
    error = scale * math.sqrt(1.0 / len(before) + 1.0 / len(after))
    return abs(contrast / error) if error > 0 else 0.0


def trend_contrast(series: Sequence[float], indices: Sequence[int],
                   anchor_day: float, scale: float,
                   bin_days: float = BIN_DAYS) -> float:
    """Slope after the shared anchor minus slope before it, standardized.

    An alternative reading for a departure that ramps rather than steps. Kept
    registered and unused by default so the registry is demonstrably plural -
    a pluggable component with one member is a constant wearing a costume.
    """
    if len(series) < MIN_BINS or scale <= 0:
        return 0.0
    split = _split_at(indices, anchor_day, bin_days)
    if split < MIN_SEGMENT_BINS or len(series) - split < MIN_SEGMENT_BINS:
        return 0.0

    def slope(points: Sequence[tuple[float, float]]) -> float:
        n = len(points)
        mean_x = sum(x for x, _ in points) / n
        mean_y = sum(y for _, y in points) / n
        denominator = sum((x - mean_x) ** 2 for x, _ in points)
        if denominator <= 0:
            return 0.0
        return sum((x - mean_x) * (y - mean_y)
                   for x, y in points) / denominator

    pairs = list(zip((float(k) for k in indices), series))
    difference = slope(pairs[split:]) - slope(pairs[:split])
    # The slope of a series of independent points with spread `scale` has
    # standard error scale / sqrt(sum (x - xbar)^2); use the smaller segment's,
    # which is the conservative one.
    smaller = pairs[:split] if split <= len(pairs) - split else pairs[split:]
    mean_x = sum(x for x, _ in smaller) / len(smaller)
    spread_x = sum((x - mean_x) ** 2 for x, _ in smaller)
    if spread_x <= 0:
        return 0.0
    error = scale / math.sqrt(spread_x)
    return abs(difference / error) if error > 0 else 0.0


#: The registry. A report records which member produced it.
STATISTICS: Mapping[str, Statistic] = MappingProxyType({
    "standardized_step": standardized_step,
    "own_scale_step": own_scale_step,
    "trend_contrast": trend_contrast,
})

#: The declared default. Chosen on **fault-free calibration**, never on fault
#: performance - which is the only selection criterion available to an engine
#: that may not claim a benchmark number yet.
#:
#: **The measured table lives in ADR-029 §8 and `DIAGNOSIS_CONTRACT.md` §8.1,
#: and deliberately not here.** It used to be restated in this comment, and the
#: restatement disagreed with both of those on every null-calibration figure -
#: two records of one measurement, written minutes apart, and neither
#: reproducible from what it wrote down. The Final Integration gate resolved it
#: the way ADR-011 resolves this whole class of problem: the artifact that owns
#: a number keeps it and the code cites rather than copies. Read either
#: document for the figures. What a reader of *this module* needs is the
#: reason, and the reason does not go stale.
#:
#: `own_scale_step` is calibrated at every declared level and can still be
#: moved by a mutation - both halves are required, since a statistic that
#: cannot move is inert whatever its calibration. `standardized_step` finds
#: more and states a level it does not keep: pooling the denominator across a
#: stratum lets a candidate whose exposure makes its series noisier come out
#: extreme on every channel at once, which is the exact shape cross-family
#: convergence exists to detect. An abstention is a claim, and a mis-calibrated
#: claim is worse than a weak one, so the calibrated statistic ships and the
#: powerful one stays registered beside it with its cost on the record.
#: `trend_contrast` is registered and unmeasured. The >=10-scenario benchmark
#: is what should choose among them, from a table rather than from folklore.
DEFAULT_STATISTIC = "own_scale_step"


def evaluate(name: str, series: Sequence[float], indices: Sequence[int],
             anchor_day: float, scale: float) -> float:
    try:
        statistic = STATISTICS[name]
    except KeyError:                                    # pragma: no cover
        raise ValueError(
            f"unknown statistic {name!r}; registered: {sorted(STATISTICS)}")
    return statistic(series, indices, anchor_day, scale)
