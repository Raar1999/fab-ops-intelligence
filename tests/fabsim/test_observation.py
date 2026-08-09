"""
Invariant tests for `fabsim.observation` — the process observation plane.

The central claim under test is **mediation**: every difference a mechanism
makes to a measurement passes through latent state. It is checked the only way
that is airtight — by measuring the same timeline twice, once against the
realized latent trajectories and once against their mechanism-free twins, and
showing the difference is exactly proportional to the latent departure and
exactly zero everywhere the latent did not move. The counterfactual is a
hidden test instrument; it never appears in an observable record and the
engine never reads it.

Everything else follows from that: a null world that varies, chambers that
overlap heavily, products that keep their own specifications, measurements
that never precede the runs they describe, and no field anywhere that says
why a number is what it is.
"""
from __future__ import annotations

import ast
import math
import os
import re
import statistics as st
import subprocess
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fabsim.observation import (
    OBSERVATION_MODEL,
    Metrology,
    ProcessObservations,
    RunMeasurement,
    observe,
    observe_response,
    zone_radius,
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

DRIFT_EVENT: dict[str, Any] = {
    "mechanism": "param_drift",
    "target": {"tool": "ETCH-01", "chamber": "A"},
    "onset_day": 30,
    "profile": {"type": "ramp", "ramp_days": 14},
    "severity": "moderate",
}

EDGE_EVENT: dict[str, Any] = {
    "mechanism": "chamber_edge_uniformity",
    "target": {"tool": "ETCH-02", "chamber": "B"},
    "onset_day": 35,
    "profile": {"type": "ramp", "ramp_days": 7},
    "severity": "moderate",
}

_CACHE: dict[str, Any] = {}


def measured(world, **overrides: Any):
    """One scenario, responded to and then measured. Memoized: not cheap."""
    config = from_mapping({**SCENARIO, **overrides})
    key = config.canonical_json
    if key not in _CACHE:
        response = respond_scenario(config, world=world)
        _CACHE[key] = (response, observe_response(response))
    return _CACHE[key]


def without_mechanisms(realization):
    """The realization with every mechanism removed, on identical draws.

    A **hidden test instrument**. It exists so a test can subtract the world
    that would have been from the world that was; the observation engine has
    no access to it and no observable record carries it.
    """
    return replace(realization, trajectories=tuple(
        replace(t, values=t.counterfactual) for t in realization.trajectories))


def contribution(response, observations):
    """Per-measurement mechanism contribution: observed minus counterfactual."""
    null = observe(response.timeline, without_mechanisms(response.realization))
    by_id = {m.run_meas_id: m for m in null.run_measurements}
    return {m.run_meas_id: m.value - by_id[m.run_meas_id].value
            for m in observations.run_measurements}


def metrology_contribution(response, observations):
    null = observe(response.timeline, without_mechanisms(response.realization))
    by_id = {m.metrology_id: m for m in null.metrology}
    return {m.metrology_id: m.value - by_id[m.metrology_id].value
            for m in observations.metrology}


def runs_by_id(response):
    return {run.run_id: run for run in response.timeline.runs}


def target_chamber(world, event):
    return world.chamber_by_name(event["target"]["tool"],
                                 event["target"]["chamber"]).chamber_id


@pytest.fixture(scope="module")
def null(world):
    return measured(world)


@pytest.fixture(scope="module")
def drift(world):
    return measured(world, events=[DRIFT_EVENT])


@pytest.fixture(scope="module")
def edge(world):
    return measured(world, events=[EDGE_EVENT])


# ------------------------------------------------------------- the records


def test_the_records_are_observable_and_nothing_more():
    """`SCHEMA_V2_DESIGN.md` §2.14 and §2.15, with no field for a cause.

    A field that exists is a field a later emitter can fill from the wrong
    plane. There is none.
    """
    assert set(RunMeasurement.__dataclass_fields__) == {
        "run_meas_id", "run_id", "param_name", "value", "unit", "set_value"}
    assert set(Metrology.__dataclass_fields__) == {
        "metrology_id", "wafer_id", "flow_step_id", "metrology_tool_id",
        "meas_time_min", "param_name", "value", "unit"}
    forbidden = {"mechanism", "fault", "fault_id", "suspect", "cause",
                 "ground_truth", "severity", "severity_label", "is_faulted",
                 "latent", "counterfactual", "departure", "event_index",
                 "chamber_offset", "truth"}
    for record in (RunMeasurement, Metrology, ProcessObservations):
        assert not (set(record.__dataclass_fields__) & forbidden), record


def test_ids_are_dense_and_the_model_is_versioned(null):
    _response, observations = null
    assert observations.model == OBSERVATION_MODEL == "fabsim.observation/v1"
    assert [m.run_meas_id for m in observations.run_measurements] == list(
        range(1, len(observations.run_measurements) + 1))
    assert [m.metrology_id for m in observations.metrology] == list(
        range(1, len(observations.metrology) + 1))


def test_every_measurement_belongs_to_a_run_that_happened(null):
    response, observations = null
    runs = runs_by_id(response)
    assert observations.run_measurements
    for measurement in observations.run_measurements:
        assert measurement.run_id in runs


def test_every_channel_keeps_its_declared_unit(null, world):
    _response, observations = null
    units = {c.name: c.unit for c in world.observation.channels}
    for measurement in observations.run_measurements:
        assert measurement.unit == units[measurement.param_name]
    for row in observations.metrology:
        metric = row.param_name.rsplit("_", 1)[0]
        assert row.unit == units[metric]


# ------------------------------------------------------------------- FDC


def test_fdc_channels_come_from_the_recipe_the_run_used(null, world):
    """Grounded, not invented: a channel exists for a run because the recipe
    gave it a setpoint (`SCHEMA_V2_DESIGN.md` §2.14)."""
    response, observations = null
    runs = runs_by_id(response)
    checked = 0
    for measurement in observations.run_measurements:
        run = runs[measurement.run_id]
        product_id = response.timeline.lot(run.lot_id).product_id
        recipe = world.recipe_for(run.step_id, product_id)
        settings = dict(recipe.settings)
        assert measurement.param_name in settings
        assert measurement.set_value == settings[measurement.param_name]
        channel = world.channel(measurement.param_name)
        assert channel.kind == "fdc"
        assert world.step(run.step_id).operation_type in channel.operation_types
        checked += 1
    assert checked > 5000


def test_every_applicable_channel_is_reported_for_every_run(null, world):
    response, observations = null
    runs = runs_by_id(response)
    for run in response.timeline.runs[:200]:
        product_id = response.timeline.lot(run.lot_id).product_id
        settings = dict(world.recipe_for(run.step_id, product_id).settings)
        expected = {c.name for c
                    in world.channels_for_operation(
                        world.step(run.step_id).operation_type)
                    if c.kind == "fdc" and c.name in settings}
        assert {m.param_name
                for m in observations.of_run(run.run_id)} == expected


def test_a_healthy_reading_sits_near_its_setpoint(null, world):
    """`nominal setpoint ± normal variation ± measurement noise`."""
    _response, observations = null
    for name in ("gas_flow_sccm", "chamber_pressure_mtorr", "rf_power_w"):
        channel = world.channel(name)
        deltas = [m.value - m.set_value for m in observations.channel(name)]
        assert abs(st.mean(deltas)) < 2.0 * channel.scale
        # The spread is the variation stack, in the channel's own units.
        assert 0.5 * channel.scale < st.pstdev(deltas) < 5.0 * channel.scale


def test_fdc_values_stay_physically_possible(null):
    """No clamping is applied, so this proves the model does not need any."""
    _response, observations = null
    assert all(m.value > 0.0 for m in observations.run_measurements)


# ------------------------------------------------------------- metrology


def test_metrology_reports_every_zone_and_the_spread(null, world):
    _response, observations = null
    zones = world.observation.wafer_zones
    by_wafer_step = defaultdict(set)
    for row in observations.metrology:
        by_wafer_step[(row.wafer_id, row.flow_step_id)].add(row.param_name)
    assert by_wafer_step
    for names in by_wafer_step.values():
        assert names == {f"cd_nm_{zone}" for zone in zones} | {"cd_nm_sigma"}


def test_metrology_is_referenced_to_the_products_own_target(null, world):
    """The specification comes from the recipe and never moves."""
    response, observations = null
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    grouped = defaultdict(list)
    for row in observations.metrology:
        if row.param_name != "cd_nm_center":
            continue
        run = byrun[(row.wafer_id, row.flow_step_id)]
        product_id = response.timeline.lot(run.lot_id).product_id
        grouped[(run.step_id, product_id)].append(row.value)

    assert len(grouped) >= 6
    for (step_id, product_id), values in grouped.items():
        recipe = world.recipe_for(step_id, product_id)
        assert recipe.metric_lsl < recipe.metric_target < recipe.metric_usl
        # Realized readings scatter about the target, not on top of it.
        assert abs(st.mean(values) - recipe.metric_target) < 1.5
        assert st.pstdev(values) > 0.0


def test_metrology_measures_the_step_the_world_declares(null, world):
    """`flow_step_id` is the *measured* step, resolved through `measures`."""
    response, observations = null
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    measurable = {s.step_id for s in world.process_steps
                  if s.measures_step_id is not None}
    measured_ids = {world.step(s.measures_step_id).step_id
                    for s in world.process_steps
                    if s.measures_step_id is not None}
    assert measurable and measured_ids

    for row in observations.metrology[:400]:
        run = byrun[(row.wafer_id, row.flow_step_id)]
        assert run.step_id in measured_ids          # never the metrology step
        tool = world.tool(row.metrology_tool_id)
        assert "METROLOGY" in tool.operations       # read out on a CD tool
        assert row.metrology_tool_id != run.tool_id


def test_a_metrology_value_reflects_the_chamber_that_processed_the_wafer(
        world, drift):
    """The etch chamber's state, not the metrology tool's.

    This is what makes the `measures` relation load-bearing: a CD number
    indicts the chamber that made the feature.
    """
    response, observations = drift
    chamber = target_chamber(world, DRIFT_EVENT)
    deltas = metrology_contribution(response, observations)
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}

    moved, still = [], []
    for row in observations.metrology:
        if row.param_name != "cd_nm_center":
            continue
        run = byrun[(row.wafer_id, row.flow_step_id)]
        (moved if run.chamber_id == chamber else still).append(
            deltas[row.metrology_id])
    assert moved and still
    assert max(abs(d) for d in moved) > 0.0
    # Wafers etched elsewhere are untouched, whatever tool read them out.
    assert max(abs(d) for d in still) == 0.0


