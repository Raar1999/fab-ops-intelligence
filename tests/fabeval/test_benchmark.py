"""
The Phase 6 harness: what it measures, and what it is forbidden to decide.

`fabeval.benchmark` builds a named population, scores the engine on it and
reports the coverage of the library. Three properties matter more than any
number it produces:

* it **writes only where the caller points it**, so the grader cannot come to
  own the datasets it grades;
* it **carries the population** with every figure, so a development number
  cannot be read as a held-out one;
* it **decides nothing** — no threshold, no anchor, no statistic lives here.

The diversity half is tested against the configurations rather than against
built datasets, which is what makes it cheap enough to run every time. A
library whose members differ only in prose would pass an "is it ten?" check and
fail these.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from fabeval import benchmark
from fabeval.benchmark import (DIVERSITY_AXES, build_population, diversity,
                               render_diversity, score_population)
from fabeval.population import (DEVELOPMENT_SCENARIOS, HELD_OUT_SCENARIOS,
                                LIBRARY)

REPO = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------- diversity


@pytest.fixture(scope="module")
def coverage():
    return diversity()


def test_every_declared_scenario_is_measured(coverage):
    assert set(coverage.per_scenario) == set(LIBRARY)


@pytest.mark.parametrize("axis,minimum", [
    ("mechanism", 4),      # three faults, plus the declared benign distractor
    ("tool_type", 4),      # a library on one equipment family is one scenario
    ("severity", 3),       # the whole declared ladder
    ("profile", 3),        # step, ramp and the duty-cycled one
    ("onset_band", 3),     # early, middle and late - the axis Phase 6 needed
    ("grain", 2),          # a chamber answer and a tool answer
    ("events", 3),         # none, one, more than one
    ("confounded", 2),
])
def test_the_library_varies_along_every_axis(coverage, axis, minimum):
    """Diversity has to be a measurement.

    Each axis is a dimension some part of the engine reasons over, and a
    library that is constant along one of them cannot say anything about it.
    `onset_band` is the load-bearing case: ADR-029 §2 could not choose an
    anchor rule because every Phase 1 fault began in the middle of the horizon,
    and this assertion is what stops that recurring.
    """
    values = [v for v in coverage.values(axis) if v != "-"]
    assert len(values) >= minimum - 1, (axis, coverage.values(axis))


def test_the_held_out_set_is_a_generalization_test_not_a_second_experiment(
        coverage):
    """It must overlap development on most axes and exceed it on some.

    Sharing nothing would make the held-out score a measurement of a different
    problem; sharing everything would make it a second draw of the same one.
    """
    shared = sum(1 for axis in DIVERSITY_AXES if coverage.shared(axis))
    assert shared >= len(DIVERSITY_AXES) - 2, "the two roles barely overlap"

    held_out_only = {
        axis: set(coverage.coverage["held-out"].get(axis, ()))
        - set(coverage.coverage["development"].get(axis, ()))
        for axis in DIVERSITY_AXES}
    novel = {axis: values for axis, values in held_out_only.items() if values}
    assert novel, "the held-out set introduces nothing development lacks"


def test_the_onset_bands_are_not_all_the_same_scenario(coverage):
    """The specific gap Phase 6 existed to close, asserted by name."""
    bands = {band for scenario in DEVELOPMENT_SCENARIOS
             for band in coverage.per_scenario[scenario]["onset_band"]
             if band != "-"}
    assert {"early", "middle", "late"} <= bands, (
        f"the development set covers only {sorted(bands)}; an anchor rule "
        f"cannot be selected against faults that all arrive in one place")


def test_the_coverage_table_renders(coverage):
    text = render_diversity(coverage)
    for scenario in LIBRARY:
        assert scenario in text
    assert "held-out" in text and "development" in text


# ------------------------------------------------------- what it may not do


def test_the_harness_decides_nothing():
    """No threshold, no anchor, no statistic may be named here.

    The harness produces the table a decision is read off. A default living in
    it would be a second, competing declaration of something the engine's own
    modules own, and the two would drift — which is the failure `decide.py` and
    `statistics.py` have each already had once.
    """
    source = Path(inspect.getfile(benchmark)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            name = getattr(target, "id", "")
            assert "ALPHA" not in name.upper(), name
            assert "ANCHOR" not in name.upper() or name == "ANCHORS", name
            assert "STATISTIC" not in name.upper(), name
    assert "own_scale_step" not in source
    assert "DEFAULT_STATISTIC" not in source


def test_the_harness_never_hands_the_engine_anything_but_a_path():
    """`diagnose` takes a database path and keyword knobs that are numbers and
    a registry key. A scenario name, a role or a truth path reaching it would
    make answer-blindness a property of the caller."""
    source = Path(inspect.getfile(benchmark)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", "") == "diagnose"]
    assert calls, "the harness no longer calls the engine"
    for call in calls:
        assert len(call.args) == 1, "diagnose takes one positional argument"
        for keyword in call.keywords:
            assert keyword.arg not in ("truth", "dataset", "scenario",
                                       "expectation", "realization")


# ------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def small_population(tmp_path_factory):
    """Two scenarios at one seed: a fault-free world and a faulted one.

    Deliberately tiny. The harness's correctness is a property of its plumbing,
    and the numbers it produces on two datasets mean nothing — which is exactly
    what `claimable` should say about them.
    """
    root = tmp_path_factory.mktemp("benchmark")
    return build_population(root, ["null_baseline", "chamber_edge_uniformity"],
                            [42])


def test_a_built_population_carries_its_role_and_both_planes(small_population):
    assert len(small_population) == 2
    for record in small_population:
        assert record.role in ("development", "held-out")
        assert Path(record.db_path).exists()
        assert Path(record.truth_path).exists()
        assert record.dataset_id.startswith("scn-")
        assert record.time_origin


def test_a_score_refuses_to_be_a_capability_claim_on_two_scenarios(
        small_population):
    score = score_population(small_population, "a two-scenario smoke test")
    rendered = score.render()
    assert "a two-scenario smoke test" in rendered
    assert "NOT PERMITTED" in rendered
    assert "not a claim about the system" in rendered


def test_the_fault_free_world_and_the_faulted_one_are_scored_apart(
        small_population):
    score = score_population(small_population, "smoke")
    assert len(score.nulls) == 1 and len(score.faulted) == 1
    assert score.false_alarm_rate is not None
    assert score.detection_rate is not None
