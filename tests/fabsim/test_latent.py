"""
Invariant tests for `fabsim.latent` — the hidden plane.

They are written against properties rather than against numbers: a test that
pinned a trajectory's digest would fail on every legitimate tuning change and
prove nothing about the physics. What is pinned is that two runs agree, that a
null world carries every distribution family a faulted one does, that a fault
is invisible before its onset and measurable after it, that maintenance means
what the design says it means to each latent, and — the property the whole
plane exists for — that nothing here can reach an observable.

The severity numbers *are* checked against the design's σ ladder, because
`CAUSAL_MECHANISM_MODEL.md` §8 makes them a contract rather than a taste. They
are checked against the **null latent distribution** and nothing else: no
yield, no defect count, no diagnostic score — none of which exist yet, and
calibrating against them is how target leakage comes back wearing a suit.
"""
from __future__ import annotations

import ast
import json
import math
import os
import statistics as st
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fabsim.latent import (
    LATENT_BLOCK_POINTS,
    LATENT_GRID_MINUTES,
    LATENT_MODEL,
    Realization,
    realize,
    realize_scenario,
)
from fabsim.rng import stream
from fabsim.scenario import from_mapping
from fabsim.timeline import simulate_scenario
from fabsim.world import MINUTES_PER_DAY

BASELINE_WORLD = "baseline_fab_v1"
BASELINE_LOTS = 20
BASELINE_HORIZON_DAYS = 84
BASELINE_SEED = 42

SCENARIO: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "null",
    "world": BASELINE_WORLD,
    "horizon_days": BASELINE_HORIZON_DAYS,
    "lots": BASELINE_LOTS,
    "default_seed": BASELINE_SEED,
}

EDGE_EVENT: dict[str, Any] = {
    "mechanism": "chamber_edge_uniformity",
    "target": {"tool": "ETCH-02", "chamber": "B"},
    "onset_day": 35,
    "profile": {"type": "ramp", "ramp_days": 7},
    "severity": "moderate",
}

DRIFT_EVENT: dict[str, Any] = {
    "mechanism": "param_drift",
    "target": {"tool": "ETCH-01", "chamber": "A"},
    "onset_day": 30,
    "profile": {"type": "ramp", "ramp_days": 14},
    "severity": "moderate",
}

PARTICLE_EVENT: dict[str, Any] = {
    "mechanism": "particle_excursion",
    "target": {"tool": "CVD-01", "chamber": "A"},
    "onset_day": 40,
    "profile": {"type": "step"},
    "severity": "moderate",
}


def scenario(**overrides: Any) -> dict[str, Any]:
    raw = json.loads(json.dumps(SCENARIO))
    raw.update(overrides)
    return raw


#: Timelines, by the only three fields that can change one here. The timeline
#: is blind to `events` and `distractors` by design (`TEMPORAL_MODEL.md` §7),
#: so two scenarios differing only in their faults share a schedule — which is
#: what makes this cache correct rather than merely convenient, and what
#: `test_the_schedule_is_the_same_whatever_the_fault` keeps honest.
_TIMELINES: dict[tuple[int, int, int], Any] = {}


def realized(world, **overrides: Any) -> Realization:
    """Simulate and realize one scenario over the shared baseline world."""
    config = from_mapping(scenario(**overrides))
    key = (config.lots, config.horizon_days, config.default_seed)
    if key not in _TIMELINES:
        _TIMELINES[key] = simulate_scenario(config, world=world)
    return realize_scenario(config, _TIMELINES[key])


def target_chamber(world, event: dict[str, Any]) -> int:
    return world.chamber_by_name(event["target"]["tool"],
                                 event["target"]["chamber"]).chamber_id


def lag_one_autocorrelation(series) -> float:
    mean = sum(series) / len(series)
    numerator = sum((series[i] - mean) * (series[i + 1] - mean)
                    for i in range(len(series) - 1))
    denominator = sum((value - mean) ** 2 for value in series)
    return numerator / denominator


# --------------------------------------------------------------- the clock


