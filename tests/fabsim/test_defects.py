"""
Invariant tests for `fabsim.defects` — the defect/inspection plane.

The audited failure this plane replaces was circular: a defect's *type* chose
its coordinates and the coordinates then "confirmed" the type. So what is
pinned here is the direction of the arrows. Intensity comes from latent state
through declared per-origin sensitivities; geometry comes from the origin;
the reported class comes from a *draw* over the origin and is wrong a
configured fraction of the time; and the observable record carries none of it
— only where, how big, and what the scanner decided to call it.

The mediation claim is stated as a **direction**, because a count model is
stochastic: where the physical magnitude a component reads goes up, that
component's intensity and its realized defects go up too, and where it goes
down they go down. That is checked exactly at the intensity level and
statistically end to end.
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

from fabsim.defects import (
    DEFECT_MODEL,
    Defect,
    DefectOrigin,
    DefectPopulation,
    Inspection,
    inspect,
    inspect_response,
    poisson,
)
from fabsim.response import respond_scenario
from fabsim.rng import stream
from fabsim.scenario import from_mapping

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
    "target": {"tool": "ETCH-03", "chamber": "A"},
    "onset_day": 35,
    "profile": {"type": "ramp", "ramp_days": 7},
    "severity": "obvious",
}

PARTICLE_EVENT: dict[str, Any] = {
    "mechanism": "particle_excursion",
    "target": {"tool": "CVD-01", "chamber": "A"},
    "onset_day": 40,
    "profile": {"type": "step"},
    "severity": "obvious",
}

_CACHE: dict[str, Any] = {}


def scanned(world, **overrides: Any):
    """One scenario, responded to and then inspected. Memoized: not cheap."""
    config = from_mapping({**SCENARIO, **overrides})
    key = config.canonical_json
    if key not in _CACHE:
        response = respond_scenario(config, world=world)
        _CACHE[key] = (response, inspect_response(response))
    return _CACHE[key]


def without_mechanisms(realization):
    """The realization with every mechanism removed. Hidden test instrument."""
    return replace(realization, trajectories=tuple(
        replace(t, values=t.counterfactual) for t in realization.trajectories))


def radius_fraction(defect, world, wafer_size_mm: int = 300) -> float:
    return math.hypot(defect.x_mm, defect.y_mm) / (wafer_size_mm / 2.0)


def target_chamber(world, event):
    return world.chamber_by_name(event["target"]["tool"],
                                 event["target"]["chamber"]).chamber_id


def by_origin(population, name):
    return [d for d in population.defects
            if population.origin_of(d.defect_id).origin == name]


@pytest.fixture(scope="module")
def null(world):
    return scanned(world)


@pytest.fixture(scope="module")
def edge(world):
    return scanned(world, events=[EDGE_EVENT])


# --------------------------------------------------------------- the records


def test_the_records_are_observable_and_nothing_more():
    """`SCHEMA_V2_DESIGN.md` §2.19/§2.20 — and `killer_flag` stays dropped."""
    assert set(Inspection.__dataclass_fields__) == {
        "inspection_id", "wafer_id", "flow_step_id", "inspection_tool_id",
        "inspection_time_min", "total_defect_count", "scan_area_mm2"}
    assert set(Defect.__dataclass_fields__) == {
        "defect_id", "inspection_id", "wafer_id", "x_mm", "y_mm", "size_um",
        "classified_type", "layer"}
    forbidden = {"killer_flag", "origin", "true_origin", "mechanism", "fault",
                 "cause", "ground_truth", "severity", "suspect",
                 "counterfactual", "chamber_id", "is_killer", "zone"}
    for record in (Inspection, Defect):
        assert not (set(record.__dataclass_fields__) & forbidden), record
    # The hidden side is a separate record, reachable only by asking for it.
    assert set(DefectOrigin.__dataclass_fields__) == {
        "defect_id", "origin", "contributing_chamber_id",
        "contributing_flow_step_id"}


def test_ids_are_dense_and_the_model_is_versioned(null):
    _response, population = null
    assert population.model == DEFECT_MODEL == "fabsim.defects/v1"
    assert [i.inspection_id for i in population.inspections] == list(
        range(1, len(population.inspections) + 1))
    assert [d.defect_id for d in population.defects] == list(
        range(1, len(population.defects) + 1))


def test_defect_counts_reconcile_with_their_inspection(null):
    """`SCHEMA_V2_DESIGN.md` §4.3: the count *is* the number of rows."""
    _response, population = null
    assert population.inspections
    for inspection in population.inspections:
        assert inspection.total_defect_count == len(
            population.of_inspection(inspection.inspection_id))
    assert sum(i.total_defect_count for i in population.inspections) == len(
        population.defects)


def test_every_defect_belongs_to_its_inspections_wafer(null):
    _response, population = null
    wafers = {i.inspection_id: i.wafer_id for i in population.inspections}
    for defect in population.defects:
        assert defect.wafer_id == wafers[defect.inspection_id]


def test_the_layer_comes_from_the_inspection_step(null, world):
    response, population = null
    layers = {s.step_id: s.layer for s in world.process_steps
              if s.is_inspection}
    runs = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    for inspection in population.inspections[:200]:
        run = runs[(inspection.wafer_id, inspection.flow_step_id)]
        expected = layers[run.step_id]
        for defect in population.of_inspection(inspection.inspection_id):
            assert defect.layer == expected
            assert defect.layer in world.layers


# ------------------------------------------------------------------ the null


def test_a_null_fab_has_a_full_defect_population(null):
    """A world with nothing wrong is not a clean world.

    If defects only appeared where something was wrong, "this wafer has
    defects" would be the answer.
    """
    response, population = null
    assert response.realization.mechanisms == ()
    counts = [i.total_defect_count for i in population.inspections]
    assert len(counts) > 500
    assert min(counts) > 0                       # no spotless wafers
    assert 8 < st.mean(counts) < 60
    assert st.pstdev(counts) > 0.0


def test_every_declared_origin_occurs_in_the_null(null, world):
    """Rule D2: a component with no support in a healthy world would be a
    value that exists only where something happened."""
    _response, population = null
    seen = Counter(o.origin for o in population.origins)
    assert set(seen) == set(world.observation.classifier.origins)
    total = sum(seen.values())
    for origin, count in seen.items():
        assert count / total > 0.01, origin


def test_every_class_occurs_in_the_null(null, world):
    _response, population = null
    seen = {d.classified_type for d in population.defects}
    assert seen == set(world.observation.classifier.classes)


def test_counts_are_realized_rather_than_formulaic(null):
    """Poisson, so two wafers in the same state differ."""
    _response, population = null
    counts = [i.total_defect_count for i in population.inspections]
    assert len(set(counts)) > 15
    # A Poisson mixture has variance at least its mean.
    assert st.pvariance(counts) > 0.5 * st.mean(counts)


def test_poisson_is_a_poisson():
    """Directly, including above the chunking threshold."""
    for mean in (0.5, 5.0, 25.0, 90.0):
        rng = stream(11, "test.poisson", int(mean * 10))
        draws = [poisson(rng, mean) for _ in range(4000)]
        assert abs(st.mean(draws) - mean) < 0.12 * mean + 0.1
        assert abs(st.pvariance(draws) - mean) < 0.25 * mean + 0.3
        assert min(draws) >= 0
    assert poisson(stream(11, "test.poisson", 0), 0.0) == 0
    assert poisson(stream(11, "test.poisson", 0), -1.0) == 0


# --------------------------------------------------------------- the geometry


def test_no_defect_lands_off_the_wafer(null, world):
    """Geometry comes from the product, never from a hard-coded 300 mm."""
    response, population = null
    radii = {p.product_id: p.wafer_size_mm / 2.0 for p in world.products}
    inspections = {i.inspection_id: i for i in population.inspections}
    wafer_lot = {w.wafer_id: w.lot_id for w in response.timeline.wafers}
    assert len({p.wafer_size_mm for p in world.products}) >= 1
    for defect in population.defects:
        wafer_id = inspections[defect.inspection_id].wafer_id
        product_id = response.timeline.lot(wafer_lot[wafer_id]).product_id
        assert math.hypot(defect.x_mm, defect.y_mm) <= radii[product_id] + 1e-9


def test_background_defects_are_spread_over_the_whole_wafer(null, world):
    """§14: uniform means uniform *per unit area*, not piled at the centre."""
    _response, population = null
    uniform = by_origin(population, "uniform")
    assert len(uniform) > 2000
    fractions = [radius_fraction(d, world) for d in uniform]
    bins = [0] * 5
    for value in fractions:
        bins[min(4, int(value * 5))] += 1
    observed = [count / len(fractions) for count in bins]
    expected = [((i + 1) / 5) ** 2 - (i / 5) ** 2 for i in range(5)]
    for got, want in zip(observed, expected):
        assert abs(got - want) < 0.02


def test_edge_defects_form_a_ring_that_is_not_a_partition(null, world):
    """§4.3: an annulus with jitter, so the signature overlaps the background
    rather than carving the wafer in two."""
    _response, population = null
    policy = world.defects
    ring = [radius_fraction(d, world) for d in by_origin(population,
                                                         "edge_ring")]
    assert len(ring) > 500
    inner = policy.edge_inner_fraction
    outer = inner + policy.edge_width_fraction
    inside = sum(1 for value in ring if inner <= value <= outer)
    assert 0.5 < inside / len(ring) < 0.95      # concentrated, not confined
    assert min(ring) < inner                    # jitter reaches inward
    assert st.mean(ring) > 0.7


def test_centre_defects_cluster_at_the_centre(null, world):
    _response, population = null
    centre = [radius_fraction(d, world) for d in by_origin(population,
                                                           "center")]
    assert len(centre) > 200
    assert st.mean(centre) < 0.3
    uniform = [radius_fraction(d, world) for d in by_origin(population,
                                                            "uniform")]
    assert st.mean(centre) < st.mean(uniform)


def test_particle_defects_arrive_in_clumps(null, world):
    """A cluster is a cluster: within one wafer, particle defects sit closer
    together than background defects do."""
    _response, population = null

    def mean_spread(origin):
        spreads = []
        for inspection in population.inspections[:400]:
            points = [(d.x_mm, d.y_mm)
                      for d in population.of_inspection(
                          inspection.inspection_id)
                      if population.origin_of(d.defect_id).origin == origin]
            if len(points) < 3:
                continue
            cx = st.mean(p[0] for p in points)
            cy = st.mean(p[1] for p in points)
            spreads.append(st.mean(math.hypot(x - cx, y - cy)
                                   for x, y in points))
        return st.mean(spreads), len(spreads)

    clustered, n_clustered = mean_spread("particle_cluster")
    scattered, n_scattered = mean_spread("uniform")
    assert n_clustered > 20 and n_scattered > 20
    assert clustered < scattered


def test_scratches_are_strokes_rather_than_clouds(null):
    """Defects of one scratch lie near a line, so their spread is anisotropic."""
    _response, population = null
    ratios = []
    for inspection in population.inspections:
        points = [(d.x_mm, d.y_mm)
                  for d in population.of_inspection(inspection.inspection_id)
                  if population.origin_of(d.defect_id).origin == "scratch"]
        if len(points) < 4:
            continue
        cx = st.mean(p[0] for p in points)
        cy = st.mean(p[1] for p in points)
        xx = st.mean((p[0] - cx) ** 2 for p in points)
        yy = st.mean((p[1] - cy) ** 2 for p in points)
        xy = st.mean((p[0] - cx) * (p[1] - cy) for p in points)
        trace, det = xx + yy, xx * yy - xy * xy
        root = math.sqrt(max(0.0, trace * trace / 4.0 - det))
        major, minor = trace / 2.0 + root, trace / 2.0 - root
        if major > 0:
            ratios.append(minor / major)
    assert len(ratios) > 5
    assert st.median(ratios) < 0.2               # long and thin


# ------------------------------------------------------------ the classifier


def test_the_classifier_is_wrong_some_of_the_time(null, world):
    """`ANTI_LEAKAGE_DESIGN.md` L5, and §4.5 of the mechanism model.

    A classifier that simply reported the origin would restore exactly the
    circularity the audit found: type ⇒ geometry ⇒ "confirmed".
    """
    _response, population = null
    rows = dict(world.observation.classifier.confusion)
    checked = 0
    for origin in world.observation.classifier.origins:
        defects = by_origin(population, origin)
        if len(defects) < 200:
            continue
        best = max(dict(rows[origin]).items(), key=lambda kv: kv[1])
        agreement = sum(1 for d in defects
                        if d.classified_type == best[0]) / len(defects)
        assert agreement < 1.0
        assert abs(agreement - best[1]) < 0.06, origin
        checked += 1
    assert checked >= 3


def test_no_class_outside_its_origins_confusion_row(null, world):
    _response, population = null
    rows = {origin: {label for label, p in row if p > 0.0}
            for origin, row in world.observation.classifier.confusion}
    for defect in population.defects:
        origin = population.origin_of(defect.defect_id).origin
        assert defect.classified_type in rows[origin]


def test_a_class_does_not_reveal_the_origin(null, world):
    """Every class arises from more than one origin, so the label is
    evidence rather than an answer."""
    _response, population = null
    origins_of_class = defaultdict(set)
    for defect in population.defects:
        origins_of_class[defect.classified_type].add(
            population.origin_of(defect.defect_id).origin)
    assert len(origins_of_class) >= 4
    for label, origins in origins_of_class.items():
        assert len(origins) > 1, label


def test_a_scanner_reports_only_what_it_can_see(null, world):
    """The step's own sensitivity threshold thins the population."""
    response, population = null
    runs = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    thresholds = {}
    for inspection in population.inspections:
        run = runs[(inspection.wafer_id, inspection.flow_step_id)]
        step = world.step(run.step_id)
        thresholds[inspection.inspection_id] = dict(
            step.settings)["sensitivity_threshold_um"]
    assert len(set(thresholds.values())) >= 2
    for defect in population.defects:
        assert defect.size_um >= thresholds[defect.inspection_id]
    sizes = [d.size_um for d in population.defects]
    assert st.mean(sizes) > 0.0
    assert max(sizes) > 3 * st.median(sizes)      # lognormal has a long tail