# -------------------------------------------------------- radial structure


def test_zone_radius_runs_from_the_centre_to_the_edge():
    assert zone_radius(0, 3) == 0.0
    assert zone_radius(1, 3) == 0.5
    assert zone_radius(2, 3) == 1.0
    assert zone_radius(0, 1) == 0.0


def test_a_radial_latent_moves_the_edge_and_leaves_the_centre_alone(world,
                                                                    edge):
    """`CAUSAL_MECHANISM_MODEL.md` §2, and the signature scenario B rests on."""
    response, observations = edge
    assert world.latent("edge_uniformity").radial_weight == 1.0
    chamber = target_chamber(world, EDGE_EVENT)
    deltas = metrology_contribution(response, observations)
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}

    by_zone = defaultdict(list)
    for row in observations.metrology:
        run = byrun[(row.wafer_id, row.flow_step_id)]
        if run.chamber_id != chamber or deltas[row.metrology_id] == 0.0:
            if run.chamber_id != chamber:
                continue
        by_zone[row.param_name].append(deltas[row.metrology_id])

    assert by_zone["cd_nm_center"]
    assert max(abs(d) for d in by_zone["cd_nm_center"]) == 0.0
    affected = [d for d in by_zone["cd_nm_edge"] if d != 0.0]
    assert affected
    assert st.mean(affected) > 0.0
    # Mid sits halfway out, so it takes half the shift.
    mid = [d for d in by_zone["cd_nm_mid"] if d != 0.0]
    assert st.mean(mid) == pytest.approx(0.5 * st.mean(affected), rel=1e-6)


