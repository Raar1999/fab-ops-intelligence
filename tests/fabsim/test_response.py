"""
Invariant tests for `fabsim.response` — the fab's reaction to a condition.

This layer is the easiest place in the simulator to accidentally build a fault
detector, so most of what is pinned here is *symmetry*: a healthy chamber
alarms, a healthy chamber gets repaired, a background breakdown recovers latent
state exactly as a requested repair does, and no observable record of any of it
carries a reason. What a fault buys is a higher *rate* along a path every
chamber can walk — never a path only it can walk.

The severity checks are relative and monotone rather than absolute. The
question a threshold model can honestly answer at this gate is "does a bigger
departure get noticed more often?", and it is answered against the null world,
never against a benchmark or a yield number that does not exist yet.
"""
from __future__ import annotations

import ast
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest

from fabsim.response import (
    RESPONSE_MODEL,
    Alarm,
    FabResponse,
    respond_scenario,
)
from fabsim.rng import stream
from fabsim.scenario import from_mapping
from fabsim.timeline import MaintenanceWindow
from fabsim.world import MINUTES_PER_DAY

BASELINE_WORLD = "baseline_fab_v1"

SCENARIO: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "null",
    "world": BASELINE_WORLD,
    "horizon_days": 84,
    "lots": 20,
    "default_seed": 42,
}

EDGE_EVENT: dict[str, Any] = {
    "mechanism": "chamber_edge_uniformity",
    "target": {"tool": "ETCH-02", "chamber": "B"},
    "onset_day": 35,
    "profile": {"type": "ramp", "ramp_days": 7},
    "severity": "moderate",
}

PARTICLE_EVENT: dict[str, Any] = {
    "mechanism": "particle_excursion",
    "target": {"tool": "CVD-01", "chamber": "A"},
    "onset_day": 40,
    "profile": {"type": "step"},
    "severity": "obvious",
}

_CACHE: dict[str, FabResponse] = {}


def responded(world, **overrides: Any) -> FabResponse:
    """One scenario's fab response, memoized: these are not cheap."""
    config = from_mapping({**SCENARIO, **overrides})
    key = config.canonical_json
    if key not in _CACHE:
        _CACHE[key] = respond_scenario(config, world=world)
    return _CACHE[key]


def target_chamber(world, event: dict[str, Any]) -> int:
    return world.chamber_by_name(event["target"]["tool"],
                                 event["target"]["chamber"]).chamber_id


@pytest.fixture(scope="module")
def null(world) -> FabResponse:
    return responded(world)


@pytest.fixture(scope="module")
def particle(world) -> FabResponse:
    return responded(world, events=[PARTICLE_EVENT])


# ------------------------------------------------------------------- alarms


def test_a_null_world_raises_alarms(null):
    """The response layer is not a fault detector, and this is the proof.

    A world with nothing wrong in it produces alarms on most of its chambers.
    If it did not, the presence of an alarm would *be* the answer.
    """
    assert null.realization.mechanisms == ()
    assert null.alarms
    assert len({a.chamber_id for a in null.alarms}) >= 8
    assert len({a.code for a in null.alarms}) >= 4


def test_a_null_world_raises_both_kinds_of_alarm(null):
    kinds = Counter(null.detail(a.alarm_id).kind for a in null.alarms)
    assert kinds["background"] > 0
    assert kinds["condition"] > 0


def test_background_alarms_need_no_condition_at_all(null, world):
    """`CAUSAL_MECHANISM_MODEL.md` §7: a false-alarm rate on every chamber.

    Drawn from a hazard, not from the latent plane — which is why they appear
    on chambers whose latents never departed anything.
    """
    assert world.alarms.background_rate_per_chamber_day > 0.0
    background = [a for a in null.alarms
                  if null.detail(a.alarm_id).kind == "background"]
    assert background
    assert all(null.detail(a.alarm_id).deviation_sigma == 0.0
               for a in background)
    assert len({a.chamber_id for a in background}) >= 8