# -------------------------------------------------------- latent mediation


def test_intensity_rises_with_the_physical_magnitude_it_reads(world):
    """The mediation claim, at the source and exactly.

    A component's intensity is its base rate plus its declared sensitivities
    times the magnitude of the latents on the covered runs — so doubling that
    magnitude raises it, and nothing else can.
    """
    from fabsim.defects import _intensities, _propensity

    response, _population = scanned(world)
    realization = response.realization
    covered = [r for r in response.timeline.runs
               if world.step(r.step_id).step_name == "GATE_ETCH"][:1]
    assert covered

    rows = _intensities(world, realization, covered, 1.0)
    edge = sum(rate for origin, rate, _c, _f in rows if origin == "edge_ring")
    base = world.defects.origin("edge_ring").base_rate
    assert edge >= base

    doubled = replace(realization, trajectories=tuple(
        replace(t, values=tuple(v * 2.0 for v in t.values))
        for t in realization.trajectories))
    louder = sum(rate for origin, rate, _c, _f
                 in _intensities(world, doubled, covered, 1.0)
                 if origin == "edge_ring")
    assert louder > edge

    # A signed latent contributes its magnitude: either direction is
    # non-uniformity, and an intensity may never go negative.
    assert _propensity(world, "edge_uniformity", -0.02) > 0.0
    assert _propensity(world, "edge_uniformity", -0.02) == pytest.approx(
        _propensity(world, "edge_uniformity", 0.02))
    assert _propensity(world, "particle_load", -0.01) == 0.0


