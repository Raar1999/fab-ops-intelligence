"""
Invariant tests for `fabsim.timeline`.

These enforce the properties the audit found missing, in the order the design
states them: determinism (same inputs, same world), entity integrity (every
reference resolves and every assignment is legal), temporal integrity (one
clock, causal order, downtime that actually stops production), and routing
integrity (availability-driven, blind to the answer).

They are written against invariants rather than against numbers: a test that
pins the digest of a realization would fail on every legitimate tuning change
and prove nothing about the simulation. What is pinned is that two runs agree,
that structure holds under a different seed, and that the rules the design
names are rules and not tendencies.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
from collections import Counter, defaultdict
from typing import Any

import pytest

from fabsim.rng import stream
from fabsim.scenario import ScenarioConfigError, from_mapping
from fabsim.timeline import (
    BLOCKING_STATES,
    EVENT_KINDS,
    LOT_STATUSES,
    MAINTENANCE_TYPES,
    STATES,
    WAFER_STATUSES,
    Timeline,
    simulate,
    simulate_scenario,
)
from fabsim.world import MINUTES_PER_DAY, World

# The shape of the reference realization built by the `timeline` fixture.
# `test_the_reference_realization_is_the_configured_baseline` keeps these and
# the fixture from drifting apart.
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


def baseline(world: World, **overrides: Any) -> Timeline:
    kwargs = {"lots": BASELINE_LOTS, "horizon_days": BASELINE_HORIZON_DAYS,
              "seed": BASELINE_SEED}
    kwargs.update(overrides)
    return simulate(world, **kwargs)


def small(world: World, **overrides: Any) -> Timeline:
    """A cheap realization for tests that do not need twelve weeks."""
    kwargs = {"lots": 4, "horizon_days": 30, "seed": 5}
    kwargs.update(overrides)
    return simulate(world, **kwargs)


def blocking_intervals(timeline: Timeline) -> dict[int, list[tuple[int, int]]]:
    blocks: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for interval in timeline.states:
        if interval.state in BLOCKING_STATES:
            blocks[interval.chamber_id].append((interval.start_min,
                                                interval.end_min))
    return blocks


def assert_structural_invariants(timeline: Timeline) -> None:
    """The invariants that must hold in *every* realization, at any seed."""
    world = timeline.world
    for run in timeline.runs:
        assert run.end_min > run.start_min
        chamber = world.chamber(run.chamber_id)
        assert chamber.tool_id == run.tool_id
        step = world.step(run.step_id)
        assert step.operation_type in world.tool(run.tool_id).operations
    for wafer in timeline.wafers:
        runs = timeline.runs_of_wafer(wafer.wafer_id)
        sequences = [world.flow_step(r.flow_step_id).step_sequence
                     for r in runs]
        assert sequences == list(range(1, len(runs) + 1))
        for earlier, later in zip(runs, runs[1:]):
            assert later.start_min >= earlier.end_min
    for chamber in world.chambers:
        occupancy = sorted(timeline.runs_on_chamber(chamber.chamber_id),
                           key=lambda r: r.start_min)
        for earlier, later in zip(occupancy, occupancy[1:]):
            assert later.start_min >= earlier.end_min
    blocks = blocking_intervals(timeline)
    for run in timeline.runs:
        for start, end in blocks[run.chamber_id]:
            assert not (run.start_min < end and start < run.end_min)


# --------------------------------------------------------------- determinism


def test_the_reference_realization_is_the_configured_baseline(timeline):
    assert timeline.world.template_name == BASELINE_WORLD
    assert timeline.lots_requested == BASELINE_LOTS
    assert timeline.horizon_days == BASELINE_HORIZON_DAYS
    assert timeline.seed == BASELINE_SEED


def test_the_same_seed_produces_the_same_world(world):
    assert baseline(world).content_sha256() == baseline(world).content_sha256()


def test_the_same_seed_produces_the_same_timeline_row_by_row(world):
    first, second = baseline(world), baseline(world)
    assert first.canonical_rows() == second.canonical_rows()
    assert first.runs == second.runs
    assert first.states == second.states
    assert first.maintenance == second.maintenance
    assert first.events == second.events


def test_a_different_seed_is_a_different_realization(world, timeline):
    other = baseline(world, seed=BASELINE_SEED + 1)
    assert other.content_sha256() != timeline.content_sha256()
    assert [lot.release_min for lot in other.lots] != [
        lot.release_min for lot in timeline.lots]
    assert {r.chamber_id for r in other.runs} == {r.chamber_id
                                                 for r in timeline.runs}


@pytest.mark.parametrize("seed", [0, 7, 2026])
def test_structure_survives_any_seed(world, seed):
    assert_structural_invariants(baseline(world, seed=seed))


def test_the_world_is_never_built_from_the_global_random_module(world):
    """Nothing here may read or perturb process-global randomness."""
    random.seed(1234)
    before = random.getstate()
    baseline(world)
    assert random.getstate() == before


def test_drawing_an_unrelated_substream_changes_nothing(world, timeline):
    """Stability under unrelated change: a new stream is a new hash.

    A later slice will draw defects and yields from streams that do not exist
    yet. Exercising one now must not move a single routing decision.
    """
    stream(BASELINE_SEED, "defects", 17, 3).random()
    stream(BASELINE_SEED, "yield", 99).gauss(0, 1)
    assert baseline(world).content_sha256() == timeline.content_sha256()


_PROBE = """
import json, sys
from fabsim.world import load_world
from fabsim.timeline import simulate
world = load_world(sys.argv[1])
timeline = simulate(world, lots=6, horizon_days=40, seed=11)
print(json.dumps({"digest": timeline.content_sha256(),
                  "runs": len(timeline.runs)}))
