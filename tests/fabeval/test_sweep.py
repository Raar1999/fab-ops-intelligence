"""
Tests for A6's severity sweep and the natural-variation floor.

The floor is the load-bearing part and the easiest thing to get quietly
wrong, so most of what is pinned here is about *it*: that it needs more than
one null world, that it is the worst chamber rather than the planted one, and
that a sweep read against an under-sampled floor is refused rather than
flattered.

The expensive fixtures build real datasets, because a floor computed from a
synthetic score mapping would test the arithmetic and not the property.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabeval.acceptance import BLOCKED, PARTIAL, PASS, check_a6
from fabeval.sweep import (
    CHANNELS,
    MINIMUM_FLOOR_SEEDS,
    channel_scores,
    natural_variation_floor,
    severity_sweep,
    summarize,
)

SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"
DEMO = "chamber_edge_uniformity"
LABEL = "ETCH-02/B"


@pytest.fixture(scope="module")
def sweep_datasets(world, tmp_path_factory):
    """One scenario at each rung of the ladder."""
    from fabeval.matrix import build_sweep

    return build_sweep(tmp_path_factory.mktemp("sweep"), world=world)


@pytest.fixture(scope="module")
def nulls(world, tmp_path_factory):
    """Three null worlds — the floor needs more than one draw."""
    from fabsim.emit import build_dataset
    from fabsim.scenario import load_scenario

    root = tmp_path_factory.mktemp("nulls")
    config = load_scenario(SCENARIO_ROOT / "null_baseline.json")
    return [build_dataset(config, seed, world=world, root=root / str(seed),
                          created_at="floor")
            for seed in (42, 101, 2024)]


# ------------------------------------------------------------------ the sweep


def test_the_sweep_orders_the_ladder_and_reads_realized_severity(
        sweep_datasets):
    readings = severity_sweep(sweep_datasets, LABEL)
    assert [r.severity for r in readings] == ["subtle", "moderate", "obvious"]
    realized = [r.realized_sigma for r in readings]
    assert realized == sorted(realized), realized
    # Realized, never configured: severity is a target and the realization is
    # what a benchmark can score (ADR-016 §4).
    assert realized[0] != 1.5 and realized[1] != 3.0


def test_the_sweep_reports_every_declared_channel(sweep_datasets):
    for reading in severity_sweep(sweep_datasets, LABEL):
        assert set(reading.standing) == set(CHANNELS), reading.severity
        for channel, (sigma, rank, total) in reading.standing.items():
            assert isinstance(sigma, float)
            assert 1 <= rank <= total


def test_the_difficulty_axis_exists(sweep_datasets, nulls):
    """A6's own words. It does not require every observable to be monotone —
    alarm counts saturate against the refractory window, and a signed
    latent's defect channel can go either way (ADR-018)."""
    outcome = summarize(severity_sweep(sweep_datasets, LABEL),
                        natural_variation_floor(nulls))
    assert outcome["realized_rises_with_severity"], outcome["realized"]
    assert outcome["monotone_channels"], "no channel rises with severity"


# ------------------------------------------------------------------ the floor


def test_the_floor_is_the_worst_chamber_not_a_chosen_one(nulls):
    """A diagnosis engine facing an unknown dataset cannot excuse a chamber,
    so the floor is the largest standing *any* chamber reaches."""
    floor = natural_variation_floor(nulls)
    for channel, reading in floor.items():
        scores_per_seed = [channel_scores(n, channel) for n in nulls]
        from fabeval.queries import zscore

        expected = max(abs(zscore(s, label))
                       for s in scores_per_seed for label in s)
        assert reading.worst == pytest.approx(expected), channel
        assert reading.worst >= reading.mean


def test_a_floor_needs_more_than_one_null(nulls):
    """One null world is one draw. Measured on the baseline, the worst
    chamber's edge-CD standing is 2.31 sigma at seed 42 and 2.84 at seed 2024
    — a floor from the lucky seed lets a moderate fault look separated when
    it is not, which is exactly how a benchmark manufactures evidence."""
    assert MINIMUM_FLOOR_SEEDS >= 3
    with pytest.raises(ValueError, match="at least"):
        natural_variation_floor(nulls[:1])
    with pytest.raises(ValueError, match="at least"):
        natural_variation_floor(nulls[:2])

    floor = natural_variation_floor(nulls)
    per_seed = floor["edge_cd"].per_seed
    assert len(per_seed) == 3
    assert max(per_seed) > min(per_seed), \
        "the seeds must actually disagree, or the point is moot"


def test_the_null_floor_is_not_trivially_small(nulls):
    """If a healthy world's worst chamber sat at 0.5 sigma, every fault would
    clear the floor and the comparison would prove nothing."""
    floor = natural_variation_floor(nulls)
    for channel, reading in floor.items():
        assert reading.worst > 1.5, (channel, reading.worst)


# --------------------------------------------------------------- the verdict


def test_a6_refuses_an_undersampled_floor(sweep_datasets, nulls):
    """The mutation that matters: with one null the checker must not score.

    An earlier version of this evaluator did exactly that and reported A6 as
    PASS, because seed 42's floor happens to sit below the moderate fault's
    standing. The refusal is what stops that recurring.
    """
    verdict = check_a6([], sweep=sweep_datasets, nulls=nulls[:1])
    assert verdict.status == PARTIAL
    assert "null realization" in verdict.detail


def test_a6_is_partial_because_moderate_does_not_clear_the_floor(
        sweep_datasets, nulls):
    """The measured verdict, with its evidence.

    At moderate the planted chamber reaches edge_cd 2.65, edge_defect_share
    2.34, alarms 4.34 and yield_split 1.25; the three-seed floor is 2.84,
    3.29, 5.05 and 2.26. Ranking first is not separation - on a null world
    some chamber always ranks first.
    """
    readings = severity_sweep(sweep_datasets, LABEL)
    floor = natural_variation_floor(nulls)
    outcome = summarize(readings, floor)
    moderate = next(r for r in readings if r.severity == "moderate")

    for channel, (sigma, _rank, _total) in moderate.standing.items():
        assert sigma <= floor[channel].worst, (
            channel, sigma, floor[channel].worst)
    assert outcome["separated_at_moderate"] == []

    verdict = check_a6([], sweep=sweep_datasets, nulls=nulls)
    assert verdict.status == PARTIAL
    assert "does not exceed the natural-variation floor" in verdict.detail


def test_a6_would_report_pass_if_the_evidence_were_there(sweep_datasets,
                                                         nulls):
    """A6's PARTIAL is a measurement, not a stub.

    Feed the summarizer a floor the moderate reading really does clear and it
    reports separation — so today's PARTIAL says something about the
    simulator rather than about the checker.
    """
    from dataclasses import replace

    readings = severity_sweep(sweep_datasets, LABEL)
    floor = natural_variation_floor(nulls)
    lowered = {c: replace(f, worst=f.worst / 4.0) for c, f in floor.items()}
    outcome = summarize(readings, lowered)
    assert outcome["separated_at_moderate"], \
        "a quarter-height floor must be clearable"


def test_the_sweep_changes_nothing_on_disk(sweep_datasets, nulls):
    """`fabeval` grades; it does not write."""
    before = {p: p.stat().st_mtime
              for d in list(sweep_datasets.values()) + list(nulls)
              for p in d.directory.rglob("*") if p.is_file()}
    severity_sweep(sweep_datasets, LABEL)
    natural_variation_floor(nulls)
    after = {p: p.stat().st_mtime for p in before}
    assert before == after