def test_product_scale_moves_the_baseline(world):
    from fabsim.defects import _intensities

    response, _population = scanned(world)
    covered = [r for r in response.timeline.runs
               if world.step(r.step_id).step_name == "GATE_ETCH"][:1]
    low = sum(rate for _o, rate, _c, _f
              in _intensities(world, response.realization, covered, 0.5))
    high = sum(rate for _o, rate, _c, _f
               in _intensities(world, response.realization, covered, 1.5))
    assert high > low
    assert len({p.defect_scale for p in world.products}) > 1


def test_defects_follow_the_physical_magnitude_in_both_directions(world):
    """End-to-end mediation, stated as a direction because counts are drawn.

    Where the magnitude a component reads goes up, its defects go up; where it
    goes down, they go down. Checked against the mechanism-free counterfactual
    on the *same* schedule, so nothing but the physical state differs.
    """
    response, population = scanned(world, events=[EDGE_EVENT])
    chamber = target_chamber(world, EDGE_EVENT)
    shadow = inspect(response.timeline, without_mechanisms(
        response.realization))
    grid = response.realization.grid
    trajectory = response.realization.trajectory(chamber, "edge_uniformity")
    window = slice(grid.index_at(45 * 24 * 60), grid.points)
    change = (st.mean(abs(v) for v in trajectory.values[window])
              - st.mean(abs(v) for v in trajectory.counterfactual[window]))
    assert change != 0.0

    step_id = world.step_by_name("GATE_ETCH").step_id

    def exposed_edge_defects(pop):
        total = edge = 0
        for inspection in pop.inspections:
            if inspection.inspection_time_min < 45 * 24 * 60:
                continue
            runs = [r for r in response.timeline.runs_of_wafer(
                inspection.wafer_id) if r.step_id == step_id]
            if not runs or runs[0].chamber_id != chamber:
                continue
            for defect in pop.of_inspection(inspection.inspection_id):
                total += 1
                edge += radius_fraction(defect, world) > 0.8
        return edge, total

    edge_on, total_on = exposed_edge_defects(population)
    edge_off, total_off = exposed_edge_defects(shadow)
    assert total_on > 200 and total_off > 200
    assert (change > 0) == (total_on > total_off)
    assert (change > 0) == (edge_on / total_on > edge_off / total_off)