"""


def test_a_realization_does_not_depend_on_the_process_it_ran_in(tmp_path):
    """Different hash salts, working directories and locales, same world."""
    def probe(directory: str, hash_seed: str, extra: dict[str, str]) -> str:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env.update(extra)
        (tmp_path / directory).mkdir(exist_ok=True)
        result = subprocess.run([sys.executable, "-c", _PROBE, BASELINE_WORLD],
                                cwd=str(tmp_path / directory), env=env,
                                capture_output=True, text=True, check=True)
        return result.stdout

    first = probe("a", "0", {"LANG": "C", "TZ": "UTC"})
    second = probe("b", "999", {"LANG": "de_DE.UTF-8", "TZ": "Asia/Tokyo"})
    assert first == second


def test_lots_already_released_do_not_move_when_more_are_added(world):
    """Per-lot substreams: enlarging a scenario may not rewrite its history."""
    short = simulate(world, lots=5, horizon_days=BASELINE_HORIZON_DAYS,
                     seed=BASELINE_SEED)
    long = simulate(world, lots=12, horizon_days=BASELINE_HORIZON_DAYS,
                    seed=BASELINE_SEED)
    assert [(l.release_min, l.product_id) for l in short.lots] == [
        (l.release_min, l.product_id) for l in long.lots[:5]]


# ---------------------------------------------------------- entity integrity


def test_every_lot_references_a_real_product_and_its_flow(timeline):
    world = timeline.world
    assert len(timeline.lots) == BASELINE_LOTS
    for lot in timeline.lots:
        product = world.product(lot.product_id)
        assert lot.flow_id == product.flow_id
        assert lot.wafer_count == world.wafers_per_lot
        assert lot.status in LOT_STATUSES


def test_every_wafer_belongs_to_a_lot_and_holds_a_slot(timeline):
    world = timeline.world
    assert len(timeline.wafers) == BASELINE_LOTS * world.wafers_per_lot
    for lot in timeline.lots:
        wafers = timeline.wafers_of_lot(lot.lot_id)
        assert [w.slot_number for w in wafers] == list(
            range(1, world.wafers_per_lot + 1))
        assert all(w.status in WAFER_STATUSES for w in wafers)


def test_every_run_sits_on_its_wafers_own_route(timeline):
    world = timeline.world
    for run in timeline.runs:
        lot = timeline.lot(run.lot_id)
        flow_step = world.flow_step(run.flow_step_id)
        assert flow_step.flow_id == lot.flow_id
        assert flow_step.step_id == run.step_id
        assert timeline.wafer(run.wafer_id).lot_id == run.lot_id


def test_every_selected_tool_is_qualified_for_the_step(timeline):
    world = timeline.world
    for run in timeline.runs:
        operation = world.step(run.step_id).operation_type
        assert operation in world.tool(run.tool_id).operations
        eligible = {c.chamber_id
                    for c in world.eligible_chambers(run.flow_step_id)}
        assert run.chamber_id in eligible


def test_every_selected_chamber_belongs_to_the_selected_tool(timeline):
    for run in timeline.runs:
        assert timeline.world.chamber(run.chamber_id).tool_id == run.tool_id


def test_every_run_uses_the_recipe_of_its_step_and_product(timeline):
    world = timeline.world
    for run in timeline.runs:
        recipe = world.recipe(run.recipe_id)
        assert recipe.step_id == run.step_id
        assert recipe.product_id == timeline.lot(run.lot_id).product_id


def test_every_run_has_an_operator_working_that_shift(timeline):
    world = timeline.world
    for run in timeline.runs:
        operator = world.operator(run.operator_id)
        assert operator.shift == world.shift_at(run.start_min)


def test_runs_have_valid_and_plausible_intervals(timeline):
    world = timeline.world
    for run in timeline.runs:
        assert 0 <= run.start_min < run.end_min <= timeline.horizon_minutes
        step = world.step(run.step_id)
        assert run.end_min - run.start_min >= int(step.duration.minimum)


def test_ids_are_dense_and_ordered(timeline):
    assert [r.run_id for r in timeline.runs] == list(
        range(1, len(timeline.runs) + 1))
    assert [m.maint_id for m in timeline.maintenance] == list(
        range(1, len(timeline.maintenance) + 1))
    assert [s.state_id for s in timeline.states] == list(
        range(1, len(timeline.states) + 1))
    starts = [r.start_min for r in timeline.runs]
    assert starts == sorted(starts)


# -------------------------------------------------------- temporal integrity


def test_a_wafer_never_starts_a_step_before_the_previous_one_finished(timeline):
    world = timeline.world
    for wafer in timeline.wafers:
        runs = timeline.runs_of_wafer(wafer.wafer_id)
        sequences = [world.flow_step(r.flow_step_id).step_sequence
                     for r in runs]
        assert sequences == sorted(sequences)
        assert sequences == list(range(1, len(runs) + 1))
        for earlier, later in zip(runs, runs[1:]):
            assert later.start_min >= earlier.end_min


def test_lots_overlap_in_the_line(timeline):
    """The audited one-lot-at-a-time artifact must be impossible here."""
    spans = [(lot.release_min,
              lot.finish_min if lot.finish_min is not None
              else timeline.horizon_minutes)
             for lot in timeline.lots]
    concurrency = max(
        sum(1 for start, end in spans if start <= moment < end)
        for moment in range(0, timeline.horizon_minutes, 240))
    assert concurrency >= 2

    # And the interleaving is real at run level, not just at lot level.
    ordered = sorted(timeline.runs, key=lambda r: r.start_min)
    switches = sum(1 for a, b in zip(ordered, ordered[1:])
                   if a.lot_id != b.lot_id)
    assert switches > len(timeline.lots)


def test_no_chamber_processes_two_runs_at_once(timeline):
    for chamber in timeline.world.chambers:
        runs = sorted(timeline.runs_on_chamber(chamber.chamber_id),
                      key=lambda r: r.start_min)
        for earlier, later in zip(runs, runs[1:]):
            assert later.start_min >= earlier.end_min, (earlier, later)


def test_a_chamber_under_maintenance_cannot_process_production(timeline):
    """The audited violation, stated directly: zero runs inside a window."""
    assert timeline.maintenance
    for window in timeline.maintenance:
        world = timeline.world
        affected = ([window.chamber_id] if window.chamber_id is not None
                    else [c.chamber_id
                          for c in world.chambers_of(window.tool_id)])
        for chamber_id in affected:
            for run in timeline.runs_on_chamber(chamber_id):
                assert not (run.start_min < window.end_min
                            and window.start_min < run.end_min), (window, run)


def test_no_run_overlaps_any_blocking_state_of_its_chamber(timeline):
    blocks = blocking_intervals(timeline)
    for run in timeline.runs:
        for start, end in blocks[run.chamber_id]:
            assert not (run.start_min < end and start < run.end_min)


def test_a_chamber_is_never_two_things_at_once(timeline):
    """The state ribbon tiles the horizon: no gaps, no overlaps, no holes."""
    for chamber in timeline.world.chambers:
        ribbon = timeline.states_of_chamber(chamber.chamber_id)
        assert ribbon
        cursor = 0
        for interval in ribbon:
            assert interval.state in STATES
            assert interval.tool_id == chamber.tool_id
            assert interval.start_min == cursor
            assert interval.end_min > interval.start_min
            cursor = interval.end_min
        assert cursor == timeline.horizon_minutes


def test_adjacent_intervals_of_the_same_state_are_one_interval(timeline):
    for chamber in timeline.world.chambers:
        ribbon = timeline.states_of_chamber(chamber.chamber_id)
        for earlier, later in zip(ribbon, ribbon[1:]):
            assert earlier.state != later.state


def test_every_run_happens_inside_a_productive_interval(timeline):
    productive: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for interval in timeline.states:
        if interval.state == "PRODUCTIVE":
            productive[interval.chamber_id].append((interval.start_min,
                                                    interval.end_min))
    for run in timeline.runs:
        assert any(start <= run.start_min and run.end_min <= end
                   for start, end in productive[run.chamber_id]), run


def test_productive_time_is_exactly_run_time(timeline):
    """No PRODUCTIVE interval exists that no run accounts for."""
    productive = Counter()
    for interval in timeline.states:
        if interval.state == "PRODUCTIVE":
            productive[interval.chamber_id] += (interval.end_min
                                                - interval.start_min)
    processed = Counter()
    for run in timeline.runs:
        processed[run.chamber_id] += run.end_min - run.start_min
    assert productive == processed


def test_maintenance_windows_coincide_with_their_state_intervals(timeline):
    world = timeline.world
    intervals = defaultdict(set)
    for interval in timeline.states:
        if interval.state in ("PM", "DOWN"):
            intervals[interval.chamber_id].add(
                (interval.state, interval.start_min, interval.end_min))
    for window in timeline.maintenance:
        assert window.maint_type in MAINTENANCE_TYPES
        assert 0 <= window.start_min < window.end_min <= timeline.horizon_minutes
        state = "PM" if window.maint_type == "PM" else "DOWN"
        affected = ([window.chamber_id] if window.chamber_id is not None
                    else [c.chamber_id
                          for c in world.chambers_of(window.tool_id)])
        for chamber_id in affected:
            assert (state, window.start_min,
                    window.end_min) in intervals[chamber_id]


def test_maintenance_happens_everywhere_not_only_somewhere(timeline):
    """Rule D7: the pattern 'unscheduled events ⇒ the bad tool' cannot form."""
    kinds = Counter(w.maint_type for w in timeline.maintenance)
    assert kinds["PM"] > 0 and kinds["UNSCHEDULED"] > 0
    pm_tools = {w.tool_id for w in timeline.maintenance
                if w.maint_type == "PM"}
    assert pm_tools == {t.tool_id for t in timeline.world.tools}
    unscheduled = Counter(w.tool_id for w in timeline.maintenance
                          if w.maint_type == "UNSCHEDULED")
    assert len(unscheduled) >= 5
    assert max(unscheduled.values()) < sum(unscheduled.values()) / 2


def test_maintenance_action_codes_come_from_the_shared_vocabulary(timeline):
    policy = timeline.world.maintenance
    for window in timeline.maintenance:
        assert window.technician in policy.technicians
        allowed = (policy.pm_action_codes if window.maint_type == "PM"
                   else policy.unscheduled_action_codes)
        assert window.action_code in allowed


def test_lot_release_is_staggered_and_inside_the_horizon(timeline):
    releases = [lot.release_min for lot in timeline.lots]
    assert releases == sorted(releases)
    assert all(0 <= r < timeline.horizon_minutes for r in releases)
    gaps = [b - a for a, b in zip(releases, releases[1:])]
    assert len(set(gaps)) > len(gaps) // 2, "releases are suspiciously regular"
    policy = timeline.world.lot_release
    span = (policy.mean_interval_days + policy.jitter_days) * MINUTES_PER_DAY
    assert all(0 < gap <= span for gap in gaps)


def test_the_lot_mix_is_spread_over_products_and_time(timeline):
    """No product may own a contiguous block of the horizon (audit artifact)."""
    products = [lot.product_id for lot in timeline.lots]
    assert len(set(products)) >= 4
    first_half = set(products[:len(products) // 2])
    second_half = set(products[len(products) // 2:])
    assert first_half & second_half


def test_lot_status_matches_what_actually_happened(timeline):
    world = timeline.world
    route_length = len(world.flow_steps_of(timeline.lots[0].flow_id))
    for lot in timeline.lots:
        wafers = timeline.wafers_of_lot(lot.lot_id)
        done = all(w.steps_completed == route_length for w in wafers)
        assert (lot.status == "COMPLETED") is done
        if done:
            last = max(timeline.runs_of_wafer(w.wafer_id)[-1].end_min
                       for w in wafers)
            assert lot.finish_min is not None and lot.finish_min >= last
        else:
            assert lot.finish_min is None


def test_wafers_still_in_the_line_are_recorded_as_such(timeline):
    world = timeline.world
    route_length = len(world.flow_steps_of(timeline.lots[0].flow_id))
    for wafer in timeline.wafers:
        runs = timeline.runs_of_wafer(wafer.wafer_id)
        assert wafer.steps_completed == len(runs)
        if wafer.status == "COMPLETED":
            assert wafer.steps_completed == route_length
        else:
            assert wafer.steps_completed < route_length


def test_the_horizon_really_closes(world):
    """A run that cannot finish inside the window is not recorded at all."""
    truncated = simulate(world, lots=6, horizon_days=3, seed=3)
    assert all(run.end_min <= truncated.horizon_minutes
               for run in truncated.runs)
    assert any(w.status == "IN_PROCESS" for w in truncated.wafers)
    assert all(lot.status == "IN_PROGRESS" for lot in truncated.lots)


# ----------------------------------------------------------------- the log


def test_the_event_log_is_ordered_and_complete(timeline):
    events = timeline.events
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert [e.minute for e in events] == sorted(e.minute for e in events)
    assert {e.kind for e in events} <= set(EVENT_KINDS)
    kinds = Counter(e.kind for e in events)
    assert kinds["RUN_START"] == kinds["RUN_END"] == len(timeline.runs)
    assert kinds["MAINT_START"] == kinds["MAINT_END"] == len(
        timeline.maintenance)
    assert kinds["LOT_RELEASE"] == len(timeline.lots)
    assert kinds["LOT_FINISH"] == sum(1 for lot in timeline.lots
                                      if lot.finish_min is not None)


def test_endings_are_logged_before_beginnings_at_the_same_instant(timeline):
    ranks = {kind: rank for rank, kind in enumerate(EVENT_KINDS)}
    for earlier, later in zip(timeline.events, timeline.events[1:]):
        if earlier.minute == later.minute:
            assert ranks[earlier.kind] <= ranks[later.kind]


def test_event_times_agree_with_the_rows_they_point_at(timeline):
    for event in timeline.events:
        if event.kind == "RUN_START":
            assert timeline.run(event.ref_id).start_min == event.minute
        elif event.kind == "RUN_END":
            assert timeline.run(event.ref_id).end_min == event.minute
        elif event.kind == "LOT_RELEASE":
            assert timeline.lot(event.ref_id).release_min == event.minute


# --------------------------------------------------------- routing integrity


def test_every_qualified_chamber_carries_production(timeline):
    """A8: no chamber may sit unused because of the order it was declared in."""
    counts = Counter(run.chamber_id for run in timeline.runs)
    assert len(counts) == len(timeline.world.chambers)
    assert min(counts.values()) > 0


def test_etch_load_is_shared_across_tools_and_chambers(timeline):
    world = timeline.world
    etch_chambers = {c.chamber_id for tool in world.tools_for_operation("ETCH")
                     for c in world.chambers_of(tool.tool_id)}
    counts = Counter(run.chamber_id for run in timeline.runs
                     if run.chamber_id in etch_chambers)
    assert set(counts) == etch_chambers
    # No chamber may take more than half the etch work: exposure must overlap.
    assert max(counts.values()) < sum(counts.values()) / 2


def test_gate_and_metal_etch_assignments_are_independent(timeline):
    """The audited 100% collinearity must be structurally impossible."""
    world = timeline.world
    gate_step = world.step_by_name("GATE_ETCH").step_id
    metal_step = world.step_by_name("METAL_ETCH").step_id
    gate = {r.wafer_id: r.chamber_id for r in timeline.runs
            if r.step_id == gate_step}
    metal = {r.wafer_id: r.chamber_id for r in timeline.runs
             if r.step_id == metal_step}
    pairs = [(gate[w], metal[w]) for w in sorted(gate) if w in metal]
    assert len(pairs) > 100
    same = sum(1 for a, b in pairs if a == b)
    assert same / len(pairs) < 0.5
    # Every gate chamber must be seen with more than one metal chamber, or the
    # one implies the other and product identity leaks into chamber identity.
    partners = defaultdict(set)
    for gate_chamber, metal_chamber in pairs:
        partners[gate_chamber].add(metal_chamber)
    assert all(len(seen) > 1 for seen in partners.values())


def test_routing_fills_chambers_as_they_free_up(timeline):
    """Earliest-available dispatch, visible in the timestamps it produces.

    When a lot's 25 wafers arrive at a step together the pool is momentarily
    saturated, and a scheduler that takes the soonest-free chamber leaves a
    signature: runs that start in the very minute the previous run ended. A
    router that ignored availability would rarely pack like this.
    """
    back_to_back = consecutive = 0
    for chamber in timeline.world.chambers:
        runs = sorted(timeline.runs_on_chamber(chamber.chamber_id),
                      key=lambda r: r.start_min)
        for earlier, later in zip(runs, runs[1:]):
            consecutive += 1
            back_to_back += later.start_min == earlier.end_min
    assert consecutive
    assert back_to_back / consecutive > 0.10


def test_production_continues_elsewhere_while_a_chamber_is_blocked(timeline):
    """Downtime costs *that chamber* exposure; the pool absorbs the work.

    This fab is lightly loaded, so plenty of maintenance falls in genuinely
    idle time — the claim is not that every window diverts work, but that
    blocking a chamber does not stall its pool.
    """
    world = timeline.world
    considered = diverted = 0
    for window in timeline.maintenance:
        operation = world.tool(window.tool_id).operations[0]
        pool = {chamber.chamber_id
                for tool in world.tools_for_operation(operation)
                for chamber in world.chambers_of(tool.tool_id)}
        blocked = ({window.chamber_id} if window.chamber_id is not None
                   else {c.chamber_id
                         for c in world.chambers_of(window.tool_id)})
        alternatives = pool - blocked
        if not alternatives:
            continue
        considered += 1
        diverted += any(
            run.start_min < window.end_min and window.start_min < run.end_min
            for chamber_id in sorted(alternatives)
            for run in timeline.runs_on_chamber(chamber_id))
    assert considered
    assert diverted / considered > 0.25


def test_full_stickiness_keeps_a_lot_on_one_chamber_per_step(make_world):
    """`stickiness: 1.0` is the degenerate end of the configured behaviour."""
    world = make_world(routing={"stickiness": 1.0})
    timeline = small(world)
    used = defaultdict(set)
    for run in timeline.runs:
        used[(run.lot_id, run.flow_step_id)].add(run.chamber_id)
    assert used
    assert all(len(chambers) == 1 for chambers in used.values())


def test_zero_stickiness_spreads_a_lot_across_the_pool(make_world):
    world = make_world(routing={"stickiness": 0.0})
    timeline = small(world)
    etch_steps = {fs.flow_step_id for fs in world.flow_steps
                  if world.step(fs.step_id).operation_type == "ETCH"}
    used = defaultdict(set)
    for run in timeline.runs:
        if run.flow_step_id in etch_steps:
            used[(run.lot_id, run.flow_step_id)].add(run.chamber_id)
    assert used
    assert max(len(chambers) for chambers in used.values()) > 1


def test_stickiness_clusters_lots_onto_chambers(world, make_world):
    """The configured stickiness must show up as clustering, not as noise.

    Measured against the same world with stickiness switched off, so the
    comparison holds whatever the pool size and the load happen to be.
    """
    sticky = _mean_chamber_reuse(small(world))
    spread = _mean_chamber_reuse(small(make_world(routing={"stickiness": 0.0})))
    assert sticky > spread
    assert spread < 0.5, "without stickiness a lot should not cluster"


def _mean_chamber_reuse(timeline: Timeline) -> float:
    """Mean share of a lot's wafers that took the same chamber at a step."""
    world = timeline.world
    etch_steps = {fs.flow_step_id for fs in world.flow_steps
                  if world.step(fs.step_id).operation_type == "ETCH"}
    grouped = defaultdict(list)
    for run in timeline.runs:
        if run.flow_step_id in etch_steps:
            grouped[(run.lot_id, run.flow_step_id)].append(run)
    shares = [max(Counter(r.chamber_id for r in runs).values()) / len(runs)
              for runs in grouped.values()]
    return sum(shares) / len(shares)


