"""The null reference distribution, and the criteria now read against it.

ADR-026 established that A6's floor and L7's threshold measured an order
statistic rather than the fab. ADR-027 replaces both references with a
distribution derived from exchangeability. That distribution is now
load-bearing for two acceptance criteria, so what is pinned here is the thing
a wrong reference would silently corrupt: the arithmetic, the critical values,
and the refusals.

The reference is a Monte Carlo integral from a declared seed, so its critical
values are exact numbers a diff can show. They are recorded below in the
`REFERENCE_CRITICAL_VALUES` table for the same reason
`tests/fabsim/test_generation_identity.py` records generation digests: a
number that moves without anyone noticing is how a benchmark stops meaning
anything.
"""
from __future__ import annotations

import math
import random

import pytest

from fabeval.queries import zscore
from fabeval.reference import (
    ALPHA,
    FAB_CONTROL_LIMIT_ALPHA,
    MINIMUM_NULL_WORLDS,
    REFERENCE_SEED,
    REFERENCE_TRIALS,
    CalibrationReading,
    _leave_one_out_z,
    exchangeable_reference,
    null_calibration,
)

#: What the declared reference produces, by chamber count. Recorded so a
#: change to the trial count, the seed or the arithmetic is a visible edit to
#: a table rather than a silently different verdict.
#:
#: n=7 is what every etch-grain reference query reports at on the baseline
#: world; n=18 is what `alarm_counts` reports at.
REFERENCE_CRITICAL_VALUES = {
    #  n: (per-chamber c at ALPHA, family-wise c at ALPHA,
    #      per-chamber c at the fab's 3-sigma convention)
    7: (3.044, 5.149, 6.457),
    18: (2.249, 3.750, 3.764),
}


# ------------------------------------------------------------- the arithmetic


def test_the_reference_arithmetic_matches_the_definition():
    """`_leave_one_out_z` must agree with `fabeval.queries.zscore`.

    It exists only to avoid `statistics.mean`/`pstdev`, whose exact-rational
    summation costs about fifty times plain float arithmetic at a hundred
    thousand worlds. A faster helper that computes a *different* number would
    give every criterion a reference of the wrong shape, so the agreement is
    checked over deliberately awkward inputs: mixed scales, and large offsets
    where a one-pass sum-of-squares form loses its significant digits.

    The one-pass form was tried first and measured disagreeing by up to 43
    sigma. This test is why that did not ship.
    """
    rng = random.Random(5)
    worst = 0.0
    for _ in range(2000):
        n = rng.choice([3, 5, 7, 17])
        sigma = rng.choice([0.01, 1.0, 50.0])
        offset = rng.choice([0.0, 0.0, 1000.0])
        values = [offset + rng.gauss(0.0, sigma) for _ in range(n)]
        fast = _leave_one_out_z(values)
        slow = [zscore({str(i): v for i, v in enumerate(values)}, str(i))
                for i in range(n)]
        worst = max(worst, max(abs(a - b) for a, b in zip(fast, slow)))
    assert worst < 1e-8, worst


def test_a_degenerate_input_does_not_raise():
    """Identical chambers have no spread; the definition returns 0 there."""
    assert _leave_one_out_z([1.0, 1.0, 1.0, 1.0]) == [0.0, 0.0, 0.0, 0.0]
    assert _leave_one_out_z([2.0, 3.0]) == [0.0, 0.0]


# --------------------------------------------------------- the distribution


def test_the_reference_is_deterministic():
    """Two builds of one reference are the same reference."""
    a = exchangeable_reference(7, trials=5_000, seed=1)
    b = exchangeable_reference(7, trials=5_000, seed=1)
    assert a.per_chamber == b.per_chamber
    assert a.per_world == b.per_world
    other = exchangeable_reference(7, trials=5_000, seed=2)
    assert other.per_chamber != a.per_chamber


@pytest.mark.parametrize("chambers", sorted(REFERENCE_CRITICAL_VALUES))
def test_the_declared_critical_values_are_what_the_reference_produces(
        chambers):
    """The tripwire. A moved constant must show up as a moved table entry."""
    reference = exchangeable_reference(chambers)
    assert reference.trials == REFERENCE_TRIALS
    per_chamber, family_wise, fab = REFERENCE_CRITICAL_VALUES[chambers]
    assert reference.per_chamber_critical(ALPHA) == pytest.approx(
        per_chamber, abs=0.005)
    assert reference.family_wise_critical(ALPHA) == pytest.approx(
        family_wise, abs=0.005)
    assert reference.per_chamber_critical(
        FAB_CONTROL_LIMIT_ALPHA) == pytest.approx(fab, abs=0.02)