def test_a_uniform_latent_moves_every_zone_alike(world, drift):
    response, observations = drift
    assert world.latent("param_bias").radial_weight == 0.0
    chamber = target_chamber(world, DRIFT_EVENT)
    deltas = metrology_contribution(response, observations)
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}

    per_wafer = defaultdict(dict)
    for row in observations.metrology:
        run = byrun[(row.wafer_id, row.flow_step_id)]
        if run.chamber_id == chamber:
            per_wafer[(row.wafer_id, row.flow_step_id)][row.param_name] = \
                deltas[row.metrology_id]
    moved = [z for z in per_wafer.values() if z.get("cd_nm_center", 0.0) != 0.0]
    assert moved
    for zones in moved:
        assert zones["cd_nm_edge"] == pytest.approx(zones["cd_nm_center"],
                                                    rel=1e-9)
        # A uniform shift moves no wafer's spread.
        assert abs(zones["cd_nm_sigma"]) < 1e-9


def test_the_within_wafer_spread_tracks_the_radial_state(null, world):
    """A chamber whose radial latent sits further from zero reads a wider
    wafer — which is why the spread is evidence rather than noise."""
    response, observations = null
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    spreads = defaultdict(list)
    for row in observations.metrology:
        if row.param_name != "cd_nm_sigma":
            continue
        run = byrun[(row.wafer_id, row.flow_step_id)]
        spreads[run.chamber_id].append(row.value)

    pairs = [(abs(response.realization.offset(cid, "edge_uniformity").total),
              st.mean(values))
             for cid, values in spreads.items() if len(values) > 20]
    assert len(pairs) >= 5
    assert all(spread > 0.0 for _offset, spread in pairs)
    widest = max(pairs, key=lambda p: p[0])
    narrowest = min(pairs, key=lambda p: p[0])
    assert widest[1] > narrowest[1]