# ------------------------------------------------------- routing conditions
#
# ADR-015: a dedication is a *share*, not a filter. These tests are about what
# that does to traffic; the contract shape lives in `test_scenario.py` and the
# resolution against a world in `test_routing.py`. They are statistical by
# necessity — a share is a distributional statement — so they measure a
# realization big enough to mean something and compare it against the *same*
# world without the dedication, rather than against a number typed in here.

DEDICATED_TOOL = "ETCH-01"
DEDICATED_PRODUCT = "Mobile-28"
DEDICATION_WINDOW = (20.0, 60.0)
DEDICATION_SHARE = 0.85


def dedication_condition(**overrides: Any) -> dict[str, Any]:
    raw = {
        "kind": "product_dedication",
        "product": DEDICATED_PRODUCT,
        "tool": DEDICATED_TOOL,
        "operation_type": "ETCH",
        "start_day": DEDICATION_WINDOW[0],
        "end_day": DEDICATION_WINDOW[1],
        "share": DEDICATION_SHARE,
    }
    raw.update(overrides)
    return raw


def etch_runs_in_window(timeline: Timeline, product_name: str,
                        window: tuple[float, float]) -> list[Any]:
    """One product's etch runs whose start falls inside a day window."""
    world = timeline.world
    product_id = world.product_by_name(product_name).product_id
    etch_steps = {fs.step_id for fs in world.flow_steps
                  if world.step(fs.step_id).operation_type == "ETCH"}
    start, end = window
    return [run for run in timeline.runs
            if run.step_id in etch_steps
            and timeline.lot(run.lot_id).product_id == product_id
            and start <= run.start_min / MINUTES_PER_DAY < end]