def test_a_mechanism_reaches_only_the_wafers_its_chamber_touched(world):
    """A wafer that never saw the affected chamber is bit-identical."""
    response, population = scanned(world, events=[PARTICLE_EVENT])
    chamber = target_chamber(world, PARTICLE_EVENT)
    shadow = inspect(response.timeline, without_mechanisms(
        response.realization))
    covered_steps = {s.step_id for s in world.process_steps
                     if s.is_inspection
                     for s in world.covered_steps(s.step_id)}

    untouched = 0
    by_id = {i.inspection_id: i for i in shadow.inspections}
    for inspection in population.inspections:
        runs = response.timeline.runs_of_wafer(inspection.wafer_id)
        if any(r.chamber_id == chamber and r.step_id in covered_steps
               for r in runs):
            continue
        assert inspection.total_defect_count == by_id[
            inspection.inspection_id].total_defect_count
        untouched += 1
    assert untouched > 200


def test_healthy_and_affected_wafers_overlap(world, edge):
    """No perfect separation: a fault shifts a distribution, it does not
    partition one (leakage class T5)."""
    response, population = edge
    chamber = target_chamber(world, EDGE_EVENT)
    step_id = world.step_by_name("GATE_ETCH").step_id
    exposed, healthy = [], []
    for inspection in population.inspections:
        if inspection.inspection_time_min < 45 * 24 * 60:
            continue
        runs = [r for r in response.timeline.runs_of_wafer(inspection.wafer_id)
                if r.step_id == step_id]
        if not runs:
            continue
        defects = population.of_inspection(inspection.inspection_id)
        if not defects:
            continue
        share = sum(1 for d in defects
                    if radius_fraction(d, world) > 0.8) / len(defects)
        (exposed if runs[0].chamber_id == chamber else healthy).append(share)
    assert len(exposed) > 20 and len(healthy) > 100
    assert min(exposed) < st.median(healthy) < max(exposed)
    assert min(healthy) < st.median(exposed) < max(healthy)