def test_the_same_code_arises_on_many_chambers(null):
    """No code belongs to one chamber, so no code can point at one."""
    by_code: dict[str, set[int]] = defaultdict(set)
    for alarm in null.alarms:
        by_code[alarm.code].add(alarm.chamber_id)
    shared = [code for code, chambers in by_code.items()
              if len(chambers) > 1]
    assert len(shared) >= 3


def test_one_code_arises_from_more_than_one_underlying_condition(world):
    """A code is not a fingerprint of a mechanism: the same code appears both
    as a false alarm and as a real crossing, on different chambers."""
    faulted = responded(world, events=[PARTICLE_EVENT])
    kinds_by_code: dict[str, set[str]] = defaultdict(set)
    for alarm in faulted.alarms:
        kinds_by_code[alarm.code].add(faulted.detail(alarm.alarm_id).kind)
    assert any(len(kinds) > 1 for kinds in kinds_by_code.values())


def test_alarms_are_not_a_copy_of_hidden_mechanism_state(world, particle):
    """Chambers alarm that have no mechanism; the affected chamber does not
    alarm on every sample. Neither direction of the map is total."""
    affected = set(particle.realization.mechanisms[0].chamber_ids)
    alarmed = {a.chamber_id for a in particle.alarms}
    assert alarmed - affected                # alarms without a mechanism
    onset = PARTICLE_EVENT["onset_day"] * MINUTES_PER_DAY
    on_target = [a for a in particle.alarms
                 if a.chamber_id in affected and a.minute >= onset]
    assert 0 < len(on_target) < 200          # noticed, not narrated


def test_an_alarm_carries_no_reason(null):
    """Observable shape (`SCHEMA_V2_DESIGN.md` §2.17) and nothing more.

    A field for the cause is a field a later emitter could fill from the
    hidden plane without noticing it had crossed a boundary. There is none.
    """
    assert set(Alarm.__dataclass_fields__) == {
        "alarm_id", "tool_id", "chamber_id", "minute", "code", "severity",
        "message"}
    forbidden = {"mechanism", "latent", "cause", "kind", "deviation_sigma",
                 "signal", "event_index", "fault"}
    assert not (set(Alarm.__dataclass_fields__) & forbidden)
    for alarm in null.alarms[:20]:
        assert alarm.message == null.world.alarms.code_by_name(
            alarm.code).message


def test_alarm_codes_and_severities_come_from_the_world(null):
    codes = {c.code: c for c in null.world.alarms.codes}
    for alarm in null.alarms:
        assert alarm.code in codes
        assert alarm.severity == codes[alarm.code].severity


def test_a_code_only_fires_on_a_tool_qualified_for_it(null, world):
    for alarm in null.alarms:
        rule = world.alarms.code_by_name(alarm.code)
        tool = world.tool(alarm.tool_id)
        assert set(rule.operation_types) & set(tool.operations)


def test_alarm_ids_are_dense_and_ordered_by_the_clock(null):
    assert [a.alarm_id for a in null.alarms] == list(
        range(1, len(null.alarms) + 1))
    minutes = [a.minute for a in null.alarms]
    assert minutes == sorted(minutes)


def test_alarms_lie_inside_the_horizon(null):
    assert all(0 <= a.minute < null.timeline.horizon_minutes
               for a in null.alarms)


def test_detection_is_probabilistic_rather_than_automatic(world):
    """A crossing is a chance to be noticed, not a notification.

    With a refractory of a day and an obvious excursion running for weeks,
    a deterministic detector would alarm every single day of it.
    """
    assert world.alarms.detection_probability < 1.0
    faulted = responded(world, events=[PARTICLE_EVENT])
    chamber = target_chamber(world, PARTICLE_EVENT)
    onset = PARTICLE_EVENT["onset_day"] * MINUTES_PER_DAY
    days = (faulted.timeline.horizon_minutes - onset) / MINUTES_PER_DAY
    on_target = [a for a in faulted.alarms
                 if a.chamber_id == chamber and a.minute >= onset]
    assert 0 < len(on_target) < days


# ------------------------------------------------------------ alarm timing


