"""
The monitors: does the arithmetic do what it says, and what does it cost?

Three kinds of test, and the third is the one that matters most.

* **Unit** — planted series with known properties. A drift is found within k
  points; a flat series is quiet; the Student-t machinery reproduces textbook
  quantiles. These need no dataset and cannot be slow.
* **Behavioural** — the report is deterministic, totally ordered, and responds
  to the one fault it was measured against. And the claim that looked true and
  is not — that the busiest chamber is the planted one — is pinned as a
  *refutation*, so it cannot be re-made without a measurement.
* **Calibration** — the realized signal rate on fault-free worlds, pinned in
  both directions. `EXPANSION_ROADMAP` Phase 3's acceptance asks the monitors
  to be "quiet on the null scenario", and taken literally that is the wrong
  criterion: this fab's null worlds alarm, escalate and get repaired by design
  (`ANTI_LEAKAGE_DESIGN.md` §3.2), and a monitor silent on them would be one
  that cannot fire. What is checked instead is that the charts fire on a
  healthy fab and that the realized rate is where it was measured to be.
  ADR-033 records the restatement.
"""
from __future__ import annotations

import math
import statistics as st
from collections import Counter

import pytest

from fabops.monitors import (CHART_RULES, DEFAULT_CHART_RULE, FAMILIES,
                             MONITOR, monitor)
from fabops.monitors.defect import clark_evans, linearity
from fabops.monitors.model import (BASELINE_FRACTION, MIN_PEERS, SIGMA_LIMIT,
                                   Series, limit_inflation, peer_difference,
                                   spc_signals, student_t_two_sided, t_limit)


# ------------------------------------------------------------------- unit


@pytest.mark.parametrize("df, alpha, expected", [
    (1, 0.05, 12.7062), (5, 0.02, 3.3649), (10, 0.05, 2.2281),
    (20, 0.01, 2.8453), (30, 0.001, 3.6460),
])
def test_the_t_quantile_matches_the_published_table(df, alpha, expected):
    """The limit inflation is only worth having if this is right. A continued
    fraction that is subtly wrong reads plausible and mis-sizes every chart —
    an earlier revision of this module applied its first partial numerator
    twice and was correct at ten degrees of freedom and wrong at fifteen."""
    assert t_limit(df, alpha) == pytest.approx(expected, abs=2e-3)


def test_the_t_tail_is_monotone_and_bounded():
    assert student_t_two_sided(0.0, 10) == pytest.approx(1.0, abs=1e-9)
    tails = [student_t_two_sided(t, 10) for t in (0.5, 1.0, 2.0, 4.0, 8.0)]
    assert tails == sorted(tails, reverse=True)
    assert all(0.0 <= value <= 1.0 for value in tails)


def test_the_limit_inflation_shrinks_towards_one_with_more_baseline():
    inflations = [limit_inflation(k) for k in (8, 12, 20, 40, 200)]
    assert inflations == sorted(inflations, reverse=True)
    assert inflations[0] > 1.5
    assert inflations[-1] == pytest.approx(1.0, abs=0.02)
    assert not math.isfinite(limit_inflation(2))


def _series(values, support=None):
    days = tuple(range(len(values)))
    return Series(entity="E", channel="c", days=days, values=tuple(values),
                  support=tuple(support or [1] * len(values)))


def test_a_flat_series_raises_nothing():
    """A process that does not move must not be charted as though it did."""
    values = [0.0, 0.1, -0.1, 0.05, -0.05, 0.02, -0.02, 0.03, -0.03, 0.01,
              -0.01, 0.0] * 6
    assert spc_signals(_series(values), horizon_days=len(values),
                       family="process", entity_kind="chamber") == []


def test_a_planted_step_is_found_and_dated():
    """A shift of four spreads after the baseline must be caught, and caught
    near where it starts rather than at the end of the record."""
    baseline = [0.0, 1.0, -1.0, 0.5, -0.5, 0.8, -0.8, 0.2, -0.2, 0.4,
                -0.4, 0.6, -0.6, 0.3, -0.3, 0.1, -0.1, 0.7, -0.7, 0.0]
    shifted = [value + 6.0 for value in baseline]
    signals = spc_signals(_series(baseline + shifted),
                          horizon_days=len(baseline) * 4,
                          family="process", entity_kind="chamber")
    assert signals, "a six-sigma step went unnoticed"
    first = min(signal.day_index for signal in signals)
    assert first <= len(baseline) + 6, (
        f"the step began at day {len(baseline)} and was first flagged on day "
        f"{first}")
    assert {"we1_beyond_3_sigma", "cusum_shift"} <= {s.rule for s in signals}