# ---------------------------------------------------- latent-mediated only


def test_a_mechanism_reaches_a_measurement_only_through_its_latent(world,
                                                                   drift):
    """L3 in miniature, and exactly: the contribution to every measurement is
    the latent departure times the declared sensitivity times the scale."""
    response, observations = drift
    chamber = target_chamber(world, DRIFT_EVENT)
    deltas = contribution(response, observations)
    runs = runs_by_id(response)
    grid = response.realization.grid
    trajectory = response.realization.trajectory(chamber, "param_bias")
    departure = trajectory.departure()
    reference = world.latent("param_bias").severity_reference

    checked = 0
    for measurement in observations.run_measurements:
        run = runs[measurement.run_id]
        if run.chamber_id != chamber:
            continue
        channel = world.channel(measurement.param_name)
        sensitivity = dict(channel.sensitivities).get("param_bias", 0.0)
        first = grid.index_at(run.start_min)
        last = grid.index_at(max(run.start_min, run.end_min - 1))
        window = departure[first:last + 1]
        expected = (sensitivity * (sum(window) / len(window)) / reference
                    * channel.scale)
        assert deltas[measurement.run_meas_id] == pytest.approx(expected,
                                                                abs=1e-9)
        checked += 1
    assert checked > 200


def test_a_mechanism_reaches_no_other_chamber(world, drift):
    response, observations = drift
    chamber = target_chamber(world, DRIFT_EVENT)
    deltas = contribution(response, observations)
    runs = runs_by_id(response)
    elsewhere = [deltas[m.run_meas_id] for m in observations.run_measurements
                 if runs[m.run_id].chamber_id != chamber]
    assert len(elsewhere) > 5000
    assert max(abs(d) for d in elsewhere) == 0.0


def test_a_mechanism_reaches_nothing_before_its_onset(world, drift):
    response, observations = drift
    chamber = target_chamber(world, DRIFT_EVENT)
    deltas = contribution(response, observations)
    runs = runs_by_id(response)
    onset = DRIFT_EVENT["onset_day"] * 24 * 60
    before = [deltas[m.run_meas_id] for m in observations.run_measurements
              if runs[m.run_id].chamber_id == chamber
              and runs[m.run_id].end_min <= onset]
    assert before
    assert max(abs(d) for d in before) == 0.0