def test_no_alarm_precedes_the_condition_that_caused_it(world):
    """Causal order: a condition-driven alarm cannot predate its onset.

    Background alarms are exempt — they have no condition to follow.
    """
    faulted = responded(world, events=[PARTICLE_EVENT])
    chamber = target_chamber(world, PARTICLE_EVENT)
    onset = PARTICLE_EVENT["onset_day"] * MINUTES_PER_DAY
    trajectory = faulted.realization.trajectory(chamber, "particle_load")
    departure = trajectory.departure()
    grid = faulted.realization.grid

    for alarm in faulted.alarms:
        if alarm.chamber_id != chamber:
            continue
        if faulted.detail(alarm.alarm_id).kind != "condition":
            continue
        if alarm.minute < onset:
            # Pre-onset condition alarms are natural wander, and the fault has
            # provably contributed nothing to them yet.
            assert departure[grid.index_at(alarm.minute)] == 0.0


def test_a_repair_follows_the_alarms_that_escalated_into_it(world, particle):
    """The chain is causal, and the lag is real (`TEMPORAL_MODEL.md` §2.3)."""
    assert particle.repairs
    for repair in particle.repairs:
        assert repair.alarm_ids
        for alarm_id in repair.alarm_ids:
            alarm = particle.alarms[alarm_id - 1]
            assert alarm.alarm_id == alarm_id
            assert alarm.chamber_id == repair.chamber_id
            assert alarm.minute <= repair.raised_minute
        assert repair.start_min >= repair.raised_minute
        assert repair.end_min > repair.start_min
        assert repair.delay_minutes > 0


def test_the_repair_delay_is_drawn_and_not_constant(world, particle):
    delays = [r.delay_minutes for r in particle.repairs]
    assert len(delays) > 3
    assert len(set(delays)) > 1
    mean_days = st.mean(delays) / MINUTES_PER_DAY
    assert 0.2 < mean_days < 20.0     # exponential about the configured mean


def test_escalation_needs_more_than_one_complaint(world, null):
    """One alarm is not a work order (`ResponsePolicy.escalation_count`)."""
    assert world.response.escalation_count > 1
    for repair in null.repairs:
        assert len(repair.alarm_ids) >= world.response.escalation_count


# ------------------------------------------------------------- maintenance


def test_a_healthy_fab_still_gets_unscheduled_maintenance(null):
    """Background breakdowns and escalated false alarms, on a null world.

    This is the load-bearing symmetry: if maintenance only ever appeared where
    something was wrong, "this chamber was repaired" would be the answer.
    """
    assert null.realization.mechanisms == ()
    unscheduled = [m for m in null.maintenance
                   if m.maint_type == "UNSCHEDULED"]
    assert unscheduled
    assert len({m.chamber_id for m in unscheduled}) >= 5
    assert null.repairs                      # escalated, on healthy chambers


def test_a_requested_repair_is_indistinguishable_from_a_breakdown(null):
    """Same type, same technicians, same action codes — anti-leakage D2.

    Nothing on the observable row says which kind of unscheduled work it was,
    because in a real fab nothing does.
    """
    requested = {r.maint_id for r in null.repairs}
    assert requested
    windows = {m.maint_id: m for m in null.maintenance}
    breakdowns = [m for m in null.maintenance
                  if m.maint_type == "UNSCHEDULED"
                  and m.maint_id not in requested]
    assert breakdowns

    for maint_id in requested:
        window = windows[maint_id]
        assert window.maint_type == "UNSCHEDULED"
        assert window.technician in null.world.maintenance.technicians
        assert window.action_code in (
            null.world.maintenance.unscheduled_action_codes)
    assert ({windows[i].action_code for i in requested}
            & {m.action_code for m in breakdowns})


def test_maintenance_carries_no_cause(null):
    """The window is observable; why it exists is not one of its fields."""
    assert set(MaintenanceWindow.__dataclass_fields__) == {
        "maint_id", "tool_id", "chamber_id", "maint_type", "start_min",
        "end_min", "technician", "action_code"}
    forbidden = {"mechanism", "cause", "order_id", "alarm_ids", "recovery",
                 "fault", "requested"}
    assert not (set(MaintenanceWindow.__dataclass_fields__) & forbidden)


