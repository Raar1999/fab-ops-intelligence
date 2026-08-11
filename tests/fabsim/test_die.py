"""
Invariant tests for `fabsim.die` — the die grid and the yield plane.

This is where the audit's central defect lived: v1 computed a wafer's yield
from a formula with `−0.08 if bad_tool` in it. So the properties pinned here
are mostly about what yield is *made of*. A die grid is real geometry on a
real disc; a die dies for reasons local to it; the yield is the count of
survivors and is never adjusted. The strongest guarantee is structural and
needs no assertion at all — `probe` is handed a timeline, the measurements and
the reported defects, and the hidden `Realization` is not one of its
parameters, so no latent value, mechanism record or defect origin is *reachable*
from the kill model. The tests below check that nothing smuggled it in anyway.

The controlled tests build their own `ProcessObservations` and
`DefectPopulation` rather than searching a realization for a wafer that
happens to suit. Holding everything constant except the one quantity under
test is the only way to show that quantity is what moved the outcome.
"""
from __future__ import annotations

import ast
import math
import os
import re
import statistics as st
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.fabsim.plane import hidden_plane_files_outside_the_root

from fabsim.defects import Defect, DefectOrigin, DefectPopulation, inspect_response
from fabsim.die import (
    COVERAGE_INSIDE,
    COVERAGE_OUTSIDE,
    COVERAGE_PARTIAL,
    DIE_MODEL,
    Die,
    DieBin,
    DiePopulation,
    die_grid,
    probe,
    probe_response,
)
from fabsim.observation import Metrology, ProcessObservations, observe, observe_response
from fabsim.response import respond_scenario
from fabsim.scenario import from_mapping
from fabsim.world import (
    DIE_INDEX_ORDERS,
    DIE_ORIGINS,
    PARTIAL_DIE_POLICIES,
    TEST_OPERATION,
    build_world,
)

SCENARIO: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "null",
    "world": "baseline_fab_v1",
    "horizon_days": 84,
    "lots": 20,
    "default_seed": 42,
}

EDGE_EVENT: dict[str, Any] = {
    "mechanism": "chamber_edge_uniformity",
    "target": {"tool": "ETCH-02", "chamber": "B"},
    "onset_day": 35,
    "profile": {"type": "ramp", "ramp_days": 7},
    "severity": "obvious",
}

_CACHE: dict[str, Any] = {}


def probed(world, **overrides: Any):
    """One scenario, run to final test. Memoized: the whole pipeline is here."""
    config = from_mapping({**SCENARIO, **overrides})
    key = config.canonical_json
    if key not in _CACHE:
        response = respond_scenario(config, world=world)
        observations = observe_response(response)
        defects = inspect_response(response)
        _CACHE[key] = (response, observations, defects,
                       probe_response(response, observations, defects))
    return _CACHE[key]


@pytest.fixture(scope="module")
def null(world):
    return probed(world)


@pytest.fixture(scope="module")
def faulted(world):
    return probed(world, events=[EDGE_EVENT])


@pytest.fixture(scope="module")
def small(world):
    """A short, thin run for the controlled tests.

    They place one defect on one die and ask what happened to it; carrying
    475 wafers through the tester to answer that is 20 seconds of arithmetic
    nobody reads. The physics is identical — same world, same grids, same
    kill model — and the population tests above use the full run.
    """
    return probed(world, horizon_days=30, lots=6)


def product_of_wafer(world, response, wafer_id: int):
    wafer = response.timeline.wafer(wafer_id)
    return world.product(response.timeline.lot(wafer.lot_id).product_id)


def without_mechanisms(realization):
    """The realization with every mechanism removed, on identical draws.

    The same hidden test instrument `test_observation` uses. It exists so a
    test can subtract the world that would have been from the world that was;
    the die engine has no access to it and no observable record carries it.
    """
    return replace(realization, trajectories=tuple(
        replace(t, values=t.counterfactual) for t in realization.trajectories))


# ------------------------------------------------------------------ geometry


def test_the_lattice_is_the_declared_geometry_and_nothing_else(world):
    """Pitch is die plus street, and the footprint is the die alone."""
    policy = world.die_grid
    for product in world.products:
        grid = die_grid(policy, product)
        assert grid.die_width_mm * grid.die_height_mm == pytest.approx(
            product.die_size_mm2)
        assert grid.die_width_mm / grid.die_height_mm == pytest.approx(
            policy.die_aspect_ratio)
        assert grid.pitch_x_mm == pytest.approx(grid.die_width_mm
                                                + policy.street_width_mm)
        assert grid.pitch_y_mm == pytest.approx(grid.die_height_mm
                                                + policy.street_width_mm)
        assert grid.wafer_radius_mm == product.wafer_size_mm / 2.0
        assert grid.usable_radius_mm == pytest.approx(
            grid.wafer_radius_mm - policy.edge_exclusion_mm)


def test_neighbouring_die_are_separated_by_exactly_one_street(world):
    """The scribe lane is real space, which is why a defect can land in it."""
    grid = die_grid(world.die_grid, world.products[0])
    street = world.die_grid.street_width_mm
    for column in range(grid.columns - 1):
        left, right = grid.at(column, 0), grid.at(column + 1, 0)
        gap = ((right.x_mm - right.width_mm / 2.0)
               - (left.x_mm + left.width_mm / 2.0))
        assert gap == pytest.approx(street)
    for row in range(grid.rows - 1):
        upper, lower = grid.at(0, row), grid.at(0, row + 1)
        gap = ((upper.y_mm - upper.height_mm / 2.0)
               - (lower.y_mm + lower.height_mm / 2.0))
        assert gap == pytest.approx(street)


def test_the_lattice_is_centred_on_the_wafer(world):
    """`wafer_center` origin: no product gets a lattice phase of its own."""
    for product in world.products:
        grid = die_grid(world.die_grid, product)
        xs = sorted(d.x_mm for d in grid.dies)
        ys = sorted(d.y_mm for d in grid.dies)
        assert xs[0] == pytest.approx(-xs[-1])
        assert ys[0] == pytest.approx(-ys[-1])
        # …and the eligible set is symmetric under a half turn, because a disc
        # is and the lattice is.
        eligible = {(round(d.x_mm, 6), round(d.y_mm, 6))
                    for d in grid.eligible}
        assert {(-x, -y) for x, y in eligible} == eligible


