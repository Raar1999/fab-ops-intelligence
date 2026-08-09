"""
reference.py — the null reference distribution the size-based criteria read.

Three criteria in this evaluator ask a question of the form "is this chamber's
standing larger than benign variation produces?" — L7 on a fault-free world,
A6 on the severity ladder, and L11's expectation floors. Until ADR-026 each
answered it against a number nobody derived: L7 against a hardcoded 2.5, A6
against `max over null seeds (max over chambers |z|)`. The first is a
per-chamber constant applied to a maximum over chambers; the second is a
cumulative maximum and diverges with the number of null worlds built. Neither
is a property of the fab, and ADR-026 measured that both would return the same
verdict on a simulator of any quality.

This module supplies what those criteria actually need: **the distribution of
the statistic under the null hypothesis the criteria are about.**

The null hypothesis is exchangeability. `ANTI_LEAKAGE_DESIGN.md` F10/F11 make
it true by construction — every chamber carries every latent with the same
dynamics, and every chamber carries a permanent offset drawn from the shared
family — so on a fault-free world the chambers are interchangeable and their
per-chamber statistics are draws from one distribution. Two derived
quantities follow, and they answer two different questions:

    per_chamber   one *specified* chamber's standing.  A6 asks this: truth
                  names the planted chamber, so the comparison is one
                  chamber against single benign chambers.
    per_world     the *worst* chamber's standing.  L7 asks this: an analyst
                  facing a fault-free world does not know which chamber to
                  excuse, so the question is whether the worst one misleads.

**Why the reference is derived rather than measured from the null datasets.**
A threshold fitted to the same nulls it is used to judge cannot fail, which is
the tautology ADR-026 §4 warns about. The reference here is external: it comes
from the chamber count and from exchangeability, and from nothing any dataset
contains. Whether the simulator's nulls actually match it is then a real,
falsifiable question — `null_calibration` asks it, and ADR-026 §3 measured the
answer as yes to three decimal places.

Stdlib only, like the rest of `fabeval`, and deterministic: the reference is a
Monte Carlo integral over a declared trial count from a declared seed, so two
runs produce the same critical values and a change to either is visible in a
diff rather than in a result.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Sequence

from fabeval.queries import zscore

__all__ = [
    "ALPHA",
    "FAB_CONTROL_LIMIT_ALPHA",
    "MINIMUM_NULL_WORLDS",
    "REFERENCE_SEED",
    "REFERENCE_TRIALS",
    "CalibrationReading",
    "ReferenceDistribution",
    "exchangeable_reference",
    "null_calibration",
]

#: The declared false-positive level every size-based criterion is read at.
#:
#: 0.05 is the conventional statistical level and it is declared here, once,
#: rather than appearing as a bare number inside a check. Two things make it
#: an honest choice rather than a convenient one. It was fixed *before* the
#: critical values were computed, and it is **not** the level that would make
#: the most criteria pass — the fab's own control charts run at 3 sigma
#: (`alarms.codes[].threshold_sigma` in the world template), i.e. a per-check
#: false-alarm rate of 0.0027, and the null world satisfies that stricter
#: level too: zero exceedances in 252 chamber-channel observations over twelve
#: fault-free worlds, against 0.7 expected (ADR-027 §3).
ALPHA = 0.05

#: The fab's own control-limit convention, for the record and for evidence:
#: two-sided 3 sigma, the multiple eight of the nine alarm codes in
#: `baseline_fab_v1` declare. Reported alongside `ALPHA` so a reader can see
#: that the conclusion does not depend on which of the two is used.
FAB_CONTROL_LIMIT_ALPHA = 0.0027

#: Fewest fault-free worlds a calibration reading may be taken from. Same
#: refusal `fabeval.sweep.MINIMUM_FLOOR_SEEDS` makes and for the same reason:
#: a rate estimated from one draw is not a rate. Three is a floor, not a
#: recommendation — `CalibrationReading.detectable_inflation` reports what the
#: sample can actually resolve, because a check that cannot say how blind it
#: is will be read as though it were not.
MINIMUM_NULL_WORLDS = 3

#: Monte Carlo settings. Declared so the critical values are reproducible and
#: so changing them is a visible edit rather than a drifting result.
REFERENCE_TRIALS = 100_000
REFERENCE_SEED = 20260809


def _leave_one_out_z(values: Sequence[float]) -> list[float]:
    """Every chamber's leave-one-out z — the definition, computed directly.

    Same quantity `fabeval.queries.zscore` returns, and
    `test_the_reference_arithmetic_matches_the_definition` pins the agreement
    over random inputs. What differs is only that this does not route through
    `statistics.mean`/`pstdev`, whose exact-rational summation is accurate far
    beyond what a Monte Carlo reference needs and costs about fifty times the
    runtime of plain float arithmetic at a hundred thousand worlds.

    The two passes are deliberate. A one-pass form — carrying the totals of
    all `n` and subtracting the held-out value — is algebraically identical
    and numerically wrong: `sum(x^2)/(n-1) - mean^2` is a difference of two
    nearly equal large numbers whenever the spread is small against the mean,
    and it was measured disagreeing with the definition by up to 43 sigma on
    heterogeneous inputs. Speed is not worth a reference distribution that is
    subtly the wrong shape.
    """
    n = len(values)
    if n < 3:
        return [0.0] * n
    out: list[float] = []
    for index in range(n):
        others = values[:index] + values[index + 1:] \
            if isinstance(values, list) else \
            [v for i, v in enumerate(values) if i != index]
        mean = sum(others) / (n - 1)
        variance = sum((v - mean) * (v - mean) for v in others) / (n - 1)
        spread = math.sqrt(variance) if variance > 0.0 else 0.0
        out.append((values[index] - mean) / spread if spread else 0.0)
    return out


@dataclass(frozen=True)
class ReferenceDistribution:
    """What `|z|` does on a world where the chambers differ by nothing.

    Both orderings are kept sorted so a quantile is an index and an exceedance
    probability is a bisection.
    """

    chambers: int
    trials: int
    #: |z| of every chamber of every simulated world, sorted.
    per_chamber: tuple[float, ...]
    #: max |z| over the chambers of each simulated world, sorted.
    per_world: tuple[float, ...]

    @staticmethod
    def _critical(sorted_values: Sequence[float], alpha: float) -> float:
        index = int(math.ceil((1.0 - alpha) * len(sorted_values)))
        return sorted_values[min(len(sorted_values) - 1, max(0, index))]

    @staticmethod
    def _exceedance(sorted_values: Sequence[float], value: float) -> float:
        import bisect

        beyond = len(sorted_values) - bisect.bisect_left(sorted_values,
                                                         abs(value))
        return beyond / len(sorted_values)

    def per_chamber_critical(self, alpha: float = ALPHA) -> float:
        """Standing one *specified* chamber exceeds with probability alpha."""
        return self._critical(self.per_chamber, alpha)

    def family_wise_critical(self, alpha: float = ALPHA) -> float:
        """Standing the *worst* chamber of a world exceeds with prob. alpha."""
        return self._critical(self.per_world, alpha)

    def per_chamber_exceedance(self, sigma: float) -> float:
        """How often a benign chamber reaches at least this standing.

        The p-value A6 reads the severity ladder in. Converging by
        construction: more reference trials sharpen it, and it does not move
        with the number of null *datasets* a gate could afford to build —
        which is the property `natural_variation_floor` lacks.
        """
        return self._exceedance(self.per_chamber, sigma)

    def family_wise_exceedance(self, sigma: float) -> float:
        """How often a fault-free world's worst chamber reaches this."""
        return self._exceedance(self.per_world, sigma)