def dedicated_share(timeline: Timeline, tool_name: str, product_name: str,
                    window: tuple[float, float]) -> float:
    runs = etch_runs_in_window(timeline, product_name, window)
    assert runs, "the fixture must produce traffic inside the window"
    tool_id = timeline.world.tool_by_name(tool_name).tool_id
    return sum(run.tool_id == tool_id for run in runs) / len(runs)


def dedicated_timeline(world: World, **overrides: Any) -> Timeline:
    config = from_mapping({**SCENARIO,
                           "routing_conditions": [
                               dedication_condition(**overrides)]})
    return simulate_scenario(config, world=world)


def test_a_dedication_window_moves_routing_shares_observably(world):
    """Scenario G's confounder is routing policy, and it is visible in data."""
    dedicated = dedicated_timeline(world)
    plain = simulate_scenario(from_mapping(SCENARIO), world=world)

    inside = dedicated_share(dedicated, DEDICATED_TOOL, DEDICATED_PRODUCT,
                             DEDICATION_WINDOW)
    before = dedicated_share(dedicated, DEDICATED_TOOL, DEDICATED_PRODUCT,
                             (0.0, DEDICATION_WINDOW[0]))
    undedicated = dedicated_share(plain, DEDICATED_TOOL, DEDICATED_PRODUCT,
                                  DEDICATION_WINDOW)

    assert inside > undedicated
    assert inside > before
    # The window is a window: outside it the same product routes as it always
    # did, so the shift is bounded in time and an investigator can see that.
    assert before < 0.7