def test_products_do_not_reveal_the_affected_chamber(world, edge):
    response, population = edge
    chamber = target_chamber(world, EDGE_EVENT)
    step_id = world.step_by_name("GATE_ETCH").step_id
    exposure = defaultdict(lambda: [0, 0])
    for inspection in population.inspections:
        runs = [r for r in response.timeline.runs_of_wafer(inspection.wafer_id)
                if r.step_id == step_id]
        if not runs:
            continue
        product_id = response.timeline.lot(runs[0].lot_id).product_id
        exposure[product_id][runs[0].chamber_id == chamber] += 1
    assert len(exposure) >= 5
    saw = [p for p, (_no, yes) in exposure.items() if yes > 0]
    assert len(saw) >= 3
    for product_id in saw:
        assert exposure[product_id][0] > 0


# --------------------------------------------------------------- the clock


def test_an_inspection_follows_everything_it_covers(null, world):
    """No defect is reported before the process that could have made it."""
    response, population = null
    runs = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    checked = 0
    for inspection in population.inspections:
        run = runs[(inspection.wafer_id, inspection.flow_step_id)]
        assert inspection.inspection_time_min == run.end_min
        for covered in world.covered_steps(run.step_id):
            earlier = [r for r in response.timeline.runs_of_wafer(
                inspection.wafer_id) if r.step_id == covered.step_id]
            assert earlier
            assert earlier[0].end_min <= run.start_min
            checked += 1
    assert checked > 1000


