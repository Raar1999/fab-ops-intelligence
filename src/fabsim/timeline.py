"""
timeline.py — the one event clock: lot release, routing, occupancy, states.

Everything that happens in a FabSim world happens on a single simulated
timeline (`TEMPORAL_MODEL.md` §1). There is no post-hoc timestamp painting: a
run's time is when the simulation produced it, a chamber's downtime is time in
which it demonstrably ran nothing, and a later step's time depends on when the
earlier step actually finished. That is the whole point of this slice — the
audit found a world in which "when did it begin?" was unanswerable *in
principle* and in which 34 runs overlapped their own tool's downtime.

The lifecycle::

    lots released (staggered, overlapping)
        ↓
    per wafer, per flow step:  route → wait for the chamber → process
        ↓                       │
    queue delay                 └── candidates = chambers of tools qualified
        ↓                            for the step's operation type, minus
    next flow step                   whatever maintenance has blocked
        ↓
    last step → wafer complete → (all wafers) → lot complete

Wafers are interleaved: the scheduler always advances whichever wafer is ready
earliest, so lots overlap in the line and a chamber's load is the sum of
everything routed to it. A lot is never simulated start-to-finish before the
next one begins.

**Determinism.** Every draw comes from a named substream of the master seed
(`rng.stream`), keyed by the entity it belongs to — `("run_duration", wafer_id,
flow_step_id)`, `("routing", wafer_id, flow_step_id)`, `("maintenance.pm",
tool_name)`. Two consequences the design asks for by name: adding a subsystem
later cannot reshuffle these draws, and a run's duration does not depend on the
chamber that ran it (a chamber whose runs were systematically longer would be a
timing fingerprint — leakage class T8).

**Blindness.** This module reads a scenario's `world`, `lots`, `horizon_days`,
`routing_conditions` and seed. It never reads `events` or `distractors`: the
timeline decides *where and when* things happen, and the mechanism layer later
decides *what that does* to latent state. So two scenarios that differ only in
their faults produce the same physical schedule at the same seed — which is
what makes a fault's effect a comparison rather than a confound, and what makes
"routing cannot use the answer" a property of the code rather than a promise.

Routing conditions are the one scenario field routing does read, and reading
them is the point: a dedication window is declared fab policy whose effect is
plainly visible in `runs` (`SCENARIO_SPECIFICATION.md` §4 G). It shifts
exposure *shares*, never eligibility, so the pool a wafer could have gone to is
the same with and without it — see `fabsim.routing`.

What this slice does **not** model: alarms, latent state, fault-driven repairs,
recovery efficacy, and every observable measurement. Maintenance appears here
only as what it does to the clock — a blocked interval takes no production —
because that invariant belongs to the timeline; its *effect* on chamber
condition belongs to the mechanism layer (`CAUSAL_MECHANISM_MODEL.md` §6).
"""
from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from fabsim.rng import Substreams, validate_master_seed
from fabsim.routing import dedication_in_force, effective_dedications
from fabsim.scenario import ScenarioConfig
from fabsim.world import MINUTES_PER_DAY, Dedication, World, load_world

__all__ = [
    "BLOCKING_STATES",
    "EVENT_KINDS",
    "LOT_STATUSES",
    "MAINTENANCE_TYPES",
    "STATES",
    "WAFER_STATUSES",
    "Lot",
    "MaintenanceWindow",
    "Run",
    "StateInterval",
    "Timeline",
    "TimelineEvent",
    "Wafer",
    "maintenance_occupancy",
    "order_maintenance",
    "simulate",
    "simulate_scenario",
]

#: E10-style chamber states (`SCHEMA_V2_DESIGN.md` §2.16). The ribbon of these
#: intervals tiles the horizon per chamber, so "was it available?" and "was it
#: busy?" are answered by one contiguous, non-overlapping record.
STATES = ("PRODUCTIVE", "IDLE", "DOWN", "PM", "QUAL")

#: States in which a chamber takes no production. The invariant that makes
#: maintenance real: a run may not overlap any of these on its own chamber.
BLOCKING_STATES = ("DOWN", "PM", "QUAL")

MAINTENANCE_TYPES = ("PM", "UNSCHEDULED")
LOT_STATUSES = ("COMPLETED", "IN_PROGRESS")
WAFER_STATUSES = ("COMPLETED", "IN_PROCESS")

#: Event kinds in tie-break rank order: at one instant, things that end are
#: recorded before things that begin, so a chamber is never seen starting a run
#: in the same tick it is still finishing one.
EVENT_KINDS = ("RUN_END", "MAINT_END", "LOT_FINISH", "LOT_RELEASE",
               "MAINT_START", "RUN_START")

_EVENT_RANK = {kind: rank for rank, kind in enumerate(EVENT_KINDS)}


# ----------------------------------------------------------------- entities