def test_indexing_is_row_major_from_the_top_left(world):
    """`row_major`, rows downwards — the way a wafer map is read."""
    grid = die_grid(world.die_grid, world.products[0])
    assert len(grid.dies) == grid.columns * grid.rows
    for die in grid.dies:
        assert die.index == die.row * grid.columns + die.column
    assert [d.index for d in grid.dies] == list(range(len(grid.dies)))
    assert grid.at(0, 0).x_mm < grid.at(1, 0).x_mm      # column runs with x
    assert grid.at(0, 0).y_mm > grid.at(0, 1).y_mm      # row runs downwards


def test_edge_exclusion_is_applied_to_the_footprint_not_the_centre(world):
    """The rule the contract states, and the one a centre test would miss.

    A die whose centre clears the exclusion boundary but whose corner hangs
    over it is `partial`, not `inside` — and on this world such die exist, so
    the distinction is doing work rather than being stated.
    """
    grid = die_grid(world.die_grid, world.products[0])
    hanging = 0
    for die in grid.dies:
        half_w, half_h = die.width_mm / 2.0, die.height_mm / 2.0
        # Recomputed here from the corners rather than reused from the
        # engine, so the two derivations have to agree.
        far = max(math.hypot(die.x_mm + sx * half_w, die.y_mm + sy * half_h)
                  for sx in (-1, 1) for sy in (-1, 1))
        near = math.hypot(max(0.0, abs(die.x_mm) - half_w),
                          max(0.0, abs(die.y_mm) - half_h))
        if die.coverage == COVERAGE_INSIDE:
            assert far <= grid.usable_radius_mm + 1e-6, die
        elif die.coverage == COVERAGE_OUTSIDE:
            assert near >= grid.usable_radius_mm - 1e-6, die
        else:
            assert near < grid.usable_radius_mm < far, die
            if die.radius_mm <= grid.usable_radius_mm:
                hanging += 1
    assert hanging > 0, "no die hangs over the boundary; the rule is untested"


def test_every_die_is_inside_partial_or_outside(world):
    for product in world.products:
        grid = die_grid(world.die_grid, product)
        counts = Counter(d.coverage for d in grid.dies)
        assert set(counts) == {COVERAGE_INSIDE, COVERAGE_PARTIAL,
                               COVERAGE_OUTSIDE}
        assert sum(counts.values()) == len(grid.dies)


def test_the_partial_die_policy_decides_the_grid(world):
    """The declared policy is `exclude`, and it demonstrably excludes.

    `PARTIAL_DIE_POLICIES` has one member. Testing "every supported policy"
    is therefore testing this one — and inventing a second so that a
    comparison becomes possible would be widening a versioned contract to suit
    a test. What is checked instead is that the policy is *consulted*: the
    eligible set is exactly the fully-inside die, it is strictly smaller than
    the die that touch the usable area at all, and a value outside the
    vocabulary is refused rather than quietly treated as the default.
    """
    from fabsim.die import _admits

    assert world.die_grid.partial_die_policy == "exclude"
    for product in world.products:
        grid = die_grid(world.die_grid, product)
        assert {d.index for d in grid.eligible} == {
            d.index for d in grid.dies if d.coverage == COVERAGE_INSIDE}
        touching = grid.count(COVERAGE_INSIDE) + grid.count(COVERAGE_PARTIAL)
        assert 0 < len(grid.eligible) < touching

    assert _admits("exclude", COVERAGE_INSIDE)
    assert not _admits("exclude", COVERAGE_PARTIAL)
    assert not _admits("exclude", COVERAGE_OUTSIDE)
    with pytest.raises(ValueError, match="unknown partial die policy"):
        _admits("keep_everything", COVERAGE_PARTIAL)


def test_every_declared_grid_convention_is_dispatched_not_assumed(world):
    """`origin` and `index_order` must be *read*, like `partial_die_policy`.

    All three are closed, versioned vocabularies with one member today, and a
    convention the engine merely happens to agree with is not implemented — it
    is a coincidence that a second declared value would silently break. Each
    is looked up and each refuses a value outside its vocabulary, so the
    contract has exactly one place to answer for a future member.
    """
    from dataclasses import replace as _replace

    from fabsim.die import _cell_centre, _cell_index

    policy = world.die_grid
    assert policy.origin in DIE_ORIGINS
    assert policy.index_order in DIE_INDEX_ORDERS
    assert _cell_centre(policy, 1, 1, 3, 3, 2.0, 2.0) == (0.0, 0.0)
    assert _cell_index(policy, 2, 1, 5, 5) == 7

    with pytest.raises(ValueError, match="unknown die grid origin"):
        _cell_centre(_replace(policy, origin="wafer_notch"),
                     0, 0, 3, 3, 2.0, 2.0)
    with pytest.raises(ValueError, match="unknown die index order"):
        _cell_index(_replace(policy, index_order="spiral"), 0, 0, 3, 3)


def test_a_bigger_die_means_fewer_of_them(world):
    """Product-specific geometry, from the product's own declared area."""
    counts = {p.product_name: len(die_grid(world.die_grid, p).eligible)
              for p in world.products}
    areas = {p.product_name: p.die_size_mm2 for p in world.products}
    ordered = sorted(counts, key=lambda name: areas[name])
    assert [counts[n] for n in ordered] == sorted(
        (counts[n] for n in ordered), reverse=True)
    assert len(set(counts.values())) == len(counts)


def test_a_smaller_wafer_carries_fewer_die(world, make_template):
    """Wafer size is the product's, not a constant baked into the grid."""
    template = make_template()
    for entry in template["products"]:
        if entry["name"] == "Mobile-28":
            entry["wafer_size_mm"] = 200
    smaller = build_world(template)
    big = die_grid(world.die_grid, world.product_by_name("Mobile-28"))
    small = die_grid(smaller.die_grid, smaller.product_by_name("Mobile-28"))
    assert small.wafer_radius_mm == 100.0
    assert len(small.eligible) < len(big.eligible)
    assert small.die_width_mm == pytest.approx(big.die_width_mm)