def test_the_critical_values_are_correctly_sized():
    """A level is a promise about a rate; check the reference keeps it."""
    reference = exchangeable_reference(7)
    for alpha in (0.10, 0.05, 0.01):
        limit = reference.per_chamber_critical(alpha)
        rate = sum(1 for v in reference.per_chamber if v > limit) \
            / len(reference.per_chamber)
        assert rate == pytest.approx(alpha, abs=0.005), (alpha, rate)
        fw = reference.family_wise_critical(alpha)
        rate = sum(1 for v in reference.per_world if v > fw) \
            / len(reference.per_world)
        assert rate == pytest.approx(alpha, abs=0.005), (alpha, rate)


def test_the_old_l7_constant_sat_below_benign_variation():
    """Why the previous threshold could not work, in one number.

    L7 compared the *worst* of seven chambers against 2.5. The worst of seven
    exchangeable chambers exceeds 2.5 with probability 0.598 — so the old
    constant was not a floor above benign variation, it was well inside it.
    ADR-026 measured the consequence: 10 of 12 fault-free worlds failed.
    """
    reference = exchangeable_reference(7)
    assert reference.family_wise_exceedance(2.5) == pytest.approx(0.598,
                                                                 abs=0.02)
    assert reference.per_chamber_critical(ALPHA) > 2.5
    assert reference.family_wise_critical(ALPHA) > 2.5


def test_exceedance_is_monotone_and_bounded():
    reference = exchangeable_reference(7)
    previous = 1.1
    for sigma in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0):
        p = reference.per_chamber_exceedance(sigma)
        assert 0.0 <= p <= 1.0
        assert p <= previous
        previous = p
    assert reference.per_chamber_exceedance(0.0) == pytest.approx(1.0,
                                                                  abs=1e-9)


def test_a_reference_needs_enough_chambers_to_leave_one_out():
    with pytest.raises(ValueError, match="at least 3 chambers"):
        exchangeable_reference(2)


# ---------------------------------------------------------- the calibration


def _reading(exceedances, observations, alpha=ALPHA):
    return CalibrationReading(worlds=12, observations=observations,
                              exceedances=exceedances, alpha=alpha,
                              critical={}, per_channel={})


def test_calibration_flags_an_inflated_population_and_not_a_sized_one():
    """The decision rule, at the boundary in both directions.

    A null population carrying *systematically* more chamber structure than
    the design declares is the failure no per-world threshold notices, because
    every world is only slightly out. This is the check that does.
    """
    # A correctly sized population: the measured rate on the real nulls is
    # 9/252 against 0.05 expected (ADR-027 §3).
    assert not _reading(9, 252).inflated
    assert not _reading(13, 252).inflated
    # A doubled rate over the same sample must be caught.
    assert _reading(25, 252).inflated
    # …and a quadrupled one, on the smaller sample a three-null library gives.
    assert not _reading(3, 63).inflated
    assert _reading(13, 63).inflated


def test_calibration_reports_how_blind_it_is():
    """A rate from three worlds resolves much less than one from twelve, and
    a check that cannot say so will be read as though it were conclusive."""
    small = _reading(3, 63).detectable_inflation
    large = _reading(13, 252).detectable_inflation
    assert small > large > 1.0
    assert math.isinf(_reading(0, 0).detectable_inflation)


def test_a_calibration_needs_more_than_one_null_world(nulls):
    """The same refusal `natural_variation_floor` makes, for the same reason."""
    assert MINIMUM_NULL_WORLDS >= 3
    with pytest.raises(ValueError, match="at least"):
        null_calibration(nulls[:1], ("edge_cd",))
    with pytest.raises(ValueError, match="at least"):
        null_calibration(nulls[:2], ("edge_cd",))


def test_the_real_nulls_are_correctly_sized(nulls):
    """The measurement that clears the simulator, in L7's own currency.

    Every chamber of every fault-free world, on the three reference channels,
    against the per-chamber critical value at the declared level. If the world
    carried excess chamber-to-chamber structure this rate would be inflated;
    ADR-027 §3 measured it at or below nominal on twelve worlds and this runs
    the same reading on the three the suite can afford.
    """
    from fabeval.leakage import L7_CHANNELS

    reading = null_calibration(nulls, L7_CHANNELS)
    assert reading.observations >= 60, reading.observations
    assert not reading.inflated, (reading.rate, reading.alpha,
                                  dict(reading.per_channel))
    # And the stricter level the fab's own charts run at is satisfied too, so
    # the conclusion does not depend on which of the two is declared.
    strict = null_calibration(nulls, L7_CHANNELS,
                              alpha=FAB_CONTROL_LIMIT_ALPHA)
    assert strict.exceedances == 0, dict(strict.per_channel)


@pytest.fixture(scope="module")
def nulls(world, tmp_path_factory):
    """Three fault-free worlds."""
    from pathlib import Path

    from fabsim.emit import build_dataset
    from fabsim.scenario import load_scenario

    root = tmp_path_factory.mktemp("reference-nulls")
    scenarios = Path(__file__).resolve().parents[2] / "scenarios"
    config = load_scenario(scenarios / "null_baseline.json")
    return [build_dataset(config, seed, world=world, root=root / str(seed),
                          created_at="reference")
            for seed in (42, 101, 2024)]