def test_a_repair_blocks_production_like_any_other_window(particle):
    """Step 2's invariant, over a calendar the response layer added to."""
    timeline = particle.timeline
    blocking = defaultdict(list)
    for interval in timeline.states:
        if interval.state in ("DOWN", "PM", "QUAL"):
            blocking[interval.chamber_id].append(
                (interval.start_min, interval.end_min))
    for run in timeline.runs:
        for start, end in blocking[run.chamber_id]:
            assert not (run.start_min < end and start < run.end_min)

    requested = {r.maint_id for r in particle.repairs}
    assert requested
    for repair in particle.repairs:
        for run in timeline.runs_on_chamber(repair.chamber_id):
            assert not (run.start_min < repair.end_min
                        and repair.start_min < run.end_min)


def test_the_state_ribbon_still_tiles_the_horizon(particle):
    timeline = particle.timeline
    for chamber in particle.world.chambers:
        cursor = 0
        for interval in timeline.states_of_chamber(chamber.chamber_id):
            assert interval.start_min == cursor
            assert interval.end_min > interval.start_min
            cursor = interval.end_min
        assert cursor == timeline.horizon_minutes


def test_every_repair_is_in_the_calendar_and_the_timeline(particle):
    ids = {m.maint_id for m in particle.timeline.maintenance}
    assert [m.maint_id for m in particle.maintenance] == list(
        range(1, len(particle.maintenance) + 1))
    for repair in particle.repairs:
        assert repair.maint_id in ids
        window = particle.timeline.maintenance[repair.maint_id - 1]
        assert window.maint_id == repair.maint_id
        assert (window.start_min, window.end_min) == (repair.start_min,
                                                      repair.end_min)


def test_repairs_never_overlap_the_calendar_they_joined(particle):
    per_chamber = defaultdict(list)
    for window in particle.maintenance:
        chambers = ([window.chamber_id] if window.chamber_id is not None
                    else [c.chamber_id for c
                          in particle.world.chambers_of(window.tool_id)])
        for chamber_id in chambers:
            per_chamber[chamber_id].append((window.start_min, window.end_min))
    for intervals in per_chamber.values():
        intervals.sort()
        for (_a, end), (start, _b) in zip(intervals, intervals[1:]):
            assert start >= end


def test_a_qual_tail_still_follows_every_window(particle):
    """QUAL blocks production after a repair exactly as after a breakdown."""
    timeline = particle.timeline
    qual = {(i.chamber_id, i.start_min)
            for i in timeline.states if i.state == "QUAL"}
    assert qual
    covered = 0
    for repair in particle.repairs:
        if (repair.chamber_id, repair.end_min) in qual:
            covered += 1
    assert covered == len(particle.repairs)


# --------------------------------------------------------- recovery symmetry


def _unscheduled_recoveries(response: FabResponse):
    by_event = defaultdict(list)
    for reset in response.realization.resets:
        if reset.kind == "UNSCHEDULED":
            by_event[(reset.chamber_id, reset.minute)].append(reset)
    return by_event


def test_a_background_breakdown_recovers_latent_state(null):
    """The Step 3A review's carried-forward condition, in one test.

    A *null* world, so every one of these is a background breakdown or an
    escalated false alarm — and every one of them moved latent state.
    """
    assert null.realization.mechanisms == ()
    events = _unscheduled_recoveries(null)
    assert events
    moved = [resets for resets in events.values()
             if any(r.fraction > 0.0 for r in resets)]
    assert len(moved) > len(events) / 2
    for resets in moved:
        assert any(abs(r.after - r.before) > 0.0 for r in resets)


def test_requested_and_background_repairs_share_one_recovery_machine(
        world, particle):
    """Same distribution, same code path, drawn the same way.

    If a requested repair recovered differently from a breakdown, "behaviour
    changed after a repair" would separate the two perfectly.
    """
    requested = {(r.chamber_id, r.end_min) for r in particle.repairs}
    assert requested
    events = _unscheduled_recoveries(particle)
    from_request = [resets for key, resets in events.items()
                    if key in requested]
    from_breakdown = [resets for key, resets in events.items()
                      if key not in requested]
    assert from_request and from_breakdown

    for group in (from_request, from_breakdown):
        for resets in group:
            for reset in resets:
                efficacy = world.latent(reset.latent).repair_efficacy
                assert reset.fraction == pytest.approx(
                    reset.quality * efficacy)
                assert 0.0 <= reset.quality <= 1.0