def test_a_dedication_is_a_share_and_not_an_exclusive_assignment(world):
    """`share: 0.85` must not become "all of it" — the Step 2 behaviour."""
    timeline = dedicated_timeline(world)
    runs = etch_runs_in_window(timeline, DEDICATED_PRODUCT, DEDICATION_WINDOW)
    tool_id = world.tool_by_name(DEDICATED_TOOL).tool_id
    elsewhere = [run for run in runs if run.tool_id != tool_id]

    assert elsewhere, "the dedicated product must still reach other etch tools"
    share = dedicated_share(timeline, DEDICATED_TOOL, DEDICATED_PRODUCT,
                            DEDICATION_WINDOW)
    # Realized share sits a little above the configured one, because the
    # traffic the draw releases can still land on the dedicated tool. What is
    # under test is the *shape*: a preference, bounded away from 1.
    assert 0.6 < share < 1.0


def test_a_dedication_does_not_change_who_is_eligible(world):
    """Exposure probability moves; qualification and eligibility do not."""
    timeline = dedicated_timeline(world)
    etch_tools = {t.tool_id for t in world.tools_for_operation("ETCH")}
    used = {run.tool_id for run in timeline.runs if run.tool_id in etch_tools}
    assert used == etch_tools

    etch_chambers = {c.chamber_id for t in world.tools_for_operation("ETCH")
                     for c in world.chambers_of(t.tool_id)}
    assert {run.chamber_id for run in timeline.runs
            if run.chamber_id in etch_chambers} == etch_chambers