def test_a_slow_ramp_is_found_by_the_cumulative_rules_before_the_point_rule():
    """The shape this fab's mechanisms actually produce. Rule 1 is nearly blind
    to it, which is why EWMA and CUSUM are in the family at all."""
    import random

    rng = random.Random(11)
    baseline = [rng.gauss(0.0, 1.0) for _ in range(20)]
    ramp = [rng.gauss(0.0, 1.0) + 0.12 * index for index in range(60)]
    signals = spc_signals(_series(baseline + ramp), horizon_days=80,
                          family="process", entity_kind="chamber")
    rules = {signal.rule: signal.day_index for signal in signals}
    assert "cusum_shift" in rules
    assert rules["cusum_shift"] <= rules.get("we1_beyond_3_sigma", 10 ** 6)


def test_peer_difference_refuses_an_entity_with_too_few_peers():
    """The same floor the engine and the reference queries carry: with one peer
    there is no spread, and a number computed anyway is a refusal in disguise."""
    series = {"a": {0: 1.0, 1: 2.0}, "b": {0: 1.0, 1: 2.0},
              "c": {0: 1.0, 1: 2.0},
              "pair_1": {0: 3.0, 1: 3.0}, "pair_2": {0: 4.0, 1: 4.0},
              "lonely": {0: 5.0, 1: 5.0}}
    roles = {"a": "X", "b": "X", "c": "X",
             "pair_1": "Y", "pair_2": "Y", "lonely": "Z"}
    out = peer_difference(series, roles)
    assert set(out) == {"a", "b", "c"}, (
        "a role with two members gives each of them one peer, and one peer is "
        "not a spread")
    assert MIN_PEERS == 2


def test_the_spatial_scores_recognize_the_shapes_they_are_named_for():
    import random

    rng = random.Random(7)
    scattered = [(rng.uniform(-100, 100), rng.uniform(-100, 100))
                 for _ in range(120)]
    clumped = [(50 + rng.gauss(0, 3), 50 + rng.gauss(0, 3)) for _ in range(120)]
    line = [(index - 60 + rng.gauss(0, 0.5), rng.gauss(0, 0.5))
            for index in range(120)]

    assert clark_evans(clumped, 150.0) < clark_evans(scattered, 150.0)
    assert linearity(line) > 0.95
    assert linearity(scattered) < 0.75


# ----------------------------------------------------------- behavioural


@pytest.fixture(scope="module")
def demo_report(demo_dataset):
    return monitor(demo_dataset["db_path"])


def test_the_report_is_shaped_and_ordered(demo_report):
    payload = demo_report.to_dict()
    assert payload["schema"] == "fabops.monitor/v1"
    assert payload["generated_by"] == MONITOR
    assert set(payload["measurements"]) == set(FAMILIES)
    assert payload["settings"]["chart_rule"] == DEFAULT_CHART_RULE
    keys = [(s.family, s.entity_kind, s.entity, s.channel, s.day_index, s.rule)
            for s in demo_report.signals]
    assert keys == sorted(keys), "the report is not in a total order"


def test_the_same_dataset_gives_the_same_report(demo_dataset, demo_report):
    again = monitor(demo_dataset["db_path"])
    assert again.to_dict() == demo_report.to_dict()


def test_a_signal_count_is_not_attribution(library):
    """The finding that stopped a capability claim, pinned so it cannot be
    quietly re-made.

    On the demo the planted chamber leads the fab 52 signals to 18, and that
    reads exactly like attribution. Across the library it is not: the *same*
    chamber leads on scenarios whose fault is planted elsewhere, because a
    chart whose baseline fortnight happened to be quiet keeps tight limits
    afterwards. This asserts the refutation in both directions — the leader
    must be a chamber that is *not* planted somewhere, and the demo's own
    ranking must still hold — so a future change that made the count genuinely
    attributive fails here and has to come with its own measurement.
    """
    from tests.fabops.conftest import planted_of

    leaders: dict[str, str] = {}
    for name, record in sorted(library.items()):
        counts = Counter(signal.entity
                         for signal in monitor(record["db_path"]).signals
                         if signal.entity_kind == "chamber")
        if counts:
            leaders[name] = max(sorted(counts), key=lambda e: (counts[e], e))

    assert len(leaders) >= 4, leaders
    planted = {name: planted_of(record) for name, record in library.items()}
    misattributed = [name for name, leader in leaders.items()
                     if planted.get(name) not in (None, leader)]
    assert misattributed, (
        "on every faulted scenario the busiest chamber was the planted one. "
        "That would be attribution, which this instrument does not claim and "
        "has not been benchmarked for — measure it against fault-free worlds "
        "and give it an ADR before relying on it.")
    assert leaders.get("chamber_edge_uniformity") == planted[
        "chamber_edge_uniformity"], (
        "the demo's own ranking moved; the monitors stopped responding to the "
        "one fault they were measured against")