def test_recovery_is_imperfect_and_sometimes_absent(null, world):
    events = _unscheduled_recoveries(null)
    qualities = [resets[0].quality for resets in events.values()]
    assert any(q == 0.0 for q in qualities)          # the no-fix draw
    assert all(q < 1.0 for q in qualities)           # never a perfect fix
    assert 0.5 < st.mean([q for q in qualities if q > 0.0]) < 1.0


def test_a_repair_leaves_a_residual_rather_than_erasing_a_fault(world):
    """Scenario I's whole point: the arc is onset → repair → *most* of it goes.

    A repair that restored a chamber exactly would make "did the intervention
    work?" a formality instead of a question.
    """
    faulted = responded(world, events=[PARTICLE_EVENT])
    chamber = target_chamber(world, PARTICLE_EVENT)
    repairs = [r for r in faulted.repairs if r.chamber_id == chamber]
    assert repairs
    trajectory = faulted.realization.trajectory(chamber, "particle_load")
    grid = faulted.realization.grid
    for repair in repairs:
        index = grid.index_at(repair.end_min)
        if index >= grid.points - 2:
            continue
        assert trajectory.values[index] < trajectory.values[index - 2]


def test_maintenance_does_not_erase_a_benign_offset(null, world):
    """A chamber recovers towards what it always was, not towards zero."""
    for reset in null.realization.resets:
        if reset.kind != "UNSCHEDULED" or reset.fraction <= 0.0:
            continue
        offset = null.realization.offset(reset.chamber_id, reset.latent)
        if world.latent(reset.latent).family != "accumulation":
            continue
        assert reset.after >= offset.total - 1e-9


# ------------------------------------------------------------- determinism


def test_the_same_inputs_produce_the_same_response(world):
    config = from_mapping({**SCENARIO, "events": [EDGE_EVENT]})
    first = respond_scenario(config, world=world)
    second = respond_scenario(config, world=world)
    assert first.content_sha256() == second.content_sha256()
    assert [a.alarm_id for a in first.alarms] == [a.alarm_id
                                                  for a in second.alarms]
    assert first.timeline.content_sha256() == second.timeline.content_sha256()


def test_a_different_seed_is_a_different_response_of_the_same_shape(world):
    baseline = responded(world, events=[EDGE_EVENT])
    other = responded(world, events=[EDGE_EVENT], default_seed=43)
    assert baseline.content_sha256() != other.content_sha256()
    assert other.alarms and other.maintenance
    assert other.model == baseline.model == RESPONSE_MODEL
    assert {m.maint_type for m in other.maintenance} == {
        m.maint_type for m in baseline.maintenance}


def test_drawing_an_unrelated_substream_changes_nothing(world):
    config = from_mapping({**SCENARIO, "events": [EDGE_EVENT]})
    before = respond_scenario(config, world=world).content_sha256()
    for index in range(200):
        stream(config.default_seed, "some.future.subsystem", index).random()
    assert respond_scenario(config, world=world).content_sha256() == before


def test_a_condition_on_one_chamber_leaves_the_others_alone(world, null):
    """Locality: response state is per chamber, and so are its streams."""
    faulted = responded(world, events=[PARTICLE_EVENT])
    affected = set(faulted.realization.mechanisms[0].chamber_ids)

    def elsewhere(response):
        return sorted((a.chamber_id, a.minute, a.code) for a in response.alarms
                      if a.chamber_id not in affected)

    assert elsewhere(faulted) == elsewhere(null)

    compared = 0
    for trajectory in null.realization.trajectories:
        if trajectory.chamber_id in affected:
            continue
        other = faulted.realization.trajectory(trajectory.chamber_id,
                                               trajectory.latent)
        assert other.values == trajectory.values
        compared += 1
    assert compared > 60