def test_the_observable_effect_scales_with_the_realized_latent(world):
    """Severity flows through the *realized* state, never the configured one.

    Measured **per run**: the observable contribution divided by the latent
    departure over that run's own window, in severity-σ, is the channel's
    declared `sensitivity × scale` — the same number at every severity, on
    every run, because the transfer function is the only thing between the two
    planes (ADR-018).

    Deliberately not a ratio of the *mean* contribution to the *peak* weekly
    shift. That statistic mixes the transfer function with the shape of the
    trajectory in time, and the shape legitimately depends on severity once
    the fab responds: a bigger departure is repaired sooner and harder, so its
    average sits further below its peak. Per run there is nothing left but the
    transfer function, which is what the claim is about.
    """
    channel = world.channel("gas_flow_sccm")
    expected = dict(channel.sensitivities)["param_bias"] * channel.scale
    chamber = target_chamber(world, DRIFT_EVENT)

    for severity in ("subtle", "moderate", "obvious"):
        response, observations = measured(
            world, events=[dict(DRIFT_EVENT, severity=severity)])
        deltas = contribution(response, observations)
        runs = runs_by_id(response)
        grid = response.realization.grid
        reference = world.latent("param_bias").severity_reference
        departure = response.realization.trajectory(chamber,
                                                    "param_bias").departure()
        ratios = []
        for measurement in observations.run_measurements:
            if measurement.param_name != "gas_flow_sccm":
                continue
            run = runs[measurement.run_id]
            if run.chamber_id != chamber:
                continue
            window = departure[grid.index_at(run.start_min):
                               grid.index_at(run.end_min) + 1]
            shift = sum(window) / len(window) / reference
            if shift == 0.0:
                assert deltas[measurement.run_meas_id] == 0.0
                continue
            ratios.append(deltas[measurement.run_meas_id] / shift)

        assert len(ratios) > 20, severity
        assert all(ratio == pytest.approx(expected, rel=1e-9)
                   for ratio in ratios), severity


def test_several_channels_respond_and_none_of_them_alone_is_the_answer(
        world, drift):
    """Converging, imperfect evidence: the declared sensitivities decide."""
    response, observations = drift
    chamber = target_chamber(world, DRIFT_EVENT)
    deltas = contribution(response, observations)
    runs = runs_by_id(response)

    per_channel = defaultdict(list)
    for measurement in observations.run_measurements:
        if runs[measurement.run_id].chamber_id != chamber:
            continue
        if deltas[measurement.run_meas_id] != 0.0:
            per_channel[measurement.param_name].append(
                deltas[measurement.run_meas_id])
    assert len(per_channel) >= 3
    for name, values in per_channel.items():
        sensitivity = dict(world.channel(name).sensitivities).get("param_bias")
        assert sensitivity and sensitivity > 0.0
        assert st.mean(values) > 0.0


# ------------------------------------------------------------ the variation