def test_a_dedication_cannot_aim_at_a_chamber(world):
    """Tool-level by construction: within the dedicated tool, chambers are
    still chosen by availability, so the confounder cannot point at the grain
    a fault is attributed at."""
    timeline = dedicated_timeline(world)
    tool = world.tool_by_name(DEDICATED_TOOL)
    inside = etch_runs_in_window(timeline, DEDICATED_PRODUCT,
                                 DEDICATION_WINDOW)
    used = {run.chamber_id for run in inside if run.tool_id == tool.tool_id}
    assert used == set(tool.chamber_ids)

    with pytest.raises(ScenarioConfigError):
        from_mapping({**SCENARIO, "routing_conditions": [
            dedication_condition(chamber="A")]})


def test_other_products_keep_using_the_dedicated_tool(world):
    """A dedication is a shift in shares, not a partition of the fab."""
    timeline = dedicated_timeline(world)
    tool_id = world.tool_by_name(DEDICATED_TOOL).tool_id
    product_id = world.product_by_name(DEDICATED_PRODUCT).product_id
    others = [run for run in timeline.runs
              if run.tool_id == tool_id
              and timeline.lot(run.lot_id).product_id != product_id
              and DEDICATION_WINDOW[0] <= run.start_min / MINUTES_PER_DAY
              < DEDICATION_WINDOW[1]]
    assert others