def test_the_process_family_reports_its_denominators(demo_report):
    """A count without its denominator ranks chambers by how much data they
    have. Both are reported, and the note that says so must survive."""
    per_chamber = demo_report.measurements["process"]["per_chamber"]
    assert per_chamber
    for entity, record in per_chamber.items():
        assert record["charted_points"] >= 0
        if record["charted_points"]:
            assert record["rate"] == pytest.approx(
                record["signals"] / record["charted_points"], abs=1e-5)
    note = demo_report.measurements["process"]["per_chamber_note"]
    assert "NOT a ranking" in note and "fabops.diagnosis" in note


def test_the_families_that_should_see_it_do(demo_report):
    families = {signal.family for signal in demo_report.signals}
    assert "process" in families
    assert "yield" in families


def test_an_unknown_family_or_chart_rule_is_refused(demo_dataset):
    with pytest.raises(ValueError, match="unknown families"):
        monitor(demo_dataset["db_path"], families=("astrology",))
    with pytest.raises(ValueError, match="unknown chart rule"):
        monitor(demo_dataset["db_path"], chart_rule="vibes")


# ------------------------------------------------------------ calibration


def test_a_fault_free_world_produces_signals(null_dataset):
    """`EXPANSION_ROADMAP` Phase 3 asks the monitors to be "quiet on the null
    scenario". Taken literally that is the wrong criterion and this is the
    check that replaces it.

    This fab's fault-free worlds alarm, escalate and get repaired by design
    (`ANTI_LEAKAGE_DESIGN.md` §3.2), and every chamber carries a permanent
    benign offset because rule F11 requires it. A monitor silent on such a
    world would be a monitor that cannot fire — and the single-world
    comparison the original criterion implies is the one-draw trap ADR-025 §5
    fell into. What is checked instead is that the charts *do* fire on a
    healthy fab, with the realized rate pinned by the population test below.
    """
    healthy = monitor(null_dataset["db_path"])
    assert healthy.signals, (
        "a fault-free world produced no signals at all; this fab's null worlds "
        "alarm and get repaired by design, so silence means the charts cannot "
        "fire")
    assert {s.family for s in healthy.signals} & {"process", "yield", "defect"}


@pytest.mark.parametrize("chart_rule", CHART_RULES)
def test_every_registered_chart_rule_runs_and_is_ordered(null_dataset,
                                                         chart_rule):
    """A registry member nobody exercises is a registry member that has
    quietly stopped working — the risk `trend_contrast` carried in the engine's
    statistic registry until Phase 6 measured it."""
    report = monitor(null_dataset["db_path"], chart_rule=chart_rule)
    assert report.to_dict()["settings"]["chart_rule"] == chart_rule
    keys = [(s.family, s.entity_kind, s.entity, s.channel, s.day_index, s.rule)
            for s in report.signals]
    assert keys == sorted(keys)


def test_the_default_chart_rule_is_the_best_calibrated_one(null_population):
    """The measurement that chose the default, re-run on the suite's own
    fault-free population and pinned in both directions.

    Measured when the rule was chosen, on twelve independent fault-free worlds:
    `individuals` 116 signals per dataset, `xbar` 135, `moving_range` 233. The
    default must stay the quietest, and the numbers must stay in the band the
    module's docstring publishes — a monitor whose realized rate drifted would
    make every statement about it wrong without failing anything else.
    """
    sample = [record["db_path"] for record in null_population[:8]]
    means = {}
    for rule in CHART_RULES:
        counts = [len(monitor(path, chart_rule=rule).signals)
                  for path in sample]
        means[rule] = st.fmean(counts)
    assert means[DEFAULT_CHART_RULE] == min(means.values()), means
    assert 60 <= means[DEFAULT_CHART_RULE] <= 200, (
        f"the default rule's fault-free signal count moved out of the band the "
        f"module documents: {means}")


def test_the_baseline_convention_is_declared_and_used(demo_report):
    """A fault inside the baseline window is absorbed. That is a real limit of
    a frozen chart and it is published rather than discovered."""
    assert 0.0 < BASELINE_FRACTION < 0.5
    cut = demo_report.horizon_days * BASELINE_FRACTION
    charted = [s for s in demo_report.signals
               if s.family in ("process", "defect")]
    assert charted
    assert all(signal.day_index >= int(cut) for signal in charted), (
        "a chart fired inside its own baseline window")
    assert SIGMA_LIMIT == 3.0