def test_a_null_world_is_not_a_flat_process(null, world):
    """Every declared layer of the stack is present without any fault."""
    response, observations = null
    assert response.realization.mechanisms == ()
    runs = runs_by_id(response)
    step_id = world.step_by_name("GATE_ETCH").step_id

    per_chamber = defaultdict(list)
    per_week = defaultdict(list)
    per_lot = defaultdict(list)
    for measurement in observations.channel("gas_flow_sccm"):
        run = runs[measurement.run_id]
        if run.step_id != step_id:
            continue
        delta = measurement.value - measurement.set_value
        per_chamber[run.chamber_id].append(delta)
        per_week[run.start_min // (7 * 24 * 60)].append(delta)
        per_lot[run.lot_id].append(delta)

    chamber_means = [st.mean(v) for v in per_chamber.values() if len(v) > 20]
    week_means = [st.mean(v) for v in per_week.values() if len(v) > 20]
    lot_means = [st.mean(v) for v in per_lot.values() if len(v) > 5]
    assert st.pstdev(chamber_means) > 0.0      # chambers differ
    assert st.pstdev(week_means) > 0.0         # the fab wanders
    assert st.pstdev(lot_means) > 0.0          # lots differ
    within = st.mean([st.pstdev(v) for v in per_chamber.values()
                      if len(v) > 20])
    assert within > 0.0                        # runs differ


def test_healthy_chambers_overlap_heavily(null, world):
    """No chamber is separable from another in a null world."""
    response, observations = null
    runs = runs_by_id(response)
    step_id = world.step_by_name("GATE_ETCH").step_id
    per_chamber = defaultdict(list)
    for measurement in observations.channel("gas_flow_sccm"):
        run = runs[measurement.run_id]
        if run.step_id == step_id:
            per_chamber[run.chamber_id].append(
                measurement.value - measurement.set_value)
    groups = [v for v in per_chamber.values() if len(v) > 20]
    assert len(groups) >= 5
    between = st.pstdev([st.mean(v) for v in groups])
    within = st.mean([st.pstdev(v) for v in groups])
    assert between < within          # chamber identity explains little


def test_a_faulted_chamber_is_not_perfectly_separable(world, drift):
    """The audited 4σ single-GROUP-BY giveaway has no successor.

    Per-run distributions of the affected chamber and its healthy peers
    overlap: neither the minimum of one nor the maximum of the other
    partitions them.
    """
    response, observations = drift
    chamber = target_chamber(world, DRIFT_EVENT)
    runs = runs_by_id(response)
    onset = DRIFT_EVENT["onset_day"] * 24 * 60
    affected, healthy = [], []
    for measurement in observations.channel("gas_flow_sccm"):
        run = runs[measurement.run_id]
        if run.start_min < onset:
            continue
        delta = measurement.value - measurement.set_value
        (affected if run.chamber_id == chamber else healthy).append(delta)
    assert len(affected) > 20 and len(healthy) > 100
    assert min(affected) < st.median(healthy) < max(affected)
    assert min(healthy) < st.median(affected) < max(healthy)


def test_the_fab_week_term_is_shared_across_chambers(null, world):
    """Shared wander is what makes "the fab moved" distinguishable from
    "this chamber moved"; independent noise per chamber would not."""
    response, observations = null
    runs = runs_by_id(response)
    per_chamber_week = defaultdict(list)
    for measurement in observations.channel("gas_flow_sccm"):
        run = runs[measurement.run_id]
        per_chamber_week[(run.chamber_id,
                          run.start_min // (7 * 24 * 60))].append(
            measurement.value - measurement.set_value)

    weeks = defaultdict(list)
    for (chamber_id, week), values in per_chamber_week.items():
        if len(values) >= 3:
            weeks[week].append((chamber_id, st.mean(values)))
    common = [w for w, entries in weeks.items() if len(entries) >= 4]
    assert len(common) >= 6

    # Chambers move together within a week more than chance would give.
    pairs = defaultdict(dict)
    for week in common:
        for chamber_id, value in weeks[week]:
            pairs[chamber_id][week] = value
    series = [c for c in pairs.values() if len(c) >= 6]
    assert len(series) >= 3
    correlations = []
    for i in range(len(series)):
        for j in range(i + 1, len(series)):
            shared = sorted(set(series[i]) & set(series[j]))
            if len(shared) >= 6:
                a = [series[i][w] for w in shared]
                b = [series[j][w] for w in shared]
                correlations.append(_pearson(a, b))
    assert correlations
    assert st.mean(correlations) > 0.1


def _pearson(a, b):
    mean_a, mean_b = st.mean(a), st.mean(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den = math.sqrt(sum((x - mean_a) ** 2 for x in a)
                    * sum((y - mean_b) ** 2 for y in b))
    return num / den if den else 0.0


def test_products_keep_their_own_behaviour_without_predicting_anything(
        world, drift):
    """Scenario G's precondition: products differ, but product identity is
    not a fault detector."""
    response, observations = drift
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    chamber = target_chamber(world, DRIFT_EVENT)

    per_product = defaultdict(list)
    exposure = defaultdict(lambda: [0, 0])
    for row in observations.metrology:
        if row.param_name != "cd_nm_center":
            continue
        run = byrun[(row.wafer_id, row.flow_step_id)]
        product_id = response.timeline.lot(run.lot_id).product_id
        per_product[product_id].append(row.value)
        exposure[product_id][run.chamber_id == chamber] += 1

    assert len(per_product) >= 5
    means = {p: st.mean(v) for p, v in per_product.items()}
    assert st.pstdev(list(means.values())) > 1.0       # products differ
    # Every product both saw and missed the affected chamber.
    seen = [p for p, (_no, yes) in exposure.items() if yes > 0]
    assert len(seen) >= 3
    for product_id in seen:
        assert exposure[product_id][0] > 0


# ------------------------------------------------------------- the clock


def test_no_measurement_exists_for_a_blocked_chamber(null):
    """No run happened there, so no reading can describe one."""
    response, observations = null
    blocking = defaultdict(list)
    for interval in response.timeline.states:
        if interval.state in ("DOWN", "PM", "QUAL"):
            blocking[interval.chamber_id].append((interval.start_min,
                                                  interval.end_min))
    runs = runs_by_id(response)
    for measurement in observations.run_measurements:
        run = runs[measurement.run_id]
        for start, end in blocking[run.chamber_id]:
            assert not (run.start_min < end and start < run.end_min)


def test_a_metrology_reading_follows_the_step_it_measures(null):
    response, observations = null
    byrun = {(r.wafer_id, r.flow_step_id): r for r in response.timeline.runs}
    assert observations.metrology
    for row in observations.metrology:
        measured_run = byrun[(row.wafer_id, row.flow_step_id)]
        assert row.meas_time_min >= measured_run.end_min


def test_a_measurement_never_reads_the_future(world, drift):
    """A run that finished before an onset cannot carry any of it — which is
    also what makes the latent window rule checkable."""
    response, observations = drift
    chamber = target_chamber(world, DRIFT_EVENT)
    deltas = contribution(response, observations)
    runs = runs_by_id(response)
    grid = response.realization.grid
    departure = response.realization.trajectory(chamber,
                                                "param_bias").departure()
    for measurement in observations.run_measurements:
        run = runs[measurement.run_id]
        if run.chamber_id != chamber or deltas[measurement.run_meas_id] == 0.0:
            continue
        first = grid.index_at(run.start_min)
        last = grid.index_at(max(run.start_min, run.end_min - 1))
        assert any(departure[i] != 0.0 for i in range(first, last + 1))


# --------------------------------------------------------------- determinism


def test_the_same_inputs_produce_the_same_measurements(world):
    config = from_mapping({**SCENARIO, "events": [EDGE_EVENT]})
    first = respond_scenario(config, world=world)
    second = respond_scenario(config, world=world)
    assert (observe_response(first).content_sha256()
            == observe_response(second).content_sha256())


def test_measuring_twice_gives_the_same_numbers(null):
    response, observations = null
    again = observe(response.timeline, response.realization)
    assert again.content_sha256() == observations.content_sha256()
    assert [m.value for m in again.run_measurements] == [
        m.value for m in observations.run_measurements]


def test_a_different_seed_changes_values_but_not_structure(world, null):
    _response, observations = null
    other_response, other = measured(world, default_seed=43)
    assert other.content_sha256() != observations.content_sha256()
    assert other.model == observations.model
    assert ({m.param_name for m in other.run_measurements}
            == {m.param_name for m in observations.run_measurements})
    assert ({m.param_name for m in other.metrology}
            == {m.param_name for m in observations.metrology})
    assert other.run_measurements and other.metrology


def test_drawing_an_unrelated_substream_changes_nothing(null):
    response, observations = null
    for index in range(200):
        stream(response.timeline.seed, "some.future.subsystem",
               index).random()
    assert (observe(response.timeline, response.realization).content_sha256()
            == observations.content_sha256())


def test_prose_does_not_reach_a_measurement(world, null):
    _response, observations = null
    renamed, other = measured(world, name="something else entirely",
                              description="rewritten prose")
    assert other.content_sha256() == observations.content_sha256()


_PROBE = """
from fabsim.observation import observe_response
from fabsim.response import respond_scenario
from fabsim.scenario import from_mapping
config = from_mapping({
    "fabsim": "scenario/v1", "name": "null", "world": "baseline_fab_v1",
    "horizon_days": 30, "lots": 6, "default_seed": 42,
})
print(observe_response(respond_scenario(config)).content_sha256())
"""


def test_measurements_do_not_depend_on_the_process_they_ran_in(tmp_path):
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


# ------------------------------------------------------------ anti-leakage


def _module() -> Path:
    return (Path(__file__).resolve().parents[2] / "src" / "fabsim"
            / "observation.py")


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


def test_the_observation_engine_cannot_see_a_mechanism():
    """It is handed the realization and reads only trajectories from it."""
    forbidden = {"mechanisms", "distractors", "counterfactual", "departure",
                 "mechanism", "realized_magnitude", "realized_shift_sigma",
                 "severity", "event_index", "onset_minute", "resets",
                 "alarm_details", "repairs", "detail"}
    assert not (_identifiers(_module()) & forbidden)


def test_the_observation_engine_names_no_entity_or_scenario():
    pattern = re.compile(
        r"(ETCH|CVD|LITHO|CMP|PVD|FURN|IMP|MET|INSP|TEST)-\d")
    strings = _code_strings(_module())
    assert [s for s in strings if pattern.search(s)] == []
    forbidden = {"scenario", "scenario_name", "config", "events"}
    assert not (_identifiers(_module()) & forbidden)


def test_the_observation_engine_reaches_no_later_plane():
    """3D and 3E concepts are absent: no defect, no die, no yield."""
    forbidden = {"defect", "defects", "classified_type", "die_bins", "die_x",
                 "die_y", "wafer_yield", "good_die", "bin_code", "yield_pct",
                 "inspection", "inspections", "kill_probability"}
    assert not (_identifiers(_module()) & forbidden)


def test_the_observation_engine_reads_and_writes_no_truth():
    source = _module().read_text(encoding="utf-8")
    for token in ("truth.json", "truth/", "open(", "write_text", "json.dump",
                  "sqlite3"):
        assert token not in source, token
    repository = Path(__file__).resolve().parents[2]
    assert not list(repository.glob("**/truth.json"))
    assert not (repository / "data" / "scenarios").exists()


def test_no_counterfactual_value_reaches_an_observable_record(world, drift):
    """The counterfactual is a test instrument; it is not in the output.

    Every emitted number is the realized one — measuring the shadow gives
    different values, so no record can be carrying both.
    """
    response, observations = drift
    shadow = observe(response.timeline, without_mechanisms(response.realization))
    assert shadow.content_sha256() != observations.content_sha256()
    realized = {m.run_meas_id: m.value for m in observations.run_measurements}
    counter = {m.run_meas_id: m.value for m in shadow.run_measurements}
    assert any(realized[k] != counter[k] for k in realized)
    for row in observations.run_measurements[:100]:
        assert not hasattr(row, "counterfactual")


def test_the_engine_is_generic_over_chambers(null, world):
    """Exactly the chambers with an applicable channel, and every one of them.

    Not "most" and not "at least a few": the set measured is precisely the set
    the world's channel definitions cover, so no chamber is special-cased in
    either direction. The five that go unmeasured are the metrology,
    inspection, test and implant chambers, for which the template declares no
    FDC channel at all.
    """
    response, observations = null
    runs = runs_by_id(response)
    seen = {runs[m.run_id].chamber_id for m in observations.run_measurements}
    qualified = {c.chamber_id for c in world.chambers
                 if any(channel.kind == "fdc"
                        for op in world.tool(c.tool_id).operations
                        for channel in world.channels_for_operation(op))}
    assert len(seen) >= 15
    assert seen == qualified