def test_the_grid_is_deterministic_and_seedless(world):
    """Same world + same product ⇒ the same lattice, with no seed involved."""
    for product in world.products:
        first = die_grid(world.die_grid, product)
        second = die_grid(world.die_grid, product)
        assert first.dies == second.dies


def test_the_eligible_area_is_close_to_the_usable_disc(world):
    """A sanity check on the whole layout, from area rather than from counts.

    Not `πr²/die_area` used *as* the model — that is what §6 of the gate
    forbids — but as an independent order-of-magnitude witness that the
    lattice really tiles the disc: the eligible die must cover most of the
    usable area and can never cover more than it.
    """
    for product in world.products:
        grid = die_grid(world.die_grid, product)
        covered = len(grid.eligible) * product.die_size_mm2
        usable = math.pi * grid.usable_radius_mm ** 2
        assert 0.75 < covered / usable < 1.0, product.product_name


# -------------------------------------------------------- a controlled wafer


def _one_wafer(world, response, observations, defects):
    """A wafer that reached the tester, with its parts, for a controlled run."""
    tested_ids = {run.wafer_id for run in response.timeline.runs
                  if world.step(run.step_id).operation_type == TEST_OPERATION}
    wafer_id = min(tested_ids)
    return wafer_id, product_of_wafer(world, response, wafer_id)


def _blank(observations: ProcessObservations) -> ProcessObservations:
    """The same observations with every metrology reading exactly on target.

    Removes the parametric risk entirely so a test can add exactly as much of
    it back as it wants.
    """
    return ProcessObservations(
        model=observations.model,
        run_measurements=observations.run_measurements,
        metrology=())


def _profile(world, response, observations, wafer_id: int, deviation: float
             ) -> ProcessObservations:
    """Metrology for one wafer, every zone `deviation` × tolerance off target.

    A flat profile: every zone equally off, so a test of *how far* off is not
    also a test of *where*.
    """
    product = product_of_wafer(world, response, wafer_id)
    rows: list[Metrology] = []
    for row in observations.of_wafer(wafer_id):
        step_id = {f.flow_step_id: f.step_id
                   for f in world.flow_steps}[row.flow_step_id]
        recipe = world.recipe_for(step_id, product.product_id)
        if recipe.metric_target is None or row.param_name.endswith("_sigma"):
            continue
        tolerance = recipe.metric_usl - recipe.metric_target
        rows.append(replace(row, value=recipe.metric_target
                            + deviation * tolerance))
    return ProcessObservations(model=observations.model,
                               run_measurements=observations.run_measurements,
                               metrology=tuple(rows))


def _defects_on(wafer_id: int, points, size_um: float, layer: str
                ) -> DefectPopulation:
    """A hand-placed defect population: exactly these defects, nowhere else."""
    rows = tuple(
        Defect(defect_id=index + 1, inspection_id=1, wafer_id=wafer_id,
               x_mm=x, y_mm=y, size_um=size_um, classified_type="PARTICLE",
               layer=layer)
        for index, (x, y) in enumerate(points))
    origins = tuple(DefectOrigin(defect_id=d.defect_id, origin="uniform",
                                 contributing_chamber_id=-1,
                                 contributing_flow_step_id=-1) for d in rows)
    return DefectPopulation(model="test", inspections=(), defects=rows,
                            origins=origins)


_EMPTY_DEFECTS = DefectPopulation(model="test", inspections=(), defects=(),
                                  origins=())


# -------------------------------------------------------------- the kill model


def test_a_probability_never_leaves_the_unit_interval(null):
    _response, _obs, _defects, population = null
    for outcome in population.outcomes:
        for probability in (outcome.p_background, outcome.p_defect,
                            outcome.p_parametric):
            assert 0.0 <= probability <= 1.0, outcome


def test_process_deviation_raises_the_failure_rate_monotonically(
        world, small):
    """Mediation test 1, on a controlled wafer.

    Everything is held fixed but the wafer's own measured departure from its
    recipe target: no defects, one product, one grid, one seed. The parametric
    risk and the realized failure rate must both rise, and the rise must be
    smooth rather than a step at a threshold.
    """
    response, observations, _defects, _population = small
    wafer_id, _product = _one_wafer(world, response, observations, _defects)

    risks, rates = [], []
    for deviation in (0.0, 1.0, 2.0, 3.0, 4.0):
        result = probe(response.timeline,
                       _profile(world, response, observations, wafer_id,
                                deviation),
                       _EMPTY_DEFECTS)
        outcomes = result.outcomes_of(wafer_id)
        assert outcomes
        risks.append(st.mean(o.p_parametric for o in outcomes))
        rates.append(sum(1 for o in outcomes if o.cause is not None)
                     / len(outcomes))

    assert risks == sorted(risks)
    assert risks[0] < 1e-3 < risks[-1]
    assert rates == sorted(rates)
    assert rates[-1] > rates[0] + 0.2
    # Smooth, not a cliff: no single step accounts for almost all of it.
    steps = [b - a for a, b in zip(risks, risks[1:])]
    assert all(step > 0.0 for step in steps)
    assert max(steps) < 0.95 * (risks[-1] - risks[0])