def test_the_latent_grid_is_the_project_clock_sampled(realization, timeline):
    """One clock, sampled — not a second timeline (`TEMPORAL_MODEL.md` §1)."""
    grid = realization.grid
    assert LATENT_GRID_MINUTES == 60
    assert grid.step_minutes == LATENT_GRID_MINUTES
    assert grid.horizon_minutes == timeline.horizon_minutes
    assert grid.points * grid.step_minutes >= timeline.horizon_minutes
    assert grid.minute_of(0) == 0
    assert grid.index_at(0) == 0
    assert grid.index_at(timeline.horizon_minutes) == grid.points - 1
    for run in timeline.runs[:50]:
        index = grid.index_at(run.start_min)
        assert grid.minute_of(index) <= run.start_min < grid.minute_of(index + 1)


def test_the_grid_is_versioned_and_blocked_by_the_day(realization):
    """Both constants change every realization, so both are versioned."""
    assert LATENT_MODEL == "fabsim.latent/v1"
    assert realization.model == LATENT_MODEL
    assert LATENT_BLOCK_POINTS == MINUTES_PER_DAY // LATENT_GRID_MINUTES
    assert realization.grid.points_per_day == 24.0
    assert realization.grid.weeks == 12


def test_weeks_partition_the_grid(realization):
    grid = realization.grid
    assert grid.week_of(0) == 0
    assert grid.week_of(grid.points_per_week) == 1
    assert grid.week_of(grid.points - 1) == grid.weeks - 1


# --------------------------------------------------------------- determinism


def test_the_same_inputs_produce_the_same_realization(timeline):
    assert (realize(timeline).content_sha256()
            == realize(timeline).content_sha256())


def test_the_same_seed_produces_the_same_trajectories_value_by_value(timeline):
    first, second = realize(timeline), realize(timeline)
    for left, right in zip(first.trajectories, second.trajectories):
        assert left.values == right.values
        assert left.counterfactual == right.counterfactual


def test_a_different_seed_is_a_different_realization_of_the_same_shape(world):
    baseline = realized(world)
    other = realized(world, default_seed=43)
    assert baseline.content_sha256() != other.content_sha256()
    assert baseline.latents == other.latents
    assert len(baseline.trajectories) == len(other.trajectories)
    assert ({(t.chamber_id, t.latent) for t in baseline.trajectories}
            == {(t.chamber_id, t.latent) for t in other.trajectories})


def test_drawing_an_unrelated_substream_changes_nothing(timeline):
    """A future subsystem taking its own stream may not move this one."""
    before = realize(timeline).content_sha256()
    for index in range(200):
        stream(timeline.seed, "some.future.subsystem", index).random()
    assert realize(timeline).content_sha256() == before


_PROBE = """
import sys
from fabsim.latent import realize
from fabsim.scenario import from_mapping
from fabsim.timeline import simulate_scenario
config = from_mapping({
    "fabsim": "scenario/v1", "name": "null", "world": "baseline_fab_v1",
    "horizon_days": 84, "lots": 20, "default_seed": 42,
})
print(realize(simulate_scenario(config)).content_sha256())
"""


def test_a_realization_does_not_depend_on_the_process_it_ran_in(tmp_path):
    """No wall clock, no cwd, no locale, no hash salt."""
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


def test_an_unrelated_event_leaves_every_other_chamber_untouched(world):
    """Stream stability where it matters: a fault on one tool may not
    reshuffle the physics of a tool it never touched."""
    null = realized(world)
    faulted = realized(world, events=[PARTICLE_EVENT])
    affected = set(faulted.mechanisms[0].chamber_ids)

    compared = 0
    for trajectory in null.trajectories:
        if trajectory.chamber_id in affected:
            continue
        other = faulted.trajectory(trajectory.chamber_id, trajectory.latent)
        assert other.values == trajectory.values
        compared += 1
    assert compared > 60


def test_adding_an_event_does_not_reshuffle_the_one_already_there(world):
    """Mechanism streams are keyed by activation, so appending a second fault
    cannot silently re-realize the first."""
    single = realized(world, events=[EDGE_EVENT])
    both = realized(world, events=[EDGE_EVENT, PARTICLE_EVENT])

    assert (single.mechanisms[0].realized_magnitude
            == both.mechanisms[0].realized_magnitude)
    chamber = target_chamber(world, EDGE_EVENT)
    assert (single.trajectory(chamber, "edge_uniformity").values
            == both.trajectory(chamber, "edge_uniformity").values)