def test_an_unrelated_alarm_rule_does_not_reshuffle_the_others(make_template,
                                                               world):
    """Adding a rule that watches something else changes nothing else.

    Alarm streams are keyed by chamber and code, so a new code is a new
    stream rather than a shift of every draw after it.
    """
    from fabsim.world import build_world

    raw = make_template()
    raw["alarms"]["codes"].append({
        "code": "PLATEN_SPEED_DEV", "source": "channel",
        "signal": "down_force_psi", "operation_types": ["CMP"],
        "threshold_sigma": 3.0, "severity": "INFO",
        "message": "platen speed outside its control limit"})
    extended = build_world(raw)

    config = from_mapping({**SCENARIO, "events": [PARTICLE_EVENT]})
    widened = respond_scenario(config, world=extended)
    baseline = responded(world, events=[PARTICLE_EVENT])

    cmp_chambers = {c.chamber_id
                    for t in world.tools_for_operation("CMP")
                    for c in world.chambers_of(t.tool_id)}
    unchanged = sorted((a.chamber_id, a.minute, a.code)
                       for a in widened.alarms
                       if a.chamber_id not in cmp_chambers)
    assert unchanged == sorted((a.chamber_id, a.minute, a.code)
                               for a in baseline.alarms
                               if a.chamber_id not in cmp_chambers)


# --------------------------------------------------------- severity response


@pytest.mark.parametrize("event, latent", [
    (EDGE_EVENT, "edge_uniformity"),
    (PARTICLE_EVENT, "particle_load"),
])
def test_a_larger_departure_is_noticed_more_often(world, event, latent):
    """Monotone in severity, measured against the null and nothing else.

    Counted over **three seeds** rather than one. A single realization's count
    is a small integer that the fab's own feedback loop bounds from above: a
    departure large enough to alarm escalates into a repair, the repair takes
    it back down, and the refractory window caps what is left at roughly one
    complaint a day either way. Moderate and obvious therefore sit within each
    other's noise at any one seed, and a test that pinned one seed's ordering
    would be pinning that seed. Pooling triples the evidence and asserts the
    same property — strictly, and at both steps of the ladder.
    """
    chamber = target_chamber(world, event)
    onset = event["onset_day"] * MINUTES_PER_DAY
    counts = []
    for severity in ("subtle", "moderate", "obvious"):
        total = 0
        for seed in (7, 42, 101):
            response = responded(world, default_seed=seed,
                                 events=[dict(event, severity=severity)])
            total += sum(1 for a in response.alarms
                         if a.chamber_id == chamber and a.minute >= onset
                         and response.detail(a.alarm_id).kind == "condition")
        counts.append(total)
    assert counts[0] < counts[1] < counts[2], counts


def test_a_subtle_fault_stays_near_the_null_floor(world, null):
    """`CAUSAL_MECHANISM_MODEL.md` §8: some faults must be analytic-only."""
    chamber = target_chamber(world, EDGE_EVENT)
    subtle = responded(world, events=[dict(EDGE_EVENT, severity="subtle")])
    onset = EDGE_EVENT["onset_day"] * MINUTES_PER_DAY

    def after(response):
        return sum(1 for a in response.alarms
                   if a.chamber_id == chamber and a.minute >= onset)

    assert after(subtle) <= after(null) + 3


# ------------------------------------------------------------ anti-leakage


def _module() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "fabsim" / "response.py"


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


#: Everything that decides whether an alarm fires, whether it escalates, and
#: when the repair happens. The front door (`respond`, `respond_scenario`,
#: `_rebind`) is excluded on purpose: it *forwards* a scenario's events to the
#: latent engine, which is plumbing, and
#: `test_the_front_door_forwards_events_without_reading_them` pins that it
#: never looks inside them.
_DECISION_PATH = ("_RunningLimit", "_ChamberResponder", "Responder",
                  "_signal_value", "_rule_latents", "_latent_spread",
                  "_rule_spread", "_rule_is_adaptive")


