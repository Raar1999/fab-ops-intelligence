"""What A6's floor and L7's threshold actually measure (ADR-026).

These tests do not assert that the benchmark passes. They assert the *reason*
it does not, so that the reason cannot be quietly lost — and so that a future
gate cannot repeat the two mistakes this one found.

The finding: `fabeval.sweep.natural_variation_floor` and
`fabeval.leakage.l7_null_blindness` both reduce a fault-free world to
`max over chambers |leave-one-out z|` and compare it against a number that
means *one chamber's standing* — the planted chamber's sigma in A6's case, a
hardcoded 2.5 in L7's. A maximum over N exchangeable draws exceeds a single
draw by an amount that is a property of N, so neither comparison is a
statement about the fab. Three things follow, and each is pinned below:

1. the reference distribution of that maximum makes L7's threshold a coin
   flip on a *correct* null world;
2. the floor is a cumulative maximum and therefore diverges with the number
   of null realizations, so the verdict it produces is a function of the
   evaluator's budget rather than of the simulator;
3. no world constant can move either, because `zscore` is scale-free.

Point 3 is why nothing under `src/fabsim/` was changed: there is no lever.

The arithmetic here is deliberately self-contained and seeded. It needs no
dataset, which is what lets it run on every commit rather than on the gates
that can afford to build twelve worlds.
"""
from __future__ import annotations

import random
import statistics as st
from pathlib import Path

import pytest

from fabeval.queries import zscore

SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"

#: Chambers the etch-grain reference queries report at on the baseline world
#: (three etch tools, two or three chambers each). `alarm_counts` reports at
#: 17 because it is not restricted to an operation.
ETCH_CHAMBERS = 7

#: `fabeval.leakage.l7_null_blindness`'s default floor. Referenced, never
#: redefined: if the constant moves, these tests must be re-read rather than
#: silently kept green.
L7_FLOOR = 2.5


def _leave_one_out_z(values, index):
    """`fabeval.queries.zscore`, on a bare list rather than a mapping."""
    scores = {str(i): v for i, v in enumerate(values)}
    return zscore(scores, str(index))


def _max_abs_z(values):
    return max(abs(_leave_one_out_z(values, i)) for i in range(len(values)))


def _exchangeable_worlds(n, trials, seed):
    """`trials` worlds of `n` chambers that differ by nothing at all."""
    rng = random.Random(seed)
    return [[rng.gauss(0.0, 1.0) for _ in range(n)] for _ in range(trials)]


@pytest.fixture(scope="module")
def nulls(world, tmp_path_factory):
    """Three fault-free worlds, the same three the sweep tests read.

    Built here rather than imported from `test_sweep` so neither module can
    change the other's fixture out from under it.
    """
    from fabsim.emit import build_dataset
    from fabsim.scenario import load_scenario

    root = tmp_path_factory.mktemp("floor-nulls")
    config = load_scenario(SCENARIO_ROOT / "null_baseline.json")
    return [build_dataset(config, seed, world=world, root=root / str(seed),
                          created_at="floor")
            for seed in (42, 101, 2024)]


# --------------------------------------------- 1. the reference distribution