def test_a_stronger_share_moves_more_traffic(world):
    """The knob is a knob: the realized share is monotone in the configured
    one, which is what makes it a difficulty setting rather than a switch."""
    shares = [
        dedicated_share(dedicated_timeline(world, share=share),
                        DEDICATED_TOOL, DEDICATED_PRODUCT, DEDICATION_WINDOW)
        for share in (0.3, 0.85)
    ]
    assert shares[0] < shares[1]


def test_a_dedicated_realization_is_deterministic(world):
    config = from_mapping({**SCENARIO,
                           "routing_conditions": [dedication_condition()]})
    first = simulate_scenario(config, world=world)
    second = simulate_scenario(config, world=world)
    assert first.content_sha256() == second.content_sha256()
    assert_structural_invariants(first)


def test_a_world_without_dedications_routes_exactly_as_before(world):
    """The dedication draw is taken only where a dedication is in force, so
    adding the rule cannot reshuffle a world that declares none."""
    plain = simulate_scenario(from_mapping(SCENARIO), world=world)
    assert plain.content_sha256() == baseline(world).content_sha256()


def test_dedication_leaves_unrelated_seeds_alone(world):
    """Different seeds are different realizations; both obey the invariants."""
    config = from_mapping({**SCENARIO,
                           "routing_conditions": [dedication_condition()]})
    first = simulate_scenario(config, world=world)
    second = simulate_scenario(config, 43, world=world)
    assert first.content_sha256() != second.content_sha256()
    for timeline in (first, second):
        assert_structural_invariants(timeline)
        assert 0.6 < dedicated_share(timeline, DEDICATED_TOOL,
                                     DEDICATED_PRODUCT,
                                     DEDICATION_WINDOW) < 1.0