def test_only_inspection_steps_produce_inspections(null, world):
    response, population = null
    runs = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    for inspection in population.inspections:
        step = world.step(runs[(inspection.wafer_id,
                                inspection.flow_step_id)].step_id)
        assert step.is_inspection
        assert step.covers_step_ids


def test_the_inspection_tool_is_not_the_process_tool(null, world):
    """§18: a scanner sees the wafer; it did not make it."""
    response, population = null
    runs = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    for inspection in population.inspections[:300]:
        run = runs[(inspection.wafer_id, inspection.flow_step_id)]
        tool = world.tool(inspection.inspection_tool_id)
        assert inspection.inspection_tool_id == run.tool_id
        assert "INSPECTION" in tool.operations
        for covered in world.covered_steps(run.step_id):
            earlier = [r for r in response.timeline.runs_of_wafer(
                inspection.wafer_id) if r.step_id == covered.step_id][0]
            assert earlier.tool_id != inspection.inspection_tool_id


def test_the_hidden_origin_names_a_chamber_that_processed_the_wafer(null,
                                                                    world):
    """Attribution, for the later truth emitter: a latent-driven defect points
    at a run the wafer actually had, never at the scanner."""
    response, population = null
    checked = 0
    for defect in population.defects[:3000]:
        origin = population.origin_of(defect.defect_id)
        if origin.contributing_chamber_id < 0:
            continue                              # background propensity
        runs = response.timeline.runs_of_wafer(defect.wafer_id)
        assert any(r.chamber_id == origin.contributing_chamber_id
                   and r.flow_step_id == origin.contributing_flow_step_id
                   for r in runs)
        checked += 1
    assert checked > 100


def test_a_wafer_without_full_coverage_is_not_inspected(world):
    """The horizon can cut a wafer off mid-route; a fab cannot report on a
    step that never ran."""
    response, population = scanned(world, horizon_days=20, lots=6)
    inspected = {i.wafer_id for i in population.inspections}
    incomplete = 0
    for wafer in response.timeline.wafers:
        steps = {r.step_id for r in response.timeline.runs_of_wafer(
            wafer.wafer_id)}
        for step in world.process_steps:
            if not step.is_inspection or step.step_id not in steps:
                continue
            if not set(step.covers_step_ids) <= steps:
                assert wafer.wafer_id not in inspected
                incomplete += 1
    assert population.inspections or incomplete


# --------------------------------------------------------------- determinism


def test_the_same_inputs_produce_the_same_defects(world):
    config = from_mapping({**SCENARIO, "events": [EDGE_EVENT]})
    first = respond_scenario(config, world=world)
    second = respond_scenario(config, world=world)
    assert (inspect_response(first).content_sha256()
            == inspect_response(second).content_sha256())


def test_inspecting_twice_gives_the_same_population(null):
    response, population = null
    again = inspect(response.timeline, response.realization)
    assert again.content_sha256() == population.content_sha256()
    assert [d.x_mm for d in again.defects] == [d.x_mm
                                               for d in population.defects]


def test_a_different_seed_changes_values_but_not_structure(world, null):
    _response, population = null
    _other_response, other = scanned(world, default_seed=43)
    assert other.content_sha256() != population.content_sha256()
    assert other.model == population.model
    assert other.inspections and other.defects
    assert ({d.classified_type for d in other.defects}
            == {d.classified_type for d in population.defects})


def test_drawing_an_unrelated_substream_changes_nothing(null):
    response, population = null
    for index in range(200):
        stream(response.timeline.seed, "some.future.subsystem",
               index).random()
    assert (inspect(response.timeline, response.realization).content_sha256()
            == population.content_sha256())