def test_l7s_floor_is_below_the_median_of_its_own_null_distribution():
    """A world where every chamber is drawn from one distribution — no benign
    offsets, no fault, nothing — still trips L7 more often than not.

    This is the whole finding in one assertion. L7 is not measuring the fab.
    """
    worlds = _exchangeable_worlds(ETCH_CHAMBERS, 20_000, seed=20260809)
    maxima = sorted(_max_abs_z(w) for w in worlds)
    tripped = sum(1 for m in maxima if m > L7_FLOOR) / len(maxima)

    assert 0.55 < tripped < 0.65, (
        f"P(max|z| > {L7_FLOOR}) = {tripped:.3f} on perfectly exchangeable "
        "chambers; ADR-026 measured 0.598")
    median = maxima[len(maxima) // 2]
    assert median > L7_FLOOR, (
        f"the median fault-free world reaches {median:.2f} sigma, above "
        f"L7's {L7_FLOOR} floor")


def test_l7_checks_three_channels_so_its_expected_failure_rate_is_near_one():
    """L7 fails a dataset if *any* of its three channels trips.

    Measured over twelve real fault-free worlds (ADR-026 §2): 10 of 12. The
    2-of-3 ADR-025 §5 reported is the modal outcome of a correct simulator,
    not evidence about one.
    """
    worlds = _exchangeable_worlds(ETCH_CHAMBERS, 20_000, seed=20260809)
    per_channel = sum(1 for w in worlds
                      if _max_abs_z(w) > L7_FLOOR) / len(worlds)
    independent_three = 1.0 - (1.0 - per_channel) ** 3
    assert independent_three > 0.9, independent_three


def test_a_single_chamber_is_a_different_question_from_the_worst_of_seven():
    """The comparison A6 and L7 make, stated as the two numbers it conflates.

    One specified chamber's |z| and the largest of seven chambers' are drawn
    from visibly different distributions. Comparing a fault's standing on the
    first against a null's standing on the second is the defect.
    """
    worlds = _exchangeable_worlds(ETCH_CHAMBERS, 20_000, seed=20260809)
    single = sorted(abs(_leave_one_out_z(w, 0)) for w in worlds)
    worst = sorted(_max_abs_z(w) for w in worlds)

    def quantile(values, p):
        return values[min(len(values) - 1, int(p * len(values)))]

    assert st.mean(worst) > 2.3 * st.mean(single), (
        st.mean(worst), st.mean(single))
    # A moderate fault at 2.65 sigma sits in the far tail of the per-chamber
    # distribution and near the *middle* of the max-of-seven one. Both
    # statements are about the same 2.65.
    tail_single = sum(1 for v in single if v >= 2.65) / len(single)
    tail_worst = sum(1 for v in worst if v >= 2.65) / len(worst)
    assert tail_single < 0.15, tail_single
    assert tail_worst > 0.45, tail_worst
    assert quantile(worst, 0.5) > quantile(single, 0.85)


# ------------------------------------------------------- 2. the divergence


def test_the_floor_is_a_cumulative_maximum_and_therefore_diverges():
    """More null worlds always raise A6's floor and never lower it.

    `natural_variation_floor` reports `max(per_seed)`. A cumulative maximum of
    an unbounded-support statistic has no limit, so "does moderate clear the
    floor?" is a question about how many nulls the benchmark could afford.
    Measured on the real world as seeds are added (ADR-026 §6): edge_cd
    2.31 -> 2.84 -> 5.38, alarms 2.50 -> 5.05 -> 13.08.

    ADR-025 §4 raised `MINIMUM_FLOOR_SEEDS` from 1 to 3 to stop an
    under-sampled floor flattering the comparison. That is the right direction
    and the wrong cure: three draws do not stabilise a divergent statistic.
    """
    worlds = _exchangeable_worlds(ETCH_CHAMBERS, 12_000, seed=4242)
    maxima = [_max_abs_z(w) for w in worlds]

    running = []
    best = 0.0
    for value in maxima:
        best = max(best, value)
        running.append(best)
    assert running == sorted(running), "a cumulative maximum cannot decrease"

    # The exact statement is the monotonicity above; the interesting one is
    # that it keeps climbing. Read over independent blocks rather than off one
    # realization, so this measures the statistic and not a lucky draw.
    def block_mean(k):
        blocks = [max(maxima[i:i + k]) for i in range(0, 12_000 - k, k)]
        return st.mean(blocks)

    at = {k: block_mean(k) for k in (1, 3, 12, 48)}
    assert at[1] < at[3] < at[12] < at[48], at
    assert at[48] > at[3] + 1.5, at
    # There is no seed count at which a moderate fault's 2.65 sigma clears it.
    assert at[3] > 2.65 and at[12] > 2.65, at

    # The divergence is in the aggregation across seeds, not in the per-world
    # statistic: averaging the same maxima is stable at every budget (3.01 at
    # 3, 12 and 48 seeds) while maximizing over them runs away. Recorded so
    # the next gate knows the instability is a choice, not the fab.
    stable = {k: st.mean([st.mean(maxima[i:i + k])
                          for i in range(0, 12_000 - k, k)])
              for k in (3, 12, 48)}
    assert max(stable.values()) - min(stable.values()) < 0.1, stable


def test_minimum_floor_seeds_does_not_make_the_floor_convergent():
    """The guard is real and is not what it is believed to be.

    `MINIMUM_FLOOR_SEEDS` is referenced rather than re-asserted, so raising it
    does not silently make this test agree with a new number.
    """
    from fabeval.sweep import MINIMUM_FLOOR_SEEDS

    worlds = _exchangeable_worlds(ETCH_CHAMBERS, 6_000, seed=99)
    maxima = [_max_abs_z(w) for w in worlds]
    at_minimum = [max(maxima[i:i + MINIMUM_FLOOR_SEEDS])
                  for i in range(0, 3_000, MINIMUM_FLOOR_SEEDS)]
    at_four_times = [max(maxima[i:i + 4 * MINIMUM_FLOOR_SEEDS])
                     for i in range(0, 3_000, 4 * MINIMUM_FLOOR_SEEDS)]
    assert st.mean(at_four_times) > st.mean(at_minimum) + 0.5, (
        st.mean(at_minimum), st.mean(at_four_times))


# ------------------------------------------------ 3. there is no lever


def test_the_z_score_is_blind_to_every_magnitude_the_world_declares():
    """Scaling every chamber's score leaves every z exactly unchanged.

    This is why no world constant can move L7 or A6's floor, and why nothing
    under `src/fabsim/` was touched by ADR-026's gate. The benign offset
    scale, the observation-plane chamber offset and the severity reference are
    all *magnitudes*; `zscore` divides by the realized spread and cannot see
    one. Measured across six world calibrations spanning 16x in benign latent
    offset and 20x in chamber offset, L7 failed 14 of 18 fault-free worlds and
    the quietest calibration failed exactly as often as the baseline.
    """
    rng = random.Random(7)
    scores = {f"TOOL-{i // 3}/{chr(65 + i % 3)}": rng.gauss(0.0, 1.0)
              for i in range(ETCH_CHAMBERS)}
    label = next(iter(scores))
    base = zscore(scores, label)

    for factor in (0.01, 0.2, 0.25, 4.0, 100.0):
        scaled = {k: v * factor for k, v in scores.items()}
        assert zscore(scaled, label) == pytest.approx(base, abs=1e-12), factor

    # A shift is invisible too: a fab-wide offset is not a chamber effect.
    for shift in (-50.0, 0.5, 1_000.0):
        moved = {k: v + shift for k, v in scores.items()}
        assert zscore(moved, label) == pytest.approx(base, abs=1e-9), shift


def test_the_reference_distribution_does_not_depend_on_the_spread():
    """The distributional form of the invariance, not just the algebraic one.

    Chambers drawn with a wide benign spread and chambers drawn with a narrow
    one produce the *same* distribution of `max|z|`, because exchangeability
    is what the statistic is standardized against. So "the benign spread is
    larger than the subtle rung sits above" (ADR-025 §5's open question) is
    not a hypothesis this currency can test, in either direction.
    """
    def maxima(sigma, seed):
        rng = random.Random(seed)
        return sorted(_max_abs_z([rng.gauss(0.0, sigma)
                                  for _ in range(ETCH_CHAMBERS)])
                      for _ in range(8_000))

    narrow = maxima(0.05, seed=11)
    wide = maxima(20.0, seed=11)
    assert narrow == pytest.approx(wide, rel=1e-9)


# ------------------------------------------- 4. the world, measured properly


def test_the_null_world_is_exchangeable_rather_than_over_dispersed(nulls):
    """The measurement that clears the simulator.

    If the world carried excess chamber-to-chamber structure, its per-chamber
    |z| would be over-dispersed against the exchangeable reference. It is not:
    pooled over twelve null realizations ADR-026 §3 measured 1.129 / 1.124 /
    1.084 against a reference mean of 1.123. This runs the same check on the
    three nulls the suite can afford, pooling the channels to keep the
    estimate's standard error below the tolerance.
    """
    from fabeval.sweep import channel_scores

    observed = []
    for dataset in nulls:
        for channel in ("edge_cd", "edge_defect_share", "yield_split"):
            scores = channel_scores(dataset, channel)
            observed += [abs(zscore(scores, label)) for label in scores]

    reference = _exchangeable_worlds(ETCH_CHAMBERS, 20_000, seed=31337)
    expected = st.mean([abs(_leave_one_out_z(w, i))
                        for w in reference for i in range(ETCH_CHAMBERS)])

    assert len(observed) >= 60, len(observed)
    # ~1.0 spread over ~63 samples puts the standard error near 0.13; 0.35 is
    # a wide band deliberately, because this test must fail on a world that
    # acquired real excess structure and not on ordinary sampling noise.
    assert st.mean(observed) == pytest.approx(expected, abs=0.35), (
        st.mean(observed), expected)