def test_a_standing_world_dedication_behaves_the_same_way(make_world):
    """The world keeps its routing machinery; the layers share semantics."""
    dedication = {"product": DEDICATED_PRODUCT, "tool": DEDICATED_TOOL,
                  "operation_type": "ETCH",
                  "start_day": DEDICATION_WINDOW[0],
                  "end_day": DEDICATION_WINDOW[1],
                  "share": DEDICATION_SHARE}
    world = make_world(routing={"stickiness": 0.6,
                                "dedications": [dedication]})
    timeline = baseline(world)
    share = dedicated_share(timeline, DEDICATED_TOOL, DEDICATED_PRODUCT,
                            DEDICATION_WINDOW)
    assert 0.6 < share < 1.0


def test_a_scenario_condition_layers_over_the_standing_policy(make_world):
    """Where both cover a decision, the experiment's condition is what runs."""
    standing = {"product": DEDICATED_PRODUCT, "tool": "ETCH-03",
                "operation_type": "ETCH", "start_day": 0.0, "end_day": 84.0,
                "share": 0.9}
    world = make_world(routing={"stickiness": 0.6,
                                "dedications": [standing]})
    timeline = dedicated_timeline(world)
    assert (dedicated_share(timeline, DEDICATED_TOOL, DEDICATED_PRODUCT,
                            DEDICATION_WINDOW)
            > dedicated_share(timeline, "ETCH-03", DEDICATED_PRODUCT,
                              DEDICATION_WINDOW))


def test_routing_cannot_see_the_answer(world):
    """Two scenarios differing only in their faults schedule identically."""
    null = from_mapping(SCENARIO)
    faulted = from_mapping({
        **SCENARIO,
        "name": "edge-uniformity",
        "description": "one chamber develops edge non-uniformity",
        "events": [{
            "mechanism": "chamber_edge_uniformity",
            "target": {"tool": "ETCH-02", "chamber": "B"},
            "onset_day": 35,
            "profile": {"type": "ramp", "ramp_days": 7},
            "severity": "obvious",
            "response": {"alarm": True, "repair_delay_days_mean": 4.0,
                         "recovery": "partial"},
        }],
        "distractors": [{"mechanism": "benign_offset",
                         "target": {"tool": "CVD-01"},
                         "magnitude": "large"}],
    })
    assert null.config_sha256 != faulted.config_sha256
    assert (simulate_scenario(null, world=world).content_sha256()
            == simulate_scenario(faulted, world=world).content_sha256())


# ------------------------------------------------------------- the front door


def test_simulate_scenario_reads_the_configuration(world):
    config = from_mapping({**SCENARIO, "lots": 5, "horizon_days": 40})
    timeline = simulate_scenario(config, world=world)
    assert len(timeline.lots) == 5
    assert timeline.horizon_days == 40
    assert timeline.seed == SCENARIO["default_seed"]


def test_simulate_scenario_resolves_the_world_by_name():
    timeline = simulate_scenario(from_mapping({**SCENARIO, "lots": 3,
                                               "horizon_days": 20}))
    assert timeline.world.template_name == BASELINE_WORLD
    assert timeline.runs


def test_an_explicit_seed_overrides_the_configured_default(world):
    config = from_mapping({**SCENARIO, "lots": 4, "horizon_days": 30})
    first = simulate_scenario(config, world=world)
    second = simulate_scenario(config, 43, world=world)
    assert second.seed == 43
    assert first.content_sha256() != second.content_sha256()


@pytest.mark.parametrize("kwargs", [
    {"lots": 0}, {"lots": -1}, {"lots": 2.0}, {"lots": True},
    {"horizon_days": 0}, {"horizon_days": 1.5},
])
def test_simulate_rejects_impossible_requests(world, kwargs):
    with pytest.raises(ValueError):
        baseline(world, **kwargs)


def test_simulate_rejects_a_seed_outside_the_contract(world):
    with pytest.raises((TypeError, ValueError)):
        baseline(world, seed=-1)


def test_a_minimal_world_still_produces_a_coherent_timeline(world):
    tiny = simulate(world, lots=1, horizon_days=15, seed=1)
    assert len(tiny.lots) == 1
    assert tiny.runs
    assert_structural_invariants(tiny)