@lru_cache(maxsize=None)
def exchangeable_reference(chambers: int,
                           trials: int = REFERENCE_TRIALS,
                           seed: int = REFERENCE_SEED
                           ) -> ReferenceDistribution:
    """Simulate worlds whose chambers differ by nothing at all.

    Gaussian because a per-chamber reference score is a mean over tens to
    hundreds of measurements and the central limit theorem is what makes that
    the right family — not an assumption about the fab, an assumption about an
    average. `null_calibration` is the test of whether it holds, and it is a
    test the simulator can fail.
    """
    if chambers < 3:
        raise ValueError(
            f"a leave-one-out reference needs at least 3 chambers, "
            f"got {chambers}")
    rng = random.Random(seed)
    gauss = rng.gauss
    per_chamber: list[float] = []
    per_world: list[float] = []
    for _ in range(trials):
        z = [abs(v) for v in
             _leave_one_out_z([gauss(0.0, 1.0) for _ in range(chambers)])]
        per_chamber += z
        per_world.append(max(z))
    per_chamber.sort()
    per_world.sort()
    return ReferenceDistribution(chambers=chambers, trials=trials,
                                 per_chamber=tuple(per_chamber),
                                 per_world=tuple(per_world))


@dataclass(frozen=True)
class CalibrationReading:
    """How often fault-free chambers exceed what exchangeability predicts.

    The high-power half of L7. A single grossly deviant chamber is what the
    family-wise guard is for; this is what catches the other failure — a null
    population carrying *systematically* more chamber-to-chamber structure
    than the design declares, which no per-world threshold would notice
    because every world would be only slightly out.
    """

    worlds: int
    observations: int
    exceedances: int
    alpha: float
    critical: Mapping[str, float]
    #: Observed exceedances per channel, for the evidence line.
    per_channel: Mapping[str, tuple[int, int]]

    @property
    def rate(self) -> float:
        return self.exceedances / self.observations if self.observations else 0.0

    @property
    def expected(self) -> float:
        return self.alpha * self.observations

    @property
    def detectable_inflation(self) -> float:
        """Smallest true rate, as a multiple of alpha, this sample resolves.

        A one-sided Gaussian approximation to the binomial at 95%: the rate
        has to exceed `alpha + 1.645 * sqrt(alpha(1-alpha)/N)` to be
        distinguishable from alpha. Reported rather than hidden, because a
        calibration check run on three worlds is nearly blind and saying so is
        the difference between a measurement and a reassurance.
        """
        if not self.observations:
            return math.inf
        margin = 1.645 * math.sqrt(self.alpha * (1.0 - self.alpha)
                                   / self.observations)
        return (self.alpha + margin) / self.alpha

    @property
    def inflated(self) -> bool:
        """True when the observed rate is above alpha by more than chance."""
        if not self.observations:
            return False
        margin = 1.645 * math.sqrt(self.alpha * (1.0 - self.alpha)
                                   / self.observations)
        return self.rate > self.alpha + margin