@dataclass(frozen=True)
class Lot:
    """A released lot. Times are minute offsets on the simulated clock."""

    lot_id: int
    lot_number: str
    product_id: int
    flow_id: int
    wafer_count: int
    release_min: int
    #: `None` while the lot is still in the line at the horizon.
    finish_min: int | None
    status: str


@dataclass(frozen=True)
class Wafer:
    wafer_id: int
    lot_id: int
    slot_number: int
    status: str
    #: How far the wafer got: number of completed flow steps.
    steps_completed: int


@dataclass(frozen=True)
class Run:
    """One wafer at one flow step on one chamber — the exposure spine.

    Which wafer saw which chamber, when, under which recipe is the substrate of
    every attribution question later asked of the data.
    """

    run_id: int
    wafer_id: int
    lot_id: int
    flow_step_id: int
    step_id: int
    tool_id: int
    chamber_id: int
    recipe_id: int
    operator_id: int
    start_min: int
    end_min: int


@dataclass(frozen=True)
class MaintenanceWindow:
    """A PM or an unscheduled repair. `chamber_id` is `None` for tool-wide PM.

    Every window blocks production for its whole interval, by construction
    rather than by luck, and coincides exactly with a PM/DOWN state interval on
    each affected chamber.
    """

    maint_id: int
    tool_id: int
    chamber_id: int | None
    maint_type: str
    start_min: int
    end_min: int
    technician: str
    action_code: str


@dataclass(frozen=True)
class StateInterval:
    state_id: int
    tool_id: int
    chamber_id: int
    state: str
    start_min: int
    end_min: int


@dataclass(frozen=True)
class TimelineEvent:
    """One ordered entry in the event log.

    The log is a deterministic linearization of the simulation: same inputs,
    same events, same order — including at coincident timestamps.
    """

    seq: int
    minute: int
    kind: str
    ref_id: int
    tool_id: int | None = None
    chamber_id: int | None = None
    lot_id: int | None = None
    wafer_id: int | None = None


# ----------------------------------------------------------------- timeline