def test_prose_does_not_reach_a_defect(world, null):
    _response, population = null
    _renamed, other = scanned(world, name="something else entirely",
                              description="rewritten prose")
    assert other.content_sha256() == population.content_sha256()


_PROBE = """
from fabsim.defects import inspect_response
from fabsim.response import respond_scenario
from fabsim.scenario import from_mapping
config = from_mapping({
    "fabsim": "scenario/v1", "name": "null", "world": "baseline_fab_v1",
    "horizon_days": 30, "lots": 6, "default_seed": 42,
})
print(inspect_response(respond_scenario(config)).content_sha256())
"""


def test_defects_do_not_depend_on_the_process_they_ran_in(tmp_path):
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
    return (Path(__file__).resolve().parents[2] / "src" / "fabsim"
            / "defects.py")


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


def test_the_defect_engine_cannot_see_a_mechanism():
    forbidden = {"mechanisms", "distractors", "counterfactual", "departure",
                 "mechanism", "realized_magnitude", "realized_shift_sigma",
                 "severity", "event_index", "events", "scenario", "config",
                 "resets", "alarms", "repairs", "truth", "ground_truth",
                 "suspect"}
    assert not (_identifiers(_module()) & forbidden)


def test_the_defect_engine_names_no_entity():
    pattern = re.compile(
        r"(ETCH|CVD|LITHO|CMP|PVD|FURN|IMP|MET|INSP|TEST)-\d")
    assert [s for s in _code_strings(_module()) if pattern.search(s)] == []


def test_the_defect_engine_reaches_no_die_or_yield():
    """§26: the chain stops at defects. 3E owns everything after.

    Identifiers and code strings, not prose: the module's docstring says what
    the plane is *for*, and explaining that 3E comes next is not a code path
    to it — the same exclusion every other anti-leakage scan here makes.
    """
    forbidden = {"die_bins", "die_x", "die_y", "die_grid", "wafer_yield",
                 "good_die", "total_die", "bin_code", "yield_pct",
                 "kill_probability", "killer_flag", "is_killer", "die",
                 "kill"}
    assert not (_identifiers(_module()) & forbidden)
    lowered = " ".join(_code_strings(_module())).lower()
    for token in ("yield", "die_", "kill", "bin"):
        assert token not in lowered, token


def test_the_defect_engine_reads_and_writes_nothing():
    source = _module().read_text(encoding="utf-8")
    for token in ("truth.json", "truth/", "open(", "write_text", "json.dump",
                  "sqlite3"):
        assert token not in source, token
    repository = Path(__file__).resolve().parents[2]
    assert not list(repository.glob("**/truth.json"))
    assert not (repository / "data" / "scenarios").exists()


def test_the_hidden_origin_never_crosses_into_the_observable_record(null):
    """The two planes are separate collections, and the observable one has
    nowhere to put an origin even if an emitter wanted to."""
    _response, population = null
    assert set(DefectPopulation.__dataclass_fields__) >= {
        "inspections", "defects", "origins"}
    for defect in population.defects[:200]:
        assert not hasattr(defect, "origin")
    assert {o.defect_id for o in population.origins} == {
        d.defect_id for d in population.defects}
    # The classified type disagrees with the origin often enough that the
    # observable plane cannot be read back into the hidden one.
    disagree = sum(1 for d in population.defects
                   if d.classified_type.lower()
                   != population.origin_of(d.defect_id).origin.lower())
    assert disagree > len(population.defects) * 0.4


def test_the_engine_is_generic_over_chambers(null, world):
    """Every chamber that processes a covered step contributes, by the same
    code path; none is special-cased."""
    _response, population = null
    contributing = {o.contributing_chamber_id for o in population.origins
                    if o.contributing_chamber_id > 0}
    covered_steps = set()
    for step in world.process_steps:
        if step.is_inspection:
            covered_steps |= set(step.covers_step_ids)
    eligible = {c.chamber_id for c in world.chambers
                for step in world.process_steps
                if step.step_id in covered_steps
                and step.operation_type in world.tool(c.tool_id).operations}
    assert len(contributing) >= 8
    assert contributing <= eligible