def test_realizing_does_not_disturb_the_timeline(world):
    """Step 2 behaviour is unchanged: the latent plane reads the schedule."""
    config = from_mapping(scenario(events=[EDGE_EVENT]))
    timeline = simulate_scenario(config, world=world)
    before = timeline.content_sha256()
    realize_scenario(config, timeline)
    assert timeline.content_sha256() == before


def test_the_schedule_is_the_same_whatever_the_fault(world):
    """Still true after 3A, and the reason a faulted realization can be built
    on the null's schedule: the timeline never reads `events`."""
    null = simulate_scenario(from_mapping(scenario()), world=world)
    faulted = simulate_scenario(
        from_mapping(scenario(events=[EDGE_EVENT, PARTICLE_EVENT],
                              distractors=[{"mechanism": "benign_offset",
                                            "target": {"tool": "CVD-01"},
                                            "magnitude": "large"}])),
        world=world)
    assert null.content_sha256() == faulted.content_sha256()


# ---------------------------------------------------------------- the null


def test_the_null_scenario_has_no_mechanism_departure(realization):
    assert realization.mechanisms == ()
    assert realization.distractors == ()
    for trajectory in realization.trajectories:
        assert trajectory.values == trajectory.counterfactual
        assert set(trajectory.departure()) == {0.0}


def test_the_null_carries_every_latent_on_every_chamber(realization, world):
    """Requirement F10: no distribution component exists only where a
    mechanism does, so a null world exercises the whole vocabulary."""
    assert set(realization.latents) == set(world.observation.latents)
    expected = {(c.chamber_id, latent) for c in world.chambers
                for latent in world.observation.latents}
    assert {(t.chamber_id, t.latent) for t in realization.trajectories} \
        == expected
    assert len(realization.trajectories) == 24 * 3


def test_every_null_trajectory_actually_varies(realization):
    """A latent pinned flat would be a component that exists in name only."""
    for trajectory in realization.trajectories:
        assert len(set(trajectory.values)) > 100
        assert st.pstdev(trajectory.values) > 0.0


def test_the_null_stays_inside_its_documented_bands(realization, world):
    """Baseline wander is wander: a healthy chamber's weekly statistic moves
    by about the σ the world declares, and not by a fault's worth."""
    for latent in realization.latents:
        dynamics = world.latent(latent)
        deviations = []
        for trajectory in realization.trajectories:
            if trajectory.latent != latent:
                continue
            weekly = trajectory.weekly_means()
            centre = sum(weekly) / len(weekly)
            deviations += [value - centre for value in weekly]
        measured = st.pstdev(deviations)
        ratio = measured / dynamics.severity_reference
        assert 0.7 < ratio < 1.3, (latent, ratio)


def test_the_declared_severity_reference_matches_the_null_world(world):
    """The σ severity is quoted in has to be true of the world it describes.

    Measured on the *counterfactual* series, so the check is valid on a
    faulted realization too — the yardstick is the null this world would have
    been at this seed.
    """
    for seed in (7, 42, 101):
        realization = realized(world, default_seed=seed,
                               events=[EDGE_EVENT])
        for latent in realization.latents:
            reference = world.latent(latent).severity_reference
            deviations = []
            for trajectory in realization.trajectories:
                if trajectory.latent != latent:
                    continue
                weekly = trajectory.weekly_means(trajectory.counterfactual)
                centre = sum(weekly) / len(weekly)
                deviations += [value - centre for value in weekly]
            assert 0.7 < st.pstdev(deviations) / reference < 1.3, (seed, latent)


# ------------------------------------------------------------ benign offsets


def test_every_chamber_carries_an_offset_on_every_latent(realization, world):
    """Requirement F11: offsets are baseline, not distractor bookkeeping."""
    assert len(realization.offsets) == 24 * 3
    for offset in realization.offsets:
        assert offset.total != 0.0
        assert offset.distractor_component == 0.0   # the null declares none


def test_offsets_exist_without_any_distractor_being_declared(realization):
    assert realization.distractors == ()
    assert all(o.tool_component != 0.0 and o.chamber_component != 0.0
               for o in realization.offsets)