@dataclass(frozen=True)
class Timeline:
    """The realized world: what happened, when, on what.

    Holds no observable rows and no measurements — those are projections a
    later emitter takes of this. Everything is in id order.
    """

    world: World
    seed: int
    lots_requested: int
    horizon_days: int
    horizon_minutes: int
    lots: tuple[Lot, ...]
    wafers: tuple[Wafer, ...]
    runs: tuple[Run, ...]
    maintenance: tuple[MaintenanceWindow, ...]
    states: tuple[StateInterval, ...]
    events: tuple[TimelineEvent, ...]

    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index["lot"] = {lot.lot_id: lot for lot in self.lots}
        self._index["wafer"] = {w.wafer_id: w for w in self.wafers}
        self._index["run"] = {r.run_id: r for r in self.runs}
        by_lot: dict[int, list[Wafer]] = {lot.lot_id: [] for lot in self.lots}
        for wafer in self.wafers:
            by_lot[wafer.lot_id].append(wafer)
        self._index["wafers_of_lot"] = {k: tuple(v) for k, v in by_lot.items()}
        by_wafer: dict[int, list[Run]] = {w.wafer_id: [] for w in self.wafers}
        by_chamber: dict[int, list[Run]] = {c.chamber_id: []
                                            for c in self.world.chambers}
        for run in self.runs:
            by_wafer[run.wafer_id].append(run)
            by_chamber[run.chamber_id].append(run)
        self._index["runs_of_wafer"] = {k: tuple(v)
                                        for k, v in by_wafer.items()}
        self._index["runs_on_chamber"] = {k: tuple(v)
                                          for k, v in by_chamber.items()}
        states: dict[int, list[StateInterval]] = {c.chamber_id: []
                                                  for c in self.world.chambers}
        for interval in self.states:
            states[interval.chamber_id].append(interval)
        self._index["states_of_chamber"] = {k: tuple(v)
                                            for k, v in states.items()}

    # -- lookups ----------------------------------------------------------
    def lot(self, lot_id: int) -> Lot:
        return self._index["lot"][lot_id]

    def wafer(self, wafer_id: int) -> Wafer:
        return self._index["wafer"][wafer_id]

    def run(self, run_id: int) -> Run:
        return self._index["run"][run_id]

    def wafers_of_lot(self, lot_id: int) -> tuple[Wafer, ...]:
        return self._index["wafers_of_lot"][lot_id]

    def runs_of_wafer(self, wafer_id: int) -> tuple[Run, ...]:
        """The wafer's runs in the order it took them."""
        return self._index["runs_of_wafer"][wafer_id]

    def runs_on_chamber(self, chamber_id: int) -> tuple[Run, ...]:
        return self._index["runs_on_chamber"][chamber_id]

    def states_of_chamber(self, chamber_id: int) -> tuple[StateInterval, ...]:
        """The chamber's contiguous state ribbon over the whole horizon."""
        return self._index["states_of_chamber"][chamber_id]

    def maintenance_of_chamber(self, chamber_id: int
                               ) -> tuple[MaintenanceWindow, ...]:
        """Windows that blocked this chamber, tool-wide PMs included."""
        tool_id = self.world.chamber(chamber_id).tool_id
        return tuple(m for m in self.maintenance
                     if m.tool_id == tool_id
                     and m.chamber_id in (None, chamber_id))

    # -- the clock --------------------------------------------------------
    def at(self, minutes: int) -> datetime:
        return self.world.at(minutes)

    # -- identity ---------------------------------------------------------
    def canonical_rows(self) -> tuple[tuple[Any, ...], ...]:
        """Every simulated row in a fixed order and a fixed encoding.

        The basis of the content digest, and the thing a determinism test
        should compare: it covers the whole realization rather than a sample of
        it, and it is made only of integers and strings, so equality is exact.
        """
        rows: list[tuple[Any, ...]] = []
        for lot in self.lots:
            rows.append(("lot", lot.lot_id, lot.lot_number, lot.product_id,
                         lot.flow_id, lot.wafer_count, lot.release_min,
                         -1 if lot.finish_min is None else lot.finish_min,
                         lot.status))
        for wafer in self.wafers:
            rows.append(("wafer", wafer.wafer_id, wafer.lot_id,
                         wafer.slot_number, wafer.status,
                         wafer.steps_completed))
        for run in self.runs:
            rows.append(("run", run.run_id, run.wafer_id, run.lot_id,
                         run.flow_step_id, run.step_id, run.tool_id,
                         run.chamber_id, run.recipe_id, run.operator_id,
                         run.start_min, run.end_min))
        for window in self.maintenance:
            rows.append(("maintenance", window.maint_id, window.tool_id,
                         -1 if window.chamber_id is None else window.chamber_id,
                         window.maint_type, window.start_min, window.end_min,
                         window.technician, window.action_code))
        for interval in self.states:
            rows.append(("state", interval.state_id, interval.tool_id,
                         interval.chamber_id, interval.state,
                         interval.start_min, interval.end_min))
        for event in self.events:
            rows.append(("event", event.seq, event.minute, event.kind,
                         event.ref_id))
        return tuple(rows)

    def content_sha256(self) -> str:
        """Digest of the whole realization; equal iff the timelines are."""
        digest = hashlib.sha256()
        for row in self.canonical_rows():
            digest.update("\t".join(str(part) for part in row).encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()


# --------------------------------------------------------------- scheduling


def _merge(intervals: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Sorted, non-overlapping union of intervals (touching ones join)."""
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def _earliest_start(blocks: Sequence[tuple[int, int]], not_before: int,
                    duration: int) -> int:
    """First minute ≥ `not_before` where `duration` fits between `blocks`.

    Runs are placed whole into gaps rather than being interrupted: a wafer that
    cannot finish before the chamber goes down waits for the chamber to come
    back. That is what makes "no run overlaps its chamber's downtime" a
    structural property instead of a post-hoc correction.
    """
    start = not_before
    for block_start, block_end in blocks:
        if block_end <= start:
            continue
        if block_start >= start + duration:
            break
        start = block_end
    return start


def _weighted_choice(rng: Any, weights: Sequence[int]) -> int:
    """Index drawn with the given integer weights (one `random()` call)."""
    total = sum(weights)
    threshold = rng.random() * total
    cumulative = 0
    for index, weight in enumerate(weights):
        cumulative += weight
        if threshold < cumulative:
            return index
    return len(weights) - 1


def _schedule_maintenance(world: World, horizon_min: int, rngs: Substreams
                          ) -> tuple[tuple[MaintenanceWindow, ...],
                                     dict[int, list[tuple[int, int, str]]]]:
    """Lay down the maintenance calendar before any production is scheduled.

    Two sources, both scenario-independent and both present on *every* tool —
    the audited "unscheduled events ⇒ probably the bad tool" pattern must not
    survive into this world:

    * **PM** at the template cadence, per tool, blocking all of its chambers;
    * **breakdowns** from a small memoryless hazard, per chamber.

    Each is followed by a qualification window (QUAL), which also blocks
    production. Overlapping proposals are dropped rather than merged so that a
    chamber's ribbon stays unambiguous.
    """
    policy = world.maintenance
    qual_min = max(0, int(round(policy.qual_duration_hours * 60.0)))
    interval_min = policy.pm_interval_days * MINUTES_PER_DAY
    jitter_min = policy.pm_jitter_days * MINUTES_PER_DAY
    mtbf_min = policy.breakdown_mtbf_days * MINUTES_PER_DAY

    occupied: dict[int, list[tuple[int, int, str]]] = {
        chamber.chamber_id: [] for chamber in world.chambers}
    drafts: list[tuple[int, int, int, int | None, str, str, str]] = []

    def free(chamber_id: int, start: int, end: int) -> bool:
        return all(not (start < other_end and other_start < end)
                   for other_start, other_end, _ in occupied[chamber_id])

    def occupy(chamber_id: int, start: int, end: int, state: str) -> None:
        if end > start:
            occupied[chamber_id].append((start, end, state))
            occupied[chamber_id].sort()

    for tool in world.tools:
        rng = rngs.stream("maintenance.pm", tool.tool_name)
        chambers = world.chambers_of(tool.tool_id)
        moment = rng.uniform(0.0, interval_min)
        while moment < horizon_min:
            start = int(moment)
            duration = policy.pm_duration.draw_minutes(rng)
            technician = rng.choice(policy.technicians)
            action_code = rng.choice(policy.pm_action_codes)
            end = min(start + duration, horizon_min)
            qual_end = min(end + qual_min, horizon_min)
            if end > start and all(free(c.chamber_id, start, qual_end)
                                   for c in chambers):
                drafts.append((start, end, tool.tool_id, None, "PM",
                               technician, action_code))
                for chamber in chambers:
                    occupy(chamber.chamber_id, start, end, "PM")
                    occupy(chamber.chamber_id, end, qual_end, "QUAL")
            moment = end + rng.uniform(interval_min - jitter_min,
                                       interval_min + jitter_min)

    for chamber in world.chambers:
        tool = world.tool(chamber.tool_id)
        rng = rngs.stream("maintenance.breakdown", tool.tool_name,
                          chamber.chamber_name)
        moment = rng.expovariate(1.0 / mtbf_min)
        while moment < horizon_min:
            start = int(moment)
            duration = policy.breakdown_duration.draw_minutes(rng)
            technician = rng.choice(policy.technicians)
            action_code = rng.choice(policy.unscheduled_action_codes)
            end = min(start + duration, horizon_min)
            qual_end = min(end + qual_min, horizon_min)
            if end > start and free(chamber.chamber_id, start, qual_end):
                drafts.append((start, end, tool.tool_id, chamber.chamber_id,
                               "UNSCHEDULED", technician, action_code))
                occupy(chamber.chamber_id, start, end, "DOWN")
                occupy(chamber.chamber_id, end, qual_end, "QUAL")
            moment = end + rng.expovariate(1.0 / mtbf_min)

    # Ids follow the clock, not the order the two sources were generated in.
    drafts.sort(key=lambda d: (d[0], d[2], -1 if d[3] is None else d[3], d[4]))
    windows = tuple(
        MaintenanceWindow(maint_id=index + 1, tool_id=tool_id,
                          chamber_id=chamber_id, maint_type=maint_type,
                          start_min=start, end_min=end, technician=technician,
                          action_code=action_code)
        for index, (start, end, tool_id, chamber_id, maint_type, technician,
                    action_code) in enumerate(drafts)
    )
    return windows, occupied


def order_maintenance(windows: Iterable[MaintenanceWindow]
                      ) -> tuple[MaintenanceWindow, ...]:
    """Renumber a calendar so ids follow the clock, densely, from 1.

    The response layer inserts repairs into a calendar the scheduler already
    produced; this puts the result back into the order `_schedule_maintenance`
    guarantees, so "maintenance 41" means "the 41st thing that happened"
    however the window got there.
    """
    ordered = sorted(
        windows,
        key=lambda w: (w.start_min, w.tool_id,
                       -1 if w.chamber_id is None else w.chamber_id,
                       w.maint_type, w.end_min))
    return tuple(
        MaintenanceWindow(maint_id=index + 1, tool_id=w.tool_id,
                          chamber_id=w.chamber_id, maint_type=w.maint_type,
                          start_min=w.start_min, end_min=w.end_min,
                          technician=w.technician, action_code=w.action_code)
        for index, w in enumerate(ordered)
    )


def maintenance_occupancy(world: World, windows: Iterable[MaintenanceWindow],
                          horizon_min: int
                          ) -> dict[int, list[tuple[int, int, str]]]:
    """Rebuild per-chamber blocked intervals from a maintenance calendar.

    The inverse of what `_schedule_maintenance` accumulates as it draws, so a
    calendar handed in from outside blocks production exactly as one drawn
    here does — including the QUAL tail every window carries.
    """
    qual_min = max(0, int(round(world.maintenance.qual_duration_hours * 60.0)))
    occupied: dict[int, list[tuple[int, int, str]]] = {
        chamber.chamber_id: [] for chamber in world.chambers}
    for window in windows:
        chambers = ([window.chamber_id] if window.chamber_id is not None
                    else [c.chamber_id
                          for c in world.chambers_of(window.tool_id)])
        state = "PM" if window.maint_type == "PM" else "DOWN"
        qual_end = min(window.end_min + qual_min, horizon_min)
        for chamber_id in chambers:
            if window.end_min > window.start_min:
                occupied[chamber_id].append(
                    (window.start_min, window.end_min, state))
            if qual_end > window.end_min:
                occupied[chamber_id].append(
                    (window.end_min, qual_end, "QUAL"))
    for intervals in occupied.values():
        intervals.sort()
    return occupied


def _release_lots(world: World, lots: int, horizon_min: int,
                  rngs: Substreams) -> tuple[tuple[Lot, ...],
                                             tuple[Wafer, ...]]:
    """Stagger lot releases across the horizon and populate their carriers.

    Releases are jittered rather than periodic: the audited weekly product-mix
    artifact came from a cadence so regular that time and product were the same
    variable. Each lot draws from its own substream, so changing the lot count
    does not reshuffle the lots that were already there.
    """
    policy = world.lot_release
    weights = [product.mix_weight for product in world.products]
    released: list[Lot] = []
    wafers: list[Wafer] = []
    moment = policy.first_release_day * MINUTES_PER_DAY
    for index in range(lots):
        rng = rngs.stream("lot_release", index)
        if index:
            gap_days = rng.uniform(
                policy.mean_interval_days - policy.jitter_days,
                policy.mean_interval_days + policy.jitter_days)
            moment += gap_days * MINUTES_PER_DAY
        product = world.products[_weighted_choice(rng, weights)]
        release_min = min(int(moment), max(0, horizon_min - 1))
        lot_id = index + 1
        released.append(Lot(
            lot_id=lot_id,
            lot_number=f"LOT-{2026000 + lot_id}",
            product_id=product.product_id,
            flow_id=product.flow_id,
            wafer_count=world.wafers_per_lot,
            release_min=release_min,
            finish_min=None,
            status="IN_PROGRESS",
        ))
        for slot in range(1, world.wafers_per_lot + 1):
            wafers.append(Wafer(wafer_id=len(wafers) + 1, lot_id=lot_id,
                                slot_number=slot, status="IN_PROCESS",
                                steps_completed=0))
    return tuple(released), tuple(wafers)


def _route(world: World, *, product_name: str, operation_type: str,
           flow_step_id: int, lot_id: int, ready_min: int, duration: int,
           chamber_free: Mapping[int, int],
           blocks: Mapping[int, Sequence[tuple[int, int]]],
           last_chamber: Mapping[tuple[int, int], int],
           dedications: Sequence[Dedication],
           rng: Any) -> tuple[int, int]:
    """Pick a chamber for one wafer at one flow step; return (chamber, start).

    In order (`TEMPORAL_MODEL.md` §2.2): candidates are the chambers of every
    tool qualified for the step's operation type; a dedication in force may —
    with probability `share` — restrict *this decision* to the dedicated tool;
    with probability `stickiness` the lot reuses the chamber its previous wafer
    used *at this step*; otherwise the earliest-available candidate wins.

    The dedication draw is a preference and not a filter (ADR-015): it changes
    the probability that traffic lands on the dedicated tool, never which tools
    are eligible. When the draw does not go the dedication's way the whole
    qualified pool is back — including the dedicated tool, which is why the
    realized share sits a little above the configured one. Qualification,
    chamber eligibility, stickiness and availability are untouched by it.

    Ties among equally-available chambers are broken by a seeded draw, not by
    chamber id. Id order is deterministic too, but in a lightly loaded fab
    nearly every candidate is free at once, so the lowest id would win almost
    every time and the rest of the pool would starve — an exposure pattern
    dictated by the order entities happen to appear in the world template.

    Stickiness is drawn whether or not it can be honoured, so the draw sequence
    of a wafer does not depend on its lot's history. The dedication draw, by
    contrast, is taken only where a dedication is actually in force and has
    somewhere to send the wafer: a world that declares none — every world in
    the library today — routes exactly as it did before this rule existed.
    Earliest-available is what couples routing to downtime: a chamber that is
    down loses exposure because it is down, not because anything told routing
    about it.
    """
    candidates = world.eligible_chambers(flow_step_id)
    day = ready_min / MINUTES_PER_DAY
    dedication = dedication_in_force(dedications, product_name, operation_type,
                                     day)
    if dedication is not None:
        preferred = tuple(
            chamber for chamber in candidates
            if world.tool(chamber.tool_id).tool_name == dedication.tool_name
        )
        # A dedication with no candidate here cannot express itself, and it may
        # not deadlock the line either; the draw is skipped so that `share`
        # stays a share of the traffic the dedication could actually take.
        if preferred and rng.random() < dedication.share:
            candidates = preferred

    coin = rng.random()
    previous = last_chamber.get((lot_id, flow_step_id))
    sticky = (coin < world.routing.stickiness and previous is not None
              and any(c.chamber_id == previous for c in candidates))
    if sticky:
        chosen = previous
    else:
        starts = {
            chamber.chamber_id: _earliest_start(
                blocks[chamber.chamber_id],
                max(ready_min, chamber_free[chamber.chamber_id]), duration)
            for chamber in candidates
        }
        soonest = min(starts.values())
        tied = sorted(chamber_id for chamber_id, start in starts.items()
                      if start == soonest)
        chosen = tied[rng.randrange(len(tied))]
    start = _earliest_start(blocks[chosen],
                            max(ready_min, chamber_free[chosen]), duration)
    return chosen, start


def _build_state_ribbon(world: World, horizon_min: int,
                        runs: Sequence[Run],
                        occupied: Mapping[int, Sequence[tuple[int, int, str]]]
                        ) -> tuple[StateInterval, ...]:
    """Turn runs and maintenance into one contiguous ribbon per chamber.

    The ribbon tiles ``[0, horizon]`` with no gaps and no overlaps, so a
    chamber can never be two things at once — the "AVAILABLE and BUSY at the
    same time" contradiction is unrepresentable rather than merely unlikely.
    Everything not accounted for by a run or a maintenance window is IDLE.
    """
    by_chamber: dict[int, list[tuple[int, int, str]]] = {
        chamber.chamber_id: [] for chamber in world.chambers}
    for chamber_id, intervals in occupied.items():
        by_chamber[chamber_id].extend(intervals)
    for run in runs:
        by_chamber[run.chamber_id].append((run.start_min, run.end_min,
                                           "PRODUCTIVE"))

    ribbon: list[StateInterval] = []
    for chamber in world.chambers:
        intervals = sorted(by_chamber[chamber.chamber_id])
        filled: list[tuple[int, int, str]] = []
        cursor = 0
        for start, end, state in intervals:
            if start < cursor:
                raise AssertionError(
                    f"overlapping intervals on chamber {chamber.chamber_id}: "
                    f"{state} at {start} follows activity ending {cursor}")
            if start > cursor:
                filled.append((cursor, start, "IDLE"))
            filled.append((start, end, state))
            cursor = end
        if cursor < horizon_min:
            filled.append((cursor, horizon_min, "IDLE"))

        for start, end, state in filled:
            if ribbon and ribbon[-1].chamber_id == chamber.chamber_id \
                    and ribbon[-1].state == state \
                    and ribbon[-1].end_min == start:
                # Merge what is really one uninterrupted stretch.
                previous = ribbon.pop()
                start = previous.start_min
            ribbon.append(StateInterval(
                state_id=len(ribbon) + 1, tool_id=chamber.tool_id,
                chamber_id=chamber.chamber_id, state=state,
                start_min=start, end_min=end))
    # Merging rewrote positions, so renumber once at the end.
    return tuple(
        StateInterval(state_id=index + 1, tool_id=interval.tool_id,
                      chamber_id=interval.chamber_id, state=interval.state,
                      start_min=interval.start_min, end_min=interval.end_min)
        for index, interval in enumerate(ribbon)
    )


def _build_events(lots: Sequence[Lot], runs: Sequence[Run],
                  maintenance: Sequence[MaintenanceWindow]
                  ) -> tuple[TimelineEvent, ...]:
    """Linearize the realization into one deterministically ordered log."""
    drafts: list[tuple[int, int, str, int, int | None, int | None,
                       int | None, int | None]] = []
    for lot in lots:
        drafts.append((lot.release_min, _EVENT_RANK["LOT_RELEASE"],
                       "LOT_RELEASE", lot.lot_id, None, None, lot.lot_id,
                       None))
        if lot.finish_min is not None:
            drafts.append((lot.finish_min, _EVENT_RANK["LOT_FINISH"],
                           "LOT_FINISH", lot.lot_id, None, None, lot.lot_id,
                           None))
    for run in runs:
        for kind, minute in (("RUN_START", run.start_min),
                             ("RUN_END", run.end_min)):
            drafts.append((minute, _EVENT_RANK[kind], kind, run.run_id,
                           run.tool_id, run.chamber_id, run.lot_id,
                           run.wafer_id))
    for window in maintenance:
        for kind, minute in (("MAINT_START", window.start_min),
                             ("MAINT_END", window.end_min)):
            drafts.append((minute, _EVENT_RANK[kind], kind, window.maint_id,
                           window.tool_id, window.chamber_id, None, None))

    drafts.sort(key=lambda d: (d[0], d[1], d[3]))
    return tuple(
        TimelineEvent(seq=index + 1, minute=minute, kind=kind, ref_id=ref_id,
                      tool_id=tool_id, chamber_id=chamber_id, lot_id=lot_id,
                      wafer_id=wafer_id)
        for index, (minute, _rank, kind, ref_id, tool_id, chamber_id, lot_id,
                    wafer_id) in enumerate(drafts)
    )


# ---------------------------------------------------------------- simulation


def simulate(world: World, *, lots: int, horizon_days: int, seed: int,
             dedications: Sequence[Dedication] | None = None,
             maintenance: Sequence[MaintenanceWindow] | None = None
             ) -> Timeline:
    """Run the clock over `world` and return the realized timeline.

    The scheduler always advances whichever wafer is ready earliest (ties by
    lot then slot), so lots interleave and every step's time depends on when
    the previous one actually finished. A run that could not finish inside the
    horizon is not recorded at all: its wafer — and its lot — is simply still
    in the line when the window closes, which is what a real fab's WIP looks
    like at any cut-off.

    `dedications` are the dedications *in force* for this build — the world's
    standing policy with a scenario's routing conditions layered in front of
    it, as composed by `fabsim.routing.effective_dedications`. Omitted, it is
    the world's standing policy alone, which is what a world simulated without
    a scenario means. They are the only thing a scenario tells the timeline
    beyond its size and its seed, and they are observable policy rather than
    the answer.

    `maintenance` replaces the calendar this function would otherwise draw.
    Omitted — the Step 2 path, and what every scenario got before the response
    layer existed — it schedules PM and breakdowns itself. Supplied, it takes
    the calendar as given and lays production out around it: that is how a
    response-driven repair comes to block production without this scheduler
    learning anything about why the repair happened (`fabsim.response`, and
    ADR-017). The calendar is used verbatim, ids included, so the response
    layer and the timeline agree on which window is which.
    """
    validate_master_seed(seed)
    if not isinstance(lots, int) or isinstance(lots, bool) or lots < 1:
        raise ValueError(f"lots must be a positive integer, got {lots!r}")
    if (not isinstance(horizon_days, int) or isinstance(horizon_days, bool)
            or horizon_days < 1):
        raise ValueError(
            f"horizon_days must be a positive integer, got {horizon_days!r}")

    rngs = Substreams(seed)
    horizon_min = horizon_days * MINUTES_PER_DAY
    active_dedications = (world.routing.dedications if dedications is None
                          else tuple(dedications))

    if maintenance is None:
        maintenance, occupied = _schedule_maintenance(world, horizon_min, rngs)
    else:
        maintenance = tuple(maintenance)
        occupied = maintenance_occupancy(world, maintenance, horizon_min)
    blocks = {chamber_id: _merge((start, end)
                                 for start, end, _state in intervals)
              for chamber_id, intervals in occupied.items()}
    released, wafers = _release_lots(world, lots, horizon_min, rngs)

    chamber_free = {chamber.chamber_id: 0 for chamber in world.chambers}
    last_chamber: dict[tuple[int, int], int] = {}
    progress = {wafer.wafer_id: 0 for wafer in wafers}
    finished_at: dict[int, int] = {}
    lots_by_id = {lot.lot_id: lot for lot in released}
    route_of_lot = {lot.lot_id: world.flow_steps_of(lot.flow_id)
                    for lot in released}
    product_of_lot = {lot.lot_id: world.product(lot.product_id)
                      for lot in released}

    # Ready queue, ordered by (ready minute, lot, slot). Pops are
    # non-decreasing in time — which is what makes this an event simulation
    # rather than a sequence of guesses.
    queue: list[tuple[int, int, int, int]] = []
    for wafer in wafers:
        lot = lots_by_id[wafer.lot_id]
        heapq.heappush(queue, (lot.release_min, wafer.lot_id,
                               wafer.slot_number, wafer.wafer_id))

    runs: list[Run] = []
    while queue:
        ready_min, lot_id, slot_number, wafer_id = heapq.heappop(queue)
        route = route_of_lot[lot_id]
        index = progress[wafer_id]
        flow_step = route[index]
        step = world.step(flow_step.step_id)
        product = product_of_lot[lot_id]

        duration = step.duration.draw(
            rngs.stream("run_duration", wafer_id, flow_step.flow_step_id))
        chamber_id, start_min = _route(
            world,
            product_name=product.product_name,
            operation_type=step.operation_type,
            flow_step_id=flow_step.flow_step_id,
            lot_id=lot_id,
            ready_min=ready_min,
            duration=duration,
            chamber_free=chamber_free,
            blocks=blocks,
            last_chamber=last_chamber,
            dedications=active_dedications,
            rng=rngs.stream("routing", wafer_id, flow_step.flow_step_id),
        )
        end_min = start_min + duration
        if end_min > horizon_min:
            continue  # the horizon closed on this wafer; it stays WIP

        operators = world.operators_on_shift(world.shift_at(start_min))
        operator = operators[
            rngs.stream("operator", wafer_id, flow_step.flow_step_id)
            .randrange(len(operators))]
        runs.append(Run(
            run_id=len(runs) + 1,
            wafer_id=wafer_id,
            lot_id=lot_id,
            flow_step_id=flow_step.flow_step_id,
            step_id=flow_step.step_id,
            tool_id=world.chamber(chamber_id).tool_id,
            chamber_id=chamber_id,
            recipe_id=world.recipe_for(flow_step.step_id,
                                       product.product_id).recipe_id,
            operator_id=operator.operator_id,
            start_min=start_min,
            end_min=end_min,
        ))
        chamber_free[chamber_id] = end_min
        last_chamber[(lot_id, flow_step.flow_step_id)] = chamber_id
        progress[wafer_id] = index + 1

        if index + 1 < len(route):
            delay = world.queue.delay.draw(
                rngs.stream("queue", wafer_id, flow_step.flow_step_id))
            heapq.heappush(queue, (end_min + delay, lot_id, slot_number,
                                   wafer_id))
        else:
            finished_at[wafer_id] = end_min

    # Runs were appended in scheduling order; number them by the clock so a
    # run id means "the nth thing that started", not "the nth thing the
    # scheduler happened to touch".
    runs.sort(key=lambda r: (r.start_min, r.chamber_id, r.wafer_id))
    runs = [Run(run_id=index + 1, wafer_id=r.wafer_id, lot_id=r.lot_id,
                flow_step_id=r.flow_step_id, step_id=r.step_id,
                tool_id=r.tool_id, chamber_id=r.chamber_id,
                recipe_id=r.recipe_id, operator_id=r.operator_id,
                start_min=r.start_min, end_min=r.end_min)
            for index, r in enumerate(runs)]

    wafers = tuple(
        Wafer(wafer_id=wafer.wafer_id, lot_id=wafer.lot_id,
              slot_number=wafer.slot_number,
              status="COMPLETED" if wafer.wafer_id in finished_at
              else "IN_PROCESS",
              steps_completed=progress[wafer.wafer_id])
        for wafer in wafers
    )
    completed_lots: list[Lot] = []
    wafers_by_lot: dict[int, list[Wafer]] = {lot.lot_id: []
                                             for lot in released}
    for wafer in wafers:
        wafers_by_lot[wafer.lot_id].append(wafer)
    for lot in released:
        members = wafers_by_lot[lot.lot_id]
        done = all(w.status == "COMPLETED" for w in members)
        finish_min = None
        if done:
            last_activity = max(finished_at[w.wafer_id] for w in members)
            # Closeout reuses the queue distribution rather than inventing a
            # constant of its own: it is the same kind of handling delay.
            closeout = world.queue.delay.draw(
                rngs.stream("lot_closeout", lot.lot_id))
            finish_min = min(last_activity + closeout, horizon_min)
        completed_lots.append(Lot(
            lot_id=lot.lot_id, lot_number=lot.lot_number,
            product_id=lot.product_id, flow_id=lot.flow_id,
            wafer_count=lot.wafer_count, release_min=lot.release_min,
            finish_min=finish_min,
            status="COMPLETED" if done else "IN_PROGRESS",
        ))

    states = _build_state_ribbon(world, horizon_min, runs, occupied)
    events = _build_events(completed_lots, runs, maintenance)
    return Timeline(
        world=world,
        seed=seed,
        lots_requested=lots,
        horizon_days=horizon_days,
        horizon_minutes=horizon_min,
        lots=tuple(completed_lots),
        wafers=wafers,
        runs=tuple(runs),
        maintenance=maintenance,
        states=states,
        events=events,
    )


def simulate_scenario(config: ScenarioConfig, seed: int | None = None, *,
                      world: World | None = None,
                      template_root: str | Path | None = None) -> Timeline:
    """Simulate the timeline a scenario configuration implies.

    Reads `world`, `lots`, `horizon_days`, `routing_conditions` and the seed —
    and deliberately nothing else. A scenario's `events` and `distractors` are
    invisible here by design: at a fixed seed, the null scenario and a fault
    scenario over the same world produce *the same* schedule, and the mechanism
    layer later changes what that schedule means rather than where the wafers
    went.

    `routing_conditions` are the one exception, and they are an exception in
    the honest direction: a dedication window is declared policy whose effect
    is visible in `runs`, so a diagnosis engine can see and control for it.
    Two scenarios that differ in their routing conditions differ in their
    schedules because the fab was run differently, not because the generator
    leaked anything.
    """
    resolved = world if world is not None else load_world(config.world,
                                                          template_root)
    return simulate(resolved, lots=config.lots,
                    horizon_days=config.horizon_days,
                    seed=config.default_seed if seed is None else seed,
                    dedications=effective_dedications(resolved, config))