def _decision_nodes() -> list[ast.AST]:
    tree = ast.parse(_module().read_text(encoding="utf-8"))
    found = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in _DECISION_PATH]
    assert {n.name for n in found} == set(_DECISION_PATH)
    return found


def test_the_response_layer_cannot_see_a_mechanism():
    """No branch on mechanism identity, because none is ever in scope.

    The alarm rules, the escalation and the repair scheduling are checked as a
    unit: nothing in them names a mechanism, an event, a severity, a
    counterfactual or a departure. What they can see is a chamber, a clock and
    a number.
    """
    forbidden = {"mechanism", "mechanisms", "mechanism_name", "event",
                 "events", "event_index", "severity_calibration",
                 "counterfactual", "departure", "realized_magnitude",
                 "distractor", "distractors", "onset_day", "onset_minute"}
    names: set[str] = set()
    for node in _decision_nodes():
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                names.add(child.id)
            elif isinstance(child, ast.Attribute):
                names.add(child.attr)
            elif isinstance(child, ast.arg):
                names.add(child.arg)
    assert not (names & forbidden)


def test_the_front_door_forwards_events_without_reading_them():
    """`respond`/`respond_scenario` pass events through; they never inspect.

    The only things done with them are handing them to the resolver and
    handing the result to the latent engine — no attribute of an event, and
    no comparison against one, appears anywhere in the pipeline.
    """
    tree = ast.parse(_module().read_text(encoding="utf-8"))
    doors = [node for node in tree.body
             if isinstance(node, ast.FunctionDef)
             and node.name in ("respond", "respond_scenario")]
    assert len(doors) == 2
    for node in doors:
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute):
                base = child.value
                if isinstance(base, ast.Name) and base.id in ("events",
                                                              "distractors"):
                    raise AssertionError(f"reads {child.attr} off an event")
                assert child.attr not in ("mechanism", "severity", "profile",
                                          "onset_day", "latent")


def test_the_response_layer_names_no_tool_or_chamber():
    pattern = re.compile(
        r"(ETCH|CVD|LITHO|CMP|PVD|FURN|IMP|MET|INSP|TEST)-\d")
    assert [s for s in _code_strings(_module()) if pattern.search(s)] == []


def test_the_response_layer_names_no_observable_beyond_its_own():
    """3C+ concepts are absent: no FDC row, no defect, no die, no yield."""
    forbidden = {"run_measurements", "metrology", "defect", "defects",
                 "classified_type", "die_bins", "wafer_yield", "good_die",
                 "bin_code", "yield_pct", "inspections"}
    assert not (_identifiers(_module()) & forbidden)


def test_the_response_layer_reads_and_writes_no_truth_file():
    source = _module().read_text(encoding="utf-8")
    for token in ("truth.json", "truth/", "open(", "write_text", "json.dump"):
        assert token not in source, token
    repository = Path(__file__).resolve().parents[2]
    assert not list(repository.glob("**/truth.json"))
    assert not (repository / "data" / "scenarios").exists()


def test_the_scenario_response_block_is_declared_intent_not_an_input(world):
    """ADR-017: the engine is fab-wide, so a scenario cannot tune its own
    reaction — and two scenarios differing only in `response` react alike."""
    quiet = responded(world, events=[dict(EDGE_EVENT, response={
        "alarm": False, "repair_delay_days_mean": 0.5, "recovery": "none"})])
    loud = responded(world, events=[dict(EDGE_EVENT, response={
        "alarm": True, "repair_delay_days_mean": 9.0, "recovery": "full"})])
    assert quiet.content_sha256() == loud.content_sha256()


def test_the_two_planes_are_separate_objects(null):
    """§18: an emitter handed the observable side cannot reach the hidden one.

    `alarms` and `maintenance` are plain records with no reference back; the
    reason an alarm fired lives in a different collection, reachable only by
    asking the response object for it.
    """
    for alarm in null.alarms[:10]:
        assert not hasattr(alarm, "realization")
        assert not hasattr(alarm, "detail")
    assert {d.alarm_id for d in null.alarm_details} == {
        a.alarm_id for a in null.alarms}
    assert null.detail(null.alarms[0].alarm_id).kind in ("condition",
                                                         "background")