def test_offsets_overlap_the_subtle_severity_band(realization, world):
    """They must not be separable from a subtle fault by size alone."""
    for latent in realization.latents:
        dynamics = world.latent(latent)
        subtle = (world.observation.sigma_for("subtle")
                  * dynamics.severity_reference)
        totals = [abs(o.total) for o in realization.offsets
                  if o.latent == latent]
        assert max(totals) > 0.5 * subtle, latent


def test_wander_offsets_are_signed_and_load_offsets_are_not(realization,
                                                            world):
    """A chamber can run edge-fast or edge-slow; residual contamination
    cannot be negative."""
    for latent in realization.latents:
        totals = [o.total for o in realization.offsets if o.latent == latent]
        if world.latent(latent).family == "ar1":
            assert min(totals) < 0.0 < max(totals)
        else:
            assert min(totals) > 0.0


def test_a_declared_distractor_widens_an_offset_rather_than_creating_one(
        world):
    declared = realized(world, distractors=[
        {"mechanism": "benign_offset", "target": {"tool": "CVD-01"},
         "magnitude": "large"}])
    (record,) = declared.distractors
    assert record.mechanism == "benign_offset"
    assert record.chamber_ids == world.tool_by_name("CVD-01").chamber_ids
    assert record.added

    for offset in declared.offsets:
        assert offset.total != 0.0                    # still everywhere
        if offset.chamber_id in record.chamber_ids:
            assert offset.distractor_component != 0.0
        else:
            assert offset.distractor_component == 0.0


def test_a_distractor_produces_no_departure_at_all(world):
    """It is an offset, not a fault: nothing about it evolves."""
    declared = realized(world, distractors=[
        {"mechanism": "benign_offset", "target": {"tool": "CVD-01"},
         "magnitude": "large"}])
    assert declared.mechanisms == ()
    for trajectory in declared.trajectories:
        assert set(trajectory.departure()) == {0.0}