def null_calibration(nulls: Sequence[Any],
                     channels: Sequence[str],
                     alpha: float = ALPHA) -> CalibrationReading:
    """Score fault-free worlds against the exchangeable reference.

    Raises rather than returning a reading from too few worlds, for the reason
    `fabeval.sweep.natural_variation_floor` refuses one: a number that cannot
    mean anything is worse than no number, because it will be quoted.
    """
    from fabeval.sweep import channel_scores

    if len(nulls) < MINIMUM_NULL_WORLDS:
        raise ValueError(
            f"a null calibration needs at least {MINIMUM_NULL_WORLDS} "
            f"fault-free worlds, got {len(nulls)}")

    critical: dict[str, float] = {}
    per_channel: dict[str, tuple[int, int]] = {}
    exceedances = total = 0
    for channel in channels:
        hits = seen = 0
        for dataset in nulls:
            scores = channel_scores(dataset, channel)
            if len(scores) < 3:
                continue
            limit = exchangeable_reference(
                len(scores)).per_chamber_critical(alpha)
            critical[channel] = limit
            for label in scores:
                seen += 1
                if abs(zscore(scores, label)) > limit:
                    hits += 1
        if seen:
            per_channel[channel] = (hits, seen)
            exceedances += hits
            total += seen
    return CalibrationReading(worlds=len(nulls), observations=total,
                              exceedances=exceedances, alpha=alpha,
                              critical=critical, per_channel=per_channel)