def test_a_defect_kills_only_the_die_it_physically_reached(world, small):
    """Mediation test 2, and the geometry it depends on.

    One defect, placed at the centre of one known die. Exactly that die may
    gain defect risk; every other die on the wafer must be untouched — which
    is what makes a defect map and a bin map spatially coupled rather than
    merely correlated.
    """
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    victim = grid.eligible[len(grid.eligible) // 2]

    result = probe(response.timeline, _blank(observations),
                   _defects_on(wafer_id, [(victim.x_mm, victim.y_mm)],
                               size_um=2.0, layer="GATE"))
    hit = [o for o in result.outcomes_of(wafer_id) if o.p_defect > 0.0]
    assert len(hit) == 1
    assert hit[0].die_index == victim.index
    assert hit[0].defect_ids == (1,)


def test_more_overlapping_defects_mean_more_defect_risk(world, small):
    """The burden a die carries composes as survivals, and saturates below 1."""
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    victim = grid.eligible[len(grid.eligible) // 2]

    risks = []
    for count in (1, 2, 4, 8):
        points = [(victim.x_mm + (index - count / 2) * 1e-3, victim.y_mm)
                  for index in range(count)]
        result = probe(response.timeline, _blank(observations),
                       _defects_on(wafer_id, points, size_um=0.45,
                                   layer="GATE"))
        (hit,) = [o for o in result.outcomes_of(wafer_id)
                  if o.die_index == victim.index]
        assert len(hit.defect_ids) == count
        risks.append(hit.p_defect)
    assert risks == sorted(risks)
    assert risks[0] == pytest.approx(0.5)     # one defect at the half size
    assert risks[-1] < 1.0                    # and never a certainty


def test_a_bigger_defect_is_more_likely_to_kill(world, small):
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    victim = grid.eligible[len(grid.eligible) // 2]

    risks = []
    for size_um in (0.15, 0.3, 0.45, 1.0, 3.0):
        result = probe(response.timeline, _blank(observations),
                       _defects_on(wafer_id, [(victim.x_mm, victim.y_mm)],
                                   size_um=size_um, layer="GATE"))
        (hit,) = [o for o in result.outcomes_of(wafer_id)
                  if o.die_index == victim.index]
        risks.append(hit.p_defect)
    assert risks == sorted(risks)
    assert risks[0] < 0.2 < risks[-1] < 1.0


def test_a_defect_in_a_scribe_lane_kills_nothing(world, small):
    """Which is why the street is modelled as real space between real die."""
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    left = grid.eligible[len(grid.eligible) // 2]
    right = grid.at(left.column + 1, left.row)
    assert right is not None and right.eligible
    middle = (left.x_mm + left.width_mm / 2.0
              + world.die_grid.street_width_mm / 2.0)

    result = probe(response.timeline, _blank(observations),
                   _defects_on(wafer_id, [(middle, left.y_mm)], size_um=0.3,
                               layer="GATE"))
    assert [o for o in result.outcomes_of(wafer_id) if o.p_defect > 0.0] == []


def test_the_layer_a_defect_was_reported_at_changes_how_lethal_it_is(world,
                                                                     small):
    """Declared per layer, and read off the observable defect row."""
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    victim = grid.eligible[len(grid.eligible) // 2]

    risks = {}
    for layer, weight in world.die_kill.defect_layer_weights:
        result = probe(response.timeline, _blank(observations),
                       _defects_on(wafer_id, [(victim.x_mm, victim.y_mm)],
                                   size_um=0.45, layer=layer))
        (hit,) = [o for o in result.outcomes_of(wafer_id)
                  if o.die_index == victim.index]
        risks[layer] = hit.p_defect
        assert hit.p_defect == pytest.approx(weight * 0.5)
    assert len(set(risks.values())) > 1


def test_two_risks_on_one_die_kill_it_once(world, small):
    """§16: a die that a defect reached *and* that missed its window is one
    dead die, not two. The survivals multiply and one draw decides."""
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    victim = grid.eligible[len(grid.eligible) // 2]

    result = probe(response.timeline,
                   _profile(world, response, observations, wafer_id, 6.0),
                   _defects_on(wafer_id, [(victim.x_mm, victim.y_mm)],
                               size_um=3.0, layer="GATE"))
    (hit,) = [o for o in result.outcomes_of(wafer_id)
              if o.die_index == victim.index]
    assert hit.p_defect > 0.5 and hit.p_parametric > 0.5
    assert hit.cause in ("background", "defect", "parametric")

    bins = result.bins_of(wafer_id)
    summary = result.yield_of(wafer_id)
    assert len(bins) == summary.total_die == len(grid.eligible)
    assert sum(1 for b in bins if b.bin_code != "PASS") == sum(
        1 for o in result.outcomes_of(wafer_id) if o.cause is not None)


def test_background_risk_is_the_poisson_model_over_the_die_area(world, null):
    """`p_bg = 1 − exp(−D₀ · A)`: the same density over more area kills more.

    The realized densities scatter about each product's declared mean, so the
    check is on the *median* wafer of each product — and on the ordering,
    which the scatter cannot invert at this spread.
    """
    response, _obs, _defects, population = null
    by_product: dict[str, list[float]] = defaultdict(list)
    seen: dict[int, float] = {}
    for outcome in population.outcomes:
        if outcome.wafer_id in seen:
            continue
        seen[outcome.wafer_id] = outcome.p_background
        product = product_of_wafer(world, response, outcome.wafer_id)
        by_product[product.product_name].append(outcome.p_background)

    assert len(set(seen.values())) > 50          # not one constant per fab
    nominal, realized = {}, {}
    for product in world.products:
        values = by_product.get(product.product_name)
        if not values:
            continue
        nominal[product.product_name] = 1.0 - math.exp(
            -product.killer_density_per_mm2 * product.die_size_mm2)
        realized[product.product_name] = st.median(values)

    assert len(nominal) >= 4
    for name in nominal:
        assert realized[name] == pytest.approx(nominal[name], rel=0.35), name
    order = sorted(nominal, key=lambda n: nominal[n])
    assert [realized[n] for n in order] == sorted(realized[n] for n in order)


def test_a_wafers_background_density_varies_around_its_product(world, null):
    """Benign structure, and the reason a null fab's yield moves at all.

    Two wafers of one product genuinely differ in how dirty their process
    was. Without it the only wafer-to-wafer variation would be binomial noise
    over a few thousand die, and `CAUSAL_MECHANISM_MODEL.md` §2's declared
    budget would be missed by a factor of five — a null world that uniform
    makes any fault separable at a glance.
    """
    response, _obs, _defects, population = null
    per_wafer: dict[str, dict[int, float]] = defaultdict(dict)
    for outcome in population.outcomes:
        product = product_of_wafer(world, response, outcome.wafer_id)
        per_wafer[product.product_name][outcome.wafer_id] = (
            outcome.p_background)

    for name, values in per_wafer.items():
        levels = list(values.values())
        if len(levels) < 20:
            continue
        assert len(set(levels)) == len(levels), name     # per wafer, not shared
        assert 0.05 < st.pstdev(levels) / st.mean(levels) < 0.5, name


# ------------------------------------------------------------------- binning


def test_a_bin_is_a_symptom_and_not_the_cause(world, null):
    """`die_bins` is evidence, not the kill model's answer written out.

    Every cause reaches more than one bin and every bin arises from more than
    one cause, so the observable plane cannot be read back into the hidden
    one — the same property the defect classifier has, for the same reason.
    """
    response, _obs, _defects, population = null
    tester = world.die_kill.tester
    by_die = {(o.wafer_id, o.die_index): o.cause for o in population.outcomes}

    # Join bins to causes through the grid position each was recorded at.
    index_of: dict[int, dict[tuple[int, int], int]] = {}
    for grid in population.grids:
        for die in grid.eligible:
            index_of.setdefault(grid.product_id, {})[
                (die.column, die.row)] = die.index

    pairs: Counter = Counter()
    product_of: dict[int, int] = {}
    for row in population.die_bins:
        if row.wafer_id not in product_of:
            product_of[row.wafer_id] = product_of_wafer(
                world, response, row.wafer_id).product_id
        die_index = index_of[product_of[row.wafer_id]][(row.die_x, row.die_y)]
        cause = by_die[(row.wafer_id, die_index)]
        pairs[(cause, row.bin_code)] += 1

    passing = {code for cause, code in pairs if cause is None}
    assert passing == {tester.pass_code}
    for code in tester.fail_codes:
        causes = {cause for (cause, seen), count in pairs.items()
                  if seen == code and cause is not None and count > 20}
        assert len(causes) > 1, code
    for cause in ("background", "defect", "parametric"):
        codes = {code for (seen, code), count in pairs.items()
                 if seen == cause and count > 20}
        assert len(codes) > 1, cause

    # …and the leading symptom of each cause is the declared one, so the rows
    # are being used rather than merely present.
    for cause in ("background", "defect", "parametric"):
        realized = {code: count for (seen, code), count in pairs.items()
                    if seen == cause}
        declared = dict(tester.row(cause))
        assert (max(realized, key=realized.get)
                == max(declared, key=declared.get)), cause

def test_the_hidden_cause_never_reaches_the_observable_die_row(null):
    """The two planes are separate collections, and the observable one has
    nowhere to put a cause even if an emitter wanted to."""
    _response, _obs, _defects, population = null
    assert set(DieBin.__dataclass_fields__) == {"wafer_id", "die_x", "die_y",
                                                "bin_code"}
    for row in population.die_bins[:200]:
        assert not hasattr(row, "cause")
        assert not hasattr(row, "killer_flag")
    assert set(DiePopulation.__dataclass_fields__) >= {
        "grids", "die_bins", "wafer_yield", "outcomes"}


# --------------------------------------------------------------------- yield


def test_yield_is_the_die_grid_summed_and_nothing_else(null):
    """§18: `good ÷ eligible`, reconciled against the bins that produced it."""
    _response, _obs, _defects, population = null
    assert population.wafer_yield
    for summary in population.wafer_yield:
        bins = population.bins_of(summary.wafer_id)
        assert summary.total_die == len(bins)
        assert summary.good_die == sum(1 for b in bins
                                       if b.bin_code == "PASS")
        assert summary.yield_pct == pytest.approx(
            100.0 * summary.good_die / summary.total_die)
        assert 0 <= summary.good_die <= summary.total_die


def test_the_die_count_is_the_products_own_grid(world, null):
    """Not a fixed number, and not the same number for two products."""
    response, _obs, _defects, population = null
    totals = defaultdict(set)
    for summary in population.wafer_yield:
        product = product_of_wafer(world, response, summary.wafer_id)
        totals[product.product_name].add(summary.total_die)
    assert len(totals) > 3
    for name, values in totals.items():
        assert len(values) == 1, name           # constant within a product
        grid = die_grid(world.die_grid, world.product_by_name(name))
        assert values == {len(grid.eligible)}
    assert len({next(iter(v)) for v in totals.values()}) == len(totals)


def test_only_wafers_that_reached_the_tester_have_a_yield(world, null):
    response, _obs, _defects, population = null
    reached = {run.wafer_id for run in response.timeline.runs
               if world.step(run.step_id).operation_type == TEST_OPERATION}
    assert {y.wafer_id for y in population.wafer_yield} == reached
    assert 0 < len(reached) < len(response.timeline.wafers)
    for summary in population.wafer_yield:
        last = max(run.end_min for run in response.timeline.runs
                   if run.wafer_id == summary.wafer_id)
        assert summary.test_time_min == last


# ---------------------------------------------------------------- the null


@pytest.mark.parametrize("seed", [7, 42, 101])
def test_the_null_world_yields_are_realistic_and_not_degenerate(world, seed):
    """§24: a null fab makes die, loses some of them, and varies while doing it.

    Checked against the *declared product specification* — `target_yield_pct`
    is a property of the product that a fab records, and no engine reads it
    (`test_the_die_engine_reads_no_target_yield`). The spread is checked
    against `CAUSAL_MECHANISM_MODEL.md` §2's stated budget rather than a
    number invented here.
    """
    response, _obs, _defects, population = probed(world, default_seed=seed)
    by_product = defaultdict(list)
    for summary in population.wafer_yield:
        product = product_of_wafer(world, response, summary.wafer_id)
        by_product[product].append(summary.yield_pct)

    assert len(by_product) >= 4
    for product, yields in by_product.items():
        assert len(yields) >= 20, product.product_name
        mean = st.mean(yields)
        spread = st.pstdev(yields)
        assert 60.0 < mean < 99.0, product.product_name
        assert abs(mean - product.target_yield_pct) < 3.0, product.product_name
        assert 0.5 < spread < 6.0, product.product_name
        # A distribution, not a spike: `yield_pct` is `good ÷ a fixed die
        # count`, so distinct values are bounded by the realized range rather
        # than by the wafer count — the range against the spread is the
        # statement worth making.
        assert max(yields) - min(yields) > 3.0 * spread, product.product_name
        assert len(set(yields)) > 10, product.product_name
        assert max(yields) < 100.0                   # no perfect wafer


@pytest.mark.parametrize("seed", [7, 42, 101])
def test_the_null_world_uses_every_way_of_dying(world, seed):
    """All three risks and all bin codes occur where nothing is wrong.

    A bin that only appeared when something was wrong would be an answer, and
    so would a kill cause with no support in the null (rule D2).
    """
    _response, _obs, _defects, population = probed(world, default_seed=seed)
    causes = Counter(o.cause for o in population.outcomes)
    for cause in ("background", "defect", "parametric"):
        assert causes[cause] > 50, cause
    codes = Counter(b.bin_code for b in population.die_bins)
    assert set(codes) == {"PASS", "OPEN_SHORT", "PARAM", "LEAK", "OTHER"}
    assert all(count > 100 for count in codes.values())


def test_the_null_world_loses_die_at_the_edge_more_than_at_the_centre(world,
                                                                      null):
    """§22: the spatial tendency, in a world with no fault in it.

    Edge die die more often than centre die on a healthy fab too — the
    benign radial term of the observation model reaches them, and edge-ring
    defects land on them. If this gradient existed *only* under a fault it
    would be the fingerprint the whole design is built to avoid.
    """
    response, _obs, _defects, population = null
    buckets = _radial_failure(world, response, population)
    assert buckets[-1] > buckets[0]
    # …and it is a tendency, not a partition: the centre is far from safe.
    assert buckets[0] > 0.5 * buckets[-1]


def _radial_failure(world, response, population, wafers=None, bins=5):
    """Realized die failure rate by radial fifth of the usable wafer."""
    grids = {p.product_id: die_grid(world.die_grid, p) for p in world.products}
    products = {}
    counts = [[0, 0] for _ in range(bins)]
    for outcome in population.outcomes:
        if wafers is not None and outcome.wafer_id not in wafers:
            continue
        if outcome.wafer_id not in products:
            products[outcome.wafer_id] = product_of_wafer(
                world, response, outcome.wafer_id).product_id
        grid = grids[products[outcome.wafer_id]]
        die = grid.dies[outcome.die_index]
        index = min(bins - 1,
                    int(bins * die.radius_mm / grid.usable_radius_mm))
        counts[index][1] += 1
        if outcome.cause is not None:
            counts[index][0] += 1
    return [dead / total if total else 0.0 for dead, total in counts]


def test_an_edge_ring_of_defects_kills_at_the_edge_and_not_at_the_centre(
        world, small):
    """The spatial coupling 3D's geometry buys, measured at the die plane."""
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)

    ring = grid.usable_radius_mm * 0.9
    points = []
    for step in range(360):
        angle = math.radians(step)
        points.append((ring * math.cos(angle), ring * math.sin(angle)))
    result = probe(response.timeline, _blank(observations),
                   _defects_on(wafer_id, points, size_um=3.0, layer="GATE"))

    inner, outer = [], []
    for outcome in result.outcomes_of(wafer_id):
        die = grid.dies[outcome.die_index]
        (outer if die.radius_mm > 0.8 * grid.usable_radius_mm
         else inner).append(outcome.p_defect)
    assert st.mean(outer) > 10 * max(1e-9, st.mean(inner))
    assert st.mean(inner) == 0.0


def test_a_centre_blob_of_defects_kills_at_the_centre(world, small):
    """The mirror of the ring: geometry decides, not a label."""
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    points = [(0.4 * grid.die_width_mm * (index % 5 - 2),
               0.4 * grid.die_height_mm * (index // 5 - 2))
              for index in range(25)]
    result = probe(response.timeline, _blank(observations),
                   _defects_on(wafer_id, points, size_um=3.0, layer="GATE"))
    touched = [grid.dies[o.die_index].radius_mm
               for o in result.outcomes_of(wafer_id) if o.p_defect > 0.0]
    assert touched
    assert max(touched) < 0.2 * grid.usable_radius_mm


def test_a_scratch_kills_along_its_own_line(world, small):
    """A stroke of defects takes out a stroke of die — anisotropic, like the
    3D geometry that produced it."""
    response, observations, _defects, _population = small
    wafer_id, product = _one_wafer(world, response, observations, _defects)
    grid = die_grid(world.die_grid, product)
    span = grid.usable_radius_mm * 0.8
    points = [(-span + 2 * span * index / 200.0, 0.0) for index in range(201)]
    result = probe(response.timeline, _blank(observations),
                   _defects_on(wafer_id, points, size_um=3.0, layer="GATE"))
    hit = [grid.dies[o.die_index] for o in result.outcomes_of(wafer_id)
           if o.p_defect > 0.0]
    assert len(hit) > 10
    xs = [d.x_mm for d in hit]
    ys = [d.y_mm for d in hit]
    assert st.pstdev(xs) > 5 * max(1e-9, st.pstdev(ys))


# ----------------------------------------------------------- causal mediation


def test_a_mechanism_reaches_a_die_only_through_what_the_fab_observed(
        world, faulted):
    """The subtraction, at the die plane.

    The same timeline is tested twice: once against the measurements and
    defects the fault produced, and once against the ones its mechanism-free
    twin would have produced on identical draws. Every difference in the die
    plane is therefore attributable to an observable, because nothing else
    differs — and on the wafers the affected chamber never processed, there is
    **no** difference at all.
    """
    response, observations, defects, population = faulted
    twin = without_mechanisms(response.realization)
    from fabsim.defects import inspect

    shadow = probe(response.timeline,
                   observe(response.timeline, twin),
                   inspect(response.timeline, twin))

    target = world.chamber_by_name("ETCH-02", "B").chamber_id
    onset = EDGE_EVENT["onset_day"] * 24 * 60
    exposed = {run.wafer_id for run in response.timeline.runs
               if run.chamber_id == target and run.end_min >= onset}
    assert exposed

    def risks(result):
        return {(o.wafer_id, o.die_index): (o.p_defect, o.p_parametric)
                for o in result.outcomes}

    realized, counterfactual = risks(population), risks(shadow)
    assert set(realized) == set(counterfactual)
    moved = {key for key in realized
             if realized[key] != counterfactual[key]}
    assert moved, "the mechanism reached no die at all"
    assert {wafer for wafer, _die in moved} <= exposed


def test_a_larger_edge_departure_raises_the_edge_risk_it_produced(world):
    """Continuity of the whole chain, measured at its far end.

    latent → observation → local |CD − target| at a die's radius → risk.
    Severity is the only thing that changes; the die plane never learns what
    a mechanism is, let alone which one this was.
    """
    target = world.chamber_by_name("ETCH-02", "B").chamber_id
    onset = EDGE_EVENT["onset_day"] * 24 * 60
    risks = []
    for severity in (None, "moderate", "obvious"):
        events = ([] if severity is None
                  else [dict(EDGE_EVENT, severity=severity)])
        response, _obs, _defects, population = probed(world, events=events)
        exposed = {run.wafer_id for run in response.timeline.runs
                   if run.chamber_id == target and run.end_min >= onset}
        grids = {p.product_id: die_grid(world.die_grid, p)
                 for p in world.products}
        outer = []
        for outcome in population.outcomes:
            if outcome.wafer_id not in exposed:
                continue
            grid = grids[product_of_wafer(world, response,
                                          outcome.wafer_id).product_id]
            die = grid.dies[outcome.die_index]
            if die.radius_mm > 0.8 * grid.usable_radius_mm:
                outer.append(outcome.p_parametric)
        risks.append(st.mean(outer))
    assert risks == sorted(risks), risks
    assert risks[-1] > 1.2 * risks[0]


def test_the_hidden_defect_origin_changes_nothing(world, null):
    """§21 test 3: rewrite every hidden origin, keep every observable defect
    property, and the die plane must come out bit-identical."""
    response, observations, defects, population = null
    rewritten = replace(defects, origins=tuple(
        replace(origin, origin="scratch", contributing_chamber_id=999,
                contributing_flow_step_id=999)
        for origin in defects.origins))
    assert rewritten.origins != defects.origins
    again = probe(response.timeline, observations, rewritten)
    assert again.content_sha256() == population.content_sha256()


def test_the_classified_type_changes_nothing(world, null):
    """The observable class is a noisy label; a die is killed by a physical
    defect, not by what a classifier decided to call it."""
    response, observations, defects, population = null
    relabelled = replace(defects, defects=tuple(
        replace(row, classified_type="SCRATCH") for row in defects.defects))
    again = probe(response.timeline, observations, relabelled)
    assert again.content_sha256() == population.content_sha256()


def test_the_die_plane_is_a_function_of_its_three_arguments(world, null):
    """§21 test 4, structurally: the mechanism is not an argument.

    `probe` takes a timeline, the measurements and the reported defects. There
    is no `Realization` parameter, so a scenario, a mechanism, a severity, a
    latent trajectory and a hidden defect origin are not merely unread — they
    are unreachable. Two calls with the same three arguments agree exactly,
    whatever else the process happens to be holding.
    """
    import inspect as inspect_module

    parameters = list(inspect_module.signature(probe).parameters)
    assert parameters == ["timeline", "observations", "population"]
    response, observations, defects, population = null
    assert probe(response.timeline, observations,
                 defects).content_sha256() == population.content_sha256()


def test_healthy_and_affected_wafers_still_overlap(world, faulted):
    """§23: adding the yield layer must not make the answer readable at sight.

    An `obvious` edge fault, compared within one product so the comparison is
    not a product-mix artefact. The requirement is *overlap*, not containment:
    the affected wafers must sit inside the spread the unaffected ones already
    cover, measured as the fraction of each group that falls in the other's
    central range. A single unusually good or bad wafer is ordinary; two
    distributions that no longer meet would be the fingerprint.
    """
    response, _obs, _defects, population = faulted
    target = world.chamber_by_name("ETCH-02", "B").chamber_id
    onset = EDGE_EVENT["onset_day"] * 24 * 60
    exposed = {run.wafer_id for run in response.timeline.runs
               if run.chamber_id == target and run.end_min >= onset}

    by_product = defaultdict(lambda: ([], []))
    for summary in population.wafer_yield:
        product = product_of_wafer(world, response, summary.wafer_id)
        hit, rest = by_product[product.product_name]
        (hit if summary.wafer_id in exposed else rest).append(
            summary.yield_pct)

    def span(values):
        ordered = sorted(values)
        return (ordered[len(ordered) // 10],
                ordered[-1 - len(ordered) // 10])

    compared = 0
    for name, (hit, rest) in by_product.items():
        if len(hit) < 5 or len(rest) < 20:
            continue
        compared += 1
        low, high = span(rest)
        inside = sum(1 for value in hit if low <= value <= high)
        assert inside >= 0.6 * len(hit), (name, inside, len(hit))
        # …and the affected group's median is an ordinary value for the rest.
        assert min(rest) < st.median(hit) < max(rest), name
    assert compared >= 3


# ------------------------------------------------------------ reproducibility


def test_the_same_inputs_give_the_same_die_plane(world, null):
    response, observations, defects, population = null
    again = probe(response.timeline, observations, defects)
    assert again.content_sha256() == population.content_sha256()
    assert again.die_bins == population.die_bins
    assert again.wafer_yield == population.wafer_yield


def test_a_different_seed_gives_a_different_realization(world, null):
    _response, _obs, _defects, population = null
    other = probed(world, default_seed=101)[3]
    assert other.content_sha256() != population.content_sha256()
    # …but the same *structure*: the grid is geometry, not a draw.
    assert [len(g.eligible) for g in other.grids] == [
        len(g.eligible) for g in population.grids]


def test_an_unrelated_stream_does_not_reshuffle_the_die_plane(world, null):
    """A new named substream is a new hash, so it cannot perturb an old one."""
    from fabsim.rng import Substreams

    _response, _obs, _defects, population = null
    rngs = Substreams(SCENARIO["default_seed"])
    before = [rngs.stream("die.kill", 1).random() for _ in range(5)]
    for index in range(50):
        rngs.stream("die.some_future_term", index).random()
    after = [rngs.stream("die.kill", 1).random() for _ in range(5)]
    assert before == after
    again = probed(world)[3]
    assert again.content_sha256() == population.content_sha256()


_PROBE = """
import sys
from fabsim.defects import inspect_response
from fabsim.die import probe_response
from fabsim.observation import observe_response
from fabsim.response import respond_scenario
from fabsim.scenario import from_mapping

config = from_mapping({
    "fabsim": "scenario/v1", "name": "n", "world": "baseline_fab_v1",
    "horizon_days": 30, "lots": 6, "default_seed": 42,
})
response = respond_scenario(config)
print(probe_response(response, observe_response(response),
                     inspect_response(response)).content_sha256())
"""


def test_yield_does_not_depend_on_the_process_it_ran_in(tmp_path):
    digests = []
    for name, hash_seed, extra in (("a", "0", {"LANG": "C", "TZ": "UTC"}),
                                   ("b", "999", {"LANG": "de_DE.UTF-8",
                                                 "TZ": "Asia/Tokyo"})):
        directory = tmp_path / name
        directory.mkdir()
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env.update(extra)
        digests.append(subprocess.run(
            [sys.executable, "-c", _PROBE], cwd=str(directory), env=env,
            capture_output=True, text=True, check=True).stdout.strip())
    assert digests[0] == digests[1]


# ------------------------------------------------------------- anti-leakage


def _module() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "fabsim" / "die.py"


def _identifiers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def _code_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_the_die_engine_cannot_see_a_mechanism_or_the_hidden_plane():
    """The scan the other planes make, plus the ones only 3E could fail.

    Identifiers, not prose: the module's docstring explains what it is *not*
    allowed to read, and saying so is not a path to it.
    """
    forbidden = {
        "realization", "mechanisms", "distractors", "counterfactual",
        "departure", "mechanism", "realized_magnitude",
        "realized_shift_sigma", "severity", "event_index", "events",
        "scenario", "resets", "alarms", "repairs", "truth", "ground_truth",
        "suspect", "suspect_tool", "DEMO_SUSPECT_TOOL", "benchmark",
        "expected_yield", "target_yield_pct", "penalty", "yield_penalty",
        # the hidden defect record, and the latent plane behind it
        "origins", "origin_of", "contributing_chamber_id",
        "contributing_flow_step_id", "trajectory", "trajectories", "latent",
        "latent_dynamics", "value_at",
    }
    assert sorted(_identifiers(_module()) & forbidden) == []


def test_the_die_engine_reads_only_the_grids_origin_and_never_a_defects():
    """Two different things are spelled `origin`, and only one is allowed.

    `die_grid.origin` is a *coordinate* convention — where (0, 0) sits on the
    wafer — and the engine must read it, or the declared vocabulary is a
    convention it merely happens to agree with. `DefectOrigin.origin` is the
    hidden physical cause of a defect and must never be reached. A bare-name
    scan cannot tell them apart, so this one resolves the expression the
    attribute is taken from: every `.origin` access in the module must be on
    the die-grid policy, and nothing else.
    """
    def base(node: ast.expr) -> str:
        while isinstance(node, ast.Attribute):
            node = node.value
        return node.id if isinstance(node, ast.Name) else "<expr>"

    tree = ast.parse(_module().read_text(encoding="utf-8"))
    accesses = [base(node.value) for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr == "origin"]
    assert accesses, "the coordinate origin is declared but never read"
    assert set(accesses) == {"policy"}, sorted(set(accesses))
    # And the hidden record stays out of reach by its own names.
    assert not ({"origins", "origin_of"} & _identifiers(_module()))


def test_the_die_engine_names_no_entity():
    pattern = re.compile(
        r"(ETCH|CVD|LITHO|CMP|PVD|FURN|IMP|MET|INSP|TEST)-\d")
    assert [s for s in _code_strings(_module()) if pattern.search(s)] == []


def test_the_die_engine_reads_no_target_yield():
    """`target_yield_pct` is a product *specification* a fab records, and the
    number the background killer density was calibrated against on the null
    world. Reading it here would make yield a target again rather than a
    count of survivors — which is the audited defect in its politest form."""
    source = _module().read_text(encoding="utf-8")
    tree = ast.parse(source)
    attributes = {n.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Attribute)}
    assert "target_yield_pct" not in attributes
    assert "target_yield_pct" not in " ".join(_code_strings(_module()))


def test_the_die_engine_reads_and_writes_nothing():
    source = _module().read_text(encoding="utf-8")
    for token in ("truth.json", "truth/", "open(", "write_text", "json.dump",
                  "sqlite3", "fab.db"):
        assert token not in source, token
    # A hidden plane may exist only inside the declared dataset root;
    # anywhere else is a slice that wrote one. See tests/fabsim/plane.py
    # for why the assertion is no longer "none exists anywhere".
    assert not hidden_plane_files_outside_the_root()


def test_the_die_engine_imports_nothing_outside_fabsim():
    allowed = {"fabsim", "fabsim.defects", "fabsim.observation", "fabsim.rng",
               "fabsim.timeline", "fabsim.world"}
    tree = ast.parse(_module().read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        imported = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = [node.module]
        for name in imported:
            assert not name.startswith("fabops"), name
            if name.startswith("fabsim"):
                assert name in allowed, name


def test_the_die_engine_has_no_branch_on_an_entity_or_a_scenario():
    """A mechanism-specific yield branch is the mutation §30 asks about.

    Every comparison against a string constant in this module is checked: the
    only ones permitted are against declared vocabulary — a coverage state, a
    partial-die policy, an operation type. A comparison against a tool, a
    chamber, a mechanism or a scenario name has nowhere to hide.
    """
    tree = ast.parse(_module().read_text(encoding="utf-8"))
    permitted = {COVERAGE_INSIDE, COVERAGE_OUTSIDE, COVERAGE_PARTIAL,
                 "exclude", "wafer_center", "row_major", TEST_OPERATION,
                 "accumulation", "ar1"}
    assert permitted >= set(PARTIAL_DIE_POLICIES) | set(DIE_ORIGINS) | set(
        DIE_INDEX_ORDERS)
    compared: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left] + list(node.comparators):
            if (isinstance(operand, ast.Constant)
                    and isinstance(operand.value, str)):
                compared.add(operand.value)
    assert compared <= permitted, sorted(compared - permitted)