def test_offsets_are_flat_where_faults_are_not(world):
    """The only thing separating a benign offset from a fault is its shape in
    time — which is what the diagnosis engine will have to find."""
    declared = realized(world, distractors=[
        {"mechanism": "benign_offset", "target": {"tool": "CVD-01"},
         "magnitude": "large"}])
    chamber = world.chamber_by_name("CVD-01", "A").chamber_id
    trajectory = declared.trajectory(chamber, "param_bias")
    weekly = trajectory.weekly_means()
    first_half = weekly[:len(weekly) // 2]
    second_half = weekly[len(weekly) // 2:]
    early = sum(first_half) / len(first_half)
    late = sum(second_half) / len(second_half)
    reference = world.latent("param_bias").severity_reference
    assert abs(late - early) < 1.5 * reference


# ------------------------------------------------------------ onset and ramp


def test_a_fault_is_invisible_before_its_onset(world):
    """Bit-identical to the null it would have been, not merely close."""
    faulted = realized(world, events=[EDGE_EVENT])
    chamber = target_chamber(world, EDGE_EVENT)
    trajectory = faulted.trajectory(chamber, "edge_uniformity")
    onset = faulted.grid.index_at(EDGE_EVENT["onset_day"] * MINUTES_PER_DAY)
    assert onset > 0
    assert trajectory.values[:onset] == trajectory.counterfactual[:onset]
    assert trajectory.values[onset:] != trajectory.counterfactual[onset:]


def test_edge_uniformity_follows_the_configured_ramp(world):
    faulted = realized(world, events=[EDGE_EVENT])
    record = faulted.mechanisms[0]
    chamber = target_chamber(world, EDGE_EVENT)
    trajectory = faulted.trajectory(chamber, "edge_uniformity")
    grid = faulted.grid
    departure = trajectory.departure()

    onset = grid.index_at(EDGE_EVENT["onset_day"] * MINUTES_PER_DAY)
    ramped = grid.index_at((EDGE_EVENT["onset_day"]
                            + EDGE_EVENT["profile"]["ramp_days"])
                           * MINUTES_PER_DAY)

    assert departure[onset] == pytest.approx(record.realized_magnitude
                                             / (7 * 24), rel=0.05)
    climb = departure[onset:ramped]
    assert climb == tuple(sorted(climb))
    assert departure[ramped - 1] == pytest.approx(record.realized_magnitude,
                                                  rel=1e-6)
    # Sustained afterwards: hardware does not heal on its own.
    assert departure[-1] == pytest.approx(record.realized_magnitude, rel=1e-6)


def test_a_step_profile_arrives_at_once(world):
    faulted = realized(world, events=[dict(EDGE_EVENT,
                                           profile={"type": "step"})])
    chamber = target_chamber(world, EDGE_EVENT)
    departure = faulted.trajectory(chamber, "edge_uniformity").departure()
    onset = faulted.grid.index_at(EDGE_EVENT["onset_day"] * MINUTES_PER_DAY)
    magnitude = faulted.mechanisms[0].realized_magnitude
    assert departure[onset] == pytest.approx(magnitude, rel=1e-6)


def test_a_tool_wide_target_moves_every_chamber_of_the_tool(world):
    faulted = realized(world, events=[dict(EDGE_EVENT,
                                           target={"tool": "ETCH-02"})])
    tool = world.tool_by_name("ETCH-02")
    assert faulted.mechanisms[0].chamber_ids == tool.chamber_ids
    for chamber_id in tool.chamber_ids:
        departure = faulted.trajectory(chamber_id, "edge_uniformity"
                                       ).departure()
        assert max(departure) > 0.0
    for chamber in world.chambers_of(world.tool_by_name("ETCH-01").tool_id):
        assert set(faulted.trajectory(chamber.chamber_id, "edge_uniformity"
                                      ).departure()) == {0.0}


def test_a_fault_touches_only_its_own_latent(world):
    faulted = realized(world, events=[EDGE_EVENT])
    chamber = target_chamber(world, EDGE_EVENT)
    for latent in faulted.latents:
        departure = faulted.trajectory(chamber, latent).departure()
        if latent == "edge_uniformity":
            assert max(departure) > 0.0
        else:
            assert set(departure) == {0.0}


# ---------------------------------------------------------- baseline families


def test_param_bias_wanders_with_the_configured_autocorrelation(realization,
                                                                world):
    """AR(1) with φ≈0.98 (`CAUSAL_MECHANISM_MODEL.md` §1), on every chamber."""
    for latent in ("param_bias", "edge_uniformity"):
        phi = world.latent(latent).phi
        measured = [lag_one_autocorrelation(t.values)
                    for t in realization.trajectories if t.latent == latent]
        assert abs(st.mean(measured) - phi) < 0.02, latent
        assert min(measured) > phi - 0.05, latent


def test_param_drift_is_a_trend_buried_in_wander_not_a_ruler(world):
    """A perfectly straight latent would be a signature rather than physics."""
    faulted = realized(world, events=[DRIFT_EVENT])
    chamber = target_chamber(world, DRIFT_EVENT)
    trajectory = faulted.trajectory(chamber, "param_bias")
    grid = faulted.grid
    after = trajectory.values[grid.index_at(
        (DRIFT_EVENT["onset_day"] + 20) * MINUTES_PER_DAY):]

    steps = [b - a for a, b in zip(after, after[1:])]
    assert min(steps) < 0.0 < max(steps)      # it goes down as well as up
    assert st.pstdev(steps) > 0.0


def test_particle_load_saws_between_cleans(realization, world):
    """The baseline sawtooth of `CAUSAL_MECHANISM_MODEL.md` §3, on every
    chamber — not only where an excursion was configured."""
    grid = realization.grid
    checked = 0
    for chamber in world.chambers:
        trajectory = realization.trajectory(chamber.chamber_id,
                                            "particle_load")
        offset = realization.offset(chamber.chamber_id, "particle_load").total
        resets = realization.resets_of(chamber.chamber_id, "particle_load")
        for reset in resets:
            index = grid.index_at(reset.minute)
            if index < 24 or index >= grid.points - 1:
                continue
            before = trajectory.values[index - 2]
            after = trajectory.values[index + 1]
            assert after < before                     # the clean happened
            assert after == pytest.approx(offset, abs=0.002)   # back to base
            checked += 1
    assert checked > 30


def test_particle_load_never_goes_below_its_floor(realization):
    for trajectory in realization.trajectories:
        if trajectory.latent != "particle_load":
            continue
        assert min(trajectory.values) >= 0.0


def test_a_particle_excursion_climbs_faster_than_the_baseline(world):
    faulted = realized(world, events=[PARTICLE_EVENT])
    chamber = target_chamber(world, PARTICLE_EVENT)
    trajectory = faulted.trajectory(chamber, "particle_load")
    grid = faulted.grid
    onset = grid.index_at(PARTICLE_EVENT["onset_day"] * MINUTES_PER_DAY)

    def slope(series, start, days):
        end = min(len(series) - 1, start + int(days * 24))
        return (series[end] - series[start]) / days

    baseline_rate = slope(trajectory.counterfactual, onset, 5)
    excursion_rate = slope(trajectory.values, onset, 5)
    assert excursion_rate > 3 * baseline_rate


# ------------------------------------------------------------- maintenance


def test_a_pm_cleans_particle_load_completely(realization, world):
    assert world.latent("particle_load").pm_recovery_mean == 1.0
    resets = [r for r in realization.resets if r.latent == "particle_load"]
    assert resets
    assert all(r.fraction == 1.0 for r in resets)


def test_a_pm_partly_recentres_param_bias(realization, world):
    """`N(0.7, 0.1)` per PM (`CAUSAL_MECHANISM_MODEL.md` §6): most of the
    deviation goes, and what is left is why "did it work?" is a real question.
    """
    dynamics = world.latent("param_bias")
    assert (dynamics.pm_recovery_mean, dynamics.pm_recovery_sd) == (0.7, 0.1)
    fractions = [r.fraction for r in realization.resets
                 if r.latent == "param_bias"]
    assert len(fractions) > 20
    assert 0.6 < st.mean(fractions) < 0.8
    assert len(set(fractions)) == len(fractions)     # drawn, not constant

    grid = realization.grid
    ratios = []
    for reset in realization.resets:
        if reset.latent != "param_bias":
            continue
        index = grid.index_at(reset.minute)
        if index < 2 or index >= grid.points - 2:
            continue
        trajectory = realization.trajectory(reset.chamber_id, "param_bias")
        before = abs(trajectory.values[index - 2] - trajectory.offset)
        after = abs(trajectory.values[index + 1] - trajectory.offset)
        if before > 1e-9:
            ratios.append(after / before)
    assert st.median(ratios) < 0.6


def test_a_pm_does_not_touch_edge_uniformity(realization, world):
    """Hardware is not fixed by cleaning (`CAUSAL_MECHANISM_MODEL.md` §6)."""
    assert world.latent("edge_uniformity").pm_recovery_mean == 0.0
    assert not [r for r in realization.resets
                if r.latent == "edge_uniformity"]


def test_a_pm_does_not_survive_into_a_fault_it_cannot_reach(world):
    """An edge-uniformity fault runs straight through every PM in the window."""
    faulted = realized(world, events=[EDGE_EVENT])
    chamber = target_chamber(world, EDGE_EVENT)
    departure = faulted.trajectory(chamber, "edge_uniformity").departure()
    magnitude = faulted.mechanisms[0].realized_magnitude
    grid = faulted.grid
    settled = grid.index_at((EDGE_EVENT["onset_day"] + 14) * MINUTES_PER_DAY)
    assert all(value == pytest.approx(magnitude, rel=1e-6)
               for value in departure[settled:])


def test_a_pm_does_not_reset_the_benign_offset(realization, world):
    """The offset is what a chamber returns *to*, not something it loses."""
    grid = realization.grid
    checked = 0
    for reset in realization.resets:
        if reset.latent != "particle_load":
            continue
        index = grid.index_at(reset.minute)
        if index >= grid.points - 1:
            continue
        offset = realization.offset(reset.chamber_id, "particle_load")
        after = realization.trajectory(reset.chamber_id,
                                       "particle_load").values[index + 1]
        assert after == pytest.approx(offset.total, abs=0.002)
        assert offset.total > 0.0
        checked += 1
    assert checked > 30


def test_only_pms_move_latents_at_this_gate(realization, timeline):
    """Fault-driven repair is 3B. Unscheduled windows exist in the timeline
    and deliberately do not reach the latent plane yet — bringing the reset
    forward without its trigger would have made "a repair moved a latent" a
    fault fingerprint, because only faults would ever cause one.
    """
    pm_ids = {w.maint_id for w in timeline.maintenance
              if w.maint_type == "PM"}
    unscheduled = {w.maint_id for w in timeline.maintenance
                   if w.maint_type == "UNSCHEDULED"}
    assert unscheduled
    assert {r.maint_id for r in realization.resets} <= pm_ids


# ------------------------------------------------------- severity calibration


@pytest.mark.parametrize("event, latent", [
    (EDGE_EVENT, "edge_uniformity"),
    (DRIFT_EVENT, "param_bias"),
    (PARTICLE_EVENT, "particle_load"),
])
def test_severity_is_ordered_in_measured_sigma(world, event, latent):
    """subtle < moderate < obvious, measured against the null latent plane."""
    shifts = []
    for severity in ("subtle", "moderate", "obvious"):
        faulted = realized(world, events=[dict(event, severity=severity)])
        shifts.append(faulted.mechanisms[0].realized_shift_sigma)
    assert shifts == sorted(shifts)
    assert len(set(shifts)) == 3
    assert shifts[0] > 0.0


@pytest.mark.parametrize("event, latent", [
    (EDGE_EVENT, "edge_uniformity"),
    (DRIFT_EVENT, "param_bias"),
])
def test_a_wander_fault_lands_on_the_designed_sigma_ladder(world, event,
                                                           latent):
    """`CAUSAL_MECHANISM_MODEL.md` §8: ≈1.5σ / 3σ / 6σ of the weekly statistic.

    `param_bias` lands a little under its target because a PM recentres it
    between onset and measurement — which is the design's own §6 behaviour,
    not a miss.
    """
    for severity in ("subtle", "moderate", "obvious"):
        faulted = realized(world, events=[dict(event, severity=severity)])
        nominal = world.observation.sigma_for(severity)
        realized_sigma = faulted.mechanisms[0].realized_shift_sigma
        assert 0.8 < realized_sigma / nominal < 1.25, (latent, severity)


def test_an_unattended_particle_excursion_exceeds_its_nominal_shift(world):
    """An accumulation latent keeps climbing until something cleans it, so a
    severity sets the escalation rather than a ceiling. That over-run is the
    physics scenario I's repair exists to stop, and 3B is what stops it.
    """
    faulted = realized(world, events=[PARTICLE_EVENT])
    nominal = world.observation.sigma_for(PARTICLE_EVENT["severity"])
    assert faulted.mechanisms[0].realized_shift_sigma > nominal


def test_severity_is_a_target_and_not_a_guarantee(world):
    """Two activations at one severity differ, or severity would be a label
    a later engine could read straight off the effect size."""
    magnitudes = {
        realized(world, events=[dict(EDGE_EVENT, target={"tool": "ETCH-02",
                                                         "chamber": chamber})]
                 ).mechanisms[0].realized_magnitude
        for chamber in ("A", "B", "C")
    }
    assert len(magnitudes) == 1        # same activation index, same draw

    two = realized(world, events=[EDGE_EVENT,
                                  dict(EDGE_EVENT,
                                       target={"tool": "ETCH-03",
                                               "chamber": "A"})])
    assert (two.mechanisms[0].realized_magnitude
            != two.mechanisms[1].realized_magnitude)


def test_the_realized_record_reports_intent_and_realization(world):
    faulted = realized(world, events=[EDGE_EVENT])
    (record,) = faulted.mechanisms
    assert record.mechanism == "chamber_edge_uniformity"
    assert record.latent == "edge_uniformity"
    assert record.severity == "moderate"
    assert record.tool_name == "ETCH-02"
    assert record.chamber_name == "B"
    assert record.onset_minute == EDGE_EVENT["onset_day"] * MINUTES_PER_DAY
    assert record.nominal_magnitude > 0.0
    assert record.realized_magnitude != record.nominal_magnitude
    assert record.active_from_minute >= record.onset_minute
    assert record.active_to_minute >= record.active_from_minute


# --------------------------------------------------------- causal integrity


def _module_files() -> list[Path]:
    package = Path(__file__).resolve().parents[2] / "src" / "fabsim"
    return sorted((package / "mechanisms").glob("*.py")) + [
        package / "latent.py"]


def _identifiers(module: Path) -> set[str]:
    """Every name, attribute and keyword the module's *code* mentions."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def test_the_latent_plane_names_no_observable(world):
    """ADR-004 structurally: there is no `mechanism → yield` path because
    there is no yield here to reach — nor a defect, an alarm or a measurement.
    """
    forbidden = {
        "yield_pct", "wafer_yield", "good_die", "total_die", "die_bins",
        "bin_code", "die_x", "die_y", "defect", "defects", "defect_id",
        "classified_type", "total_defect_count", "inspection", "inspections",
        "alarm", "alarms", "alarm_code", "alarm_time", "metrology",
        "run_measurements", "param_value", "measured_value",
    }
    for module in _module_files():
        hits = sorted(_identifiers(module) & forbidden)
        assert hits == [], (module.name, hits)


def test_the_latent_plane_imports_nothing_observable():
    allowed = {"fabsim", "fabsim.rng", "fabsim.scenario", "fabsim.timeline",
               "fabsim.world", "fabsim.mechanisms", "fabsim.mechanisms.base",
               "fabsim.mechanisms.benign_offset",
               "fabsim.mechanisms.edge_uniformity",
               "fabsim.mechanisms.param_drift",
               "fabsim.mechanisms.particle_load"}
    for module in _module_files():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for name in imported:
                if name.startswith("fabsim"):
                    assert name in allowed, (module.name, name)


def test_a_realization_holds_hidden_state_and_nothing_else():
    assert set(Realization.__dataclass_fields__) == {
        "model", "world", "grid", "seed", "latents", "trajectories",
        "offsets", "mechanisms", "distractors", "resets", "_index"}


def test_a_mechanism_returns_only_numbers(world):
    """The one permitted output of 3A is hidden physical state."""
    from fabsim.mechanisms import MECHANISM_NAMES, mechanism

    faulted = realized(world, events=[EDGE_EVENT, PARTICLE_EVENT])
    for trajectory in faulted.trajectories:
        assert all(isinstance(value, float) for value in trajectory.values)
    assert {type(m).__name__ for m in
            (mechanism(name) for name in MECHANISM_NAMES)}


def test_no_realization_is_written_to_disk(tmp_path, world):
    """The hidden plane is in memory and is handed on explicitly; there is no
    path, no registry and no singleton by which an observable projection could
    find it (`GROUND_TRUTH_CONTRACT.md` §4). Truth emission is a later gate.
    """
    before = {p for p in tmp_path.rglob("*")}
    realized(world, events=[EDGE_EVENT])
    assert {p for p in tmp_path.rglob("*")} == before

    repository = Path(__file__).resolve().parents[2]
    assert not list(repository.glob("**/truth.json"))
    assert not (repository / "data" / "scenarios").exists()


# ------------------------------------------------------------- the front door


def test_realize_scenario_reads_events_and_distractors(world):
    faulted = realized(world, events=[EDGE_EVENT], distractors=[
        {"mechanism": "benign_offset", "target": {"tool": "CVD-01"},
         "magnitude": "small"}])
    assert len(faulted.mechanisms) == 1
    assert len(faulted.distractors) == 1


def test_realize_without_a_scenario_is_the_null_plane(timeline):
    assert realize(timeline).content_sha256() == realize(
        timeline, events=(), distractors=()).content_sha256()


def test_the_grid_covers_a_short_horizon_too(world):
    short = realized(world, horizon_days=10, lots=3,
                     events=[dict(EDGE_EVENT, onset_day=4,
                                  profile={"type": "step"})])
    assert short.grid.points == 10 * 24
    assert short.mechanisms[0].realized_shift_sigma > 0.0


def test_value_at_reads_the_clock(world, timeline):
    realization = realize(timeline)
    chamber = world.chambers[0].chamber_id
    trajectory = realization.trajectory(chamber, "param_bias")
    for minute in (0, 5000, timeline.horizon_minutes - 1):
        assert (realization.value_at(chamber, "param_bias", minute)
                == trajectory.values[realization.grid.index_at(minute)])
    assert not math.isnan(realization.value_at(chamber, "param_bias", 0))
