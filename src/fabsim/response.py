"""
response.py — how the fab notices a condition and reacts to it.

This is the layer between hidden physics and human action::

    latent state
         │  a signal the equipment can watch
         ▼
    generic threshold rule   (world template `alarms`)
         │  detection probability
         ▼
    ALARM  ── plus a background false-alarm rate on every chamber
         │  escalation: N complaints in a window become a work order
         ▼
    REPAIR REQUEST ── delay ~ Exp(mean): the human loop of noticing
         │
         ▼
    MAINTENANCE WINDOW ── blocks production, like every other window
         │
         ▼
    GENERIC RECOVERY (`fabsim.latent`) ── one quality draw, per-latent efficacy

**Nothing here knows what a fault is.** There is no mechanism name in this
module, no event, no target and no scenario field. A rule watches a signal and
compares it against *that chamber's own recent history*; if the departure is
large enough it may fire, and if a chamber complains often enough it gets a
work order. A faulted chamber travels that path because it genuinely deviates,
and a healthy one travels it because false alarms are real and sometimes
cluster. Both arrive at the same kind of window, with the same technician
vocabulary, the same action codes and the same recovery distribution.

Three design decisions carry the anti-leakage weight (ADR-017):

* **The baseline is the chamber's own EWMA**, not zero and not a global mean.
  Real FDC limits are set per chamber from its own history, and modelling it
  that way is what stops a chamber's permanent benign offset from becoming a
  permanent alarm — which would have turned rule F11's honest structure into
  a fingerprint.
* **Escalation counts alarms, not causes.** A background false alarm escalates
  exactly like a condition-driven one. That is what makes maintenance appear
  on healthy chambers, which is what stops "this chamber was repaired" from
  being an answer.
* **The response policy is fab-wide.** No per-scenario, per-mechanism or
  per-chamber variant of any threshold, delay or recovery exists, so the
  engine cannot treat a faulted chamber differently — it has nothing to treat
  it differently *with*. A scenario's `response` block is recorded as declared
  intent for the truth artifact and is never read here.

**Interim signal.** 3C owns the process-observation model; until it lands, a
`channel`-source alarm rule is evaluated against a *latent-space proxy*: the
channel's declared latent sensitivities applied to the chamber's latent state,
normalized by those latents' reference σ. It invents no FDC value and adds no
observable row; it is the same linear map 3C will use, read one plane earlier.
When 3C lands, this is the one function that changes.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabsim.latent import Realization, realize
from fabsim.mechanisms import resolve_distractors, resolve_events
from fabsim.rng import Substreams
from fabsim.routing import effective_dedications
from fabsim.scenario import ScenarioConfig
from fabsim.timeline import (
    MaintenanceWindow,
    Timeline,
    maintenance_occupancy,
    order_maintenance,
    simulate,
    simulate_scenario,
)
from fabsim.world import MINUTES_PER_DAY, World, load_world

__all__ = [
    "RESPONSE_MODEL",
    "Alarm",
    "AlarmRealization",
    "FabResponse",
    "RepairRealization",
    "Responder",
    "respond",
    "respond_scenario",
]

#: Versioned name of the response model, like the latent model's: a change to
#: how the fab reacts is a visible version bump, not a silent reshuffle.
RESPONSE_MODEL = "fabsim.response/v1"

#: Where a sample stops counting towards the *spread* of a chamber's limits.
#: Deliberately not a world-template knob: it is a property of the estimator,
#: like the estimator's own arithmetic, and a fab does not tune it.
_WINSOR_SIGMA = 3.0


# ------------------------------------------------------------ observable side


@dataclass(frozen=True)
class Alarm:
    """One coded alarm, in the shape `SCHEMA_V2_DESIGN.md` §2.17 will emit.

    Deliberately narrow: an id, where, when, which code, how severe, and
    fab-wide templated text. There is no field for what caused it, because a
    real alarm has none — and because a field that existed here could be
    filled from the hidden plane by a later emitter that was not paying
    attention. What tripped it lives in `AlarmRealization`, on the other side
    of the boundary.
    """

    alarm_id: int
    tool_id: int
    chamber_id: int
    minute: int
    code: str
    severity: str
    message: str


# ---------------------------------------------------------------- hidden side


@dataclass(frozen=True)
class AlarmRealization:
    """Why an alarm fired. Hidden: this is answer-key material."""

    alarm_id: int
    #: `condition` — a rule crossed its threshold; `background` — the false
    #: alarm rate every chamber carries.
    kind: str
    signal: str
    source: str
    #: Realized departure from the chamber's own baseline, in σ of the signal.
    deviation_sigma: float


@dataclass(frozen=True)
class RepairRealization:
    """Why a repair happened, and what it was asked to fix. Hidden."""

    order_id: int
    chamber_id: int
    tool_id: int
    #: When escalation completed — the work order, not the wrench.
    raised_minute: int
    start_min: int
    end_min: int
    delay_minutes: int
    #: The alarms that escalated into this order, whatever their kind.
    alarm_ids: tuple[int, ...]
    #: Filled once the final calendar is numbered.
    maint_id: int = -1


# ------------------------------------------------------------ the response


@dataclass(frozen=True)
class FabResponse:
    """One dataset's fab response: what was noticed, and what was done.

    The two planes sit side by side and are *named* so a caller has to choose:
    `alarms` and `maintenance` are observable-shaped and carry no cause;
    `realization` is the hidden plane and is never emitted. A later emitter is
    handed the first two.
    """

    model: str
    world: World
    seed: int
    timeline: Timeline
    #: Observable plane.
    alarms: tuple[Alarm, ...]
    maintenance: tuple[MaintenanceWindow, ...]
    #: Hidden plane.
    realization: Realization
    alarm_details: tuple[AlarmRealization, ...]
    repairs: tuple[RepairRealization, ...]

    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index["detail"] = {d.alarm_id: d for d in self.alarm_details}

    def detail(self, alarm_id: int) -> AlarmRealization:
        """Hidden reason one alarm fired."""
        return self._index["detail"][alarm_id]

    def alarms_of(self, chamber_id: int) -> tuple[Alarm, ...]:
        return tuple(a for a in self.alarms if a.chamber_id == chamber_id)

    def content_sha256(self) -> str:
        """Digest of the whole response; equal iff two responses are."""
        digest = hashlib.sha256()
        digest.update(f"{self.model}\t{self.seed}\n".encode("ascii"))
        for alarm in self.alarms:
            digest.update(
                f"alarm\t{alarm.alarm_id}\t{alarm.tool_id}\t"
                f"{alarm.chamber_id}\t{alarm.minute}\t{alarm.code}\t"
                f"{alarm.severity}\n".encode("utf-8"))
        for window in self.maintenance:
            digest.update(
                f"maint\t{window.maint_id}\t{window.tool_id}\t"
                f"{-1 if window.chamber_id is None else window.chamber_id}\t"
                f"{window.maint_type}\t{window.start_min}\t{window.end_min}\n"
                .encode("utf-8"))
        for repair in self.repairs:
            digest.update(
                f"repair\t{repair.order_id}\t{repair.chamber_id}\t"
                f"{repair.raised_minute}\t{repair.start_min}\t"
                f"{repair.end_min}\n".encode("utf-8"))
        digest.update(self.realization.content_sha256().encode("ascii"))
        return digest.hexdigest()


# ------------------------------------------------------------- the rule engine


def _signal_value(rule: Any, world: World,
                  values: Mapping[str, float]) -> float:
    """The value a rule watches.

    A `latent` rule reads the latent directly. A `channel` rule reads the
    interim latent-space proxy described in the module docstring: the
    channel's own declared sensitivities applied to the chamber's latents.
    Either way it is one number, and the rule's threshold is quoted in σ of
    *that number's own spread*, which the chamber estimates for itself.
    """
    if rule.source == "latent":
        return values[rule.signal]
    channel = world.channel(rule.signal)
    return sum(sensitivity * values[latent]
               for latent, sensitivity in channel.sensitivities)


def _rule_latents(rule: Any, world: World) -> tuple[str, ...]:
    if rule.source == "latent":
        return (rule.signal,)
    return tuple(latent for latent, _s in world.channel(rule.signal
                                                        ).sensitivities)


def _latent_spread(world: World, latent: str) -> float:
    """How much a healthy chamber's latent normally moves, from the world.

    Declared rather than estimated, because it is derivable exactly from the
    dynamics the template already states — and because limits a later
    condition could widen are limits that condition can hide behind. An AR(1)
    has a stationary spread; an accumulating load has the amplitude of the
    sawtooth it makes between cleans.
    """
    dynamics = world.latent(latent)
    if dynamics.family == "ar1":
        return float(dynamics.sigma) / math.sqrt(
            1.0 - float(dynamics.phi) ** 2)
    return (float(dynamics.growth_per_day)
            * world.maintenance.pm_interval_days / math.sqrt(12.0))


def _rule_spread(rule: Any, world: World) -> float:
    if rule.source == "latent":
        return _latent_spread(world, rule.signal)
    return sum(abs(sensitivity) * _latent_spread(world, latent)
               for latent, sensitivity
               in world.channel(rule.signal).sensitivities)


def _rule_is_adaptive(rule: Any, world: World) -> bool:
    """Whether this signal's centre has to keep moving to stay meaningful.

    The chart is matched to the *process*, never to the fault. A stationary
    signal gets fixed limits set at qualification, so a sustained departure
    stays visible for as long as it lasts. A signal that climbs between cleans
    by design gets a centre that climbs with it, or every chamber in the fab
    would alarm at the top of every PM cycle.
    """
    return any(world.latent(latent).family == "accumulation"
               for latent in _rule_latents(rule, world))


class _RunningLimit:
    """A chamber's own control limits for one signal — an individuals chart.

    Set the way a real fab sets them, and the way matters twice over:

    * **The centre is the chamber's own**, tracked by an EWMA. A permanent
      benign offset is absorbed into it instead of becoming a permanent alarm
      — without which rule F11's honest structure would have created exactly
      the fingerprint it exists to prevent. The same EWMA absorbs the particle
      sawtooth every chamber shows between cleans.
    * **The spread is measured once, during a qualification window, and then
      frozen.** A spread that kept adapting would widen to accommodate a slow
      ramp, and a fault would become invisible by being persistent — the
      opposite of what a chart is for. Freezing it also means no later
      condition can influence the limits that judge it.

    Nothing is judged during warm-up: limits computed from three samples would
    fire on the fourth.
    """

    def __init__(self, alpha: float, warmup: int, spread: float) -> None:
        #: Zero for a stationary signal: its centre is fixed at qualification.
        self.alpha = alpha
        self.warmup = max(2, warmup)
        self.spread = spread
        self.samples = 0
        self.mean = 0.0
        self._total = 0.0

    def update(self, value: float) -> float | None:
        """Fold in a sample; return its departure in σ, or `None` if warming.

        The sample is scored against the limits *before* it moves them, so a
        change is measured against the history it broke from rather than
        against a baseline it has already shifted.
        """
        self.samples += 1
        if self.samples <= self.warmup:
            # Qualification: learn where this chamber sits, judge nothing.
            self._total += value
            self.mean = self._total / self.samples
            return None
        deviation = value - self.mean
        self.mean += self.alpha * deviation
        if self.spread <= 0.0:
            return None
        return deviation / self.spread


@dataclass
class _ChamberResponder:
    """The fab's instrumentation and work-order desk, for one chamber.

    One instance per chamber, with its own random substreams, so a condition
    on one chamber cannot perturb what another chamber notices or when it gets
    repaired.
    """

    world: World
    chamber: Any
    tool: Any
    rngs: Substreams
    horizon_minutes: int
    occupied: list[tuple[int, int]]

    def __post_init__(self) -> None:
        policy = self.world.response
        self.rules = tuple(
            rule for rule in self.world.alarms.codes
            if set(rule.operation_types) & set(self.tool.operations))
        self.warmup = int(round(policy.baseline_warmup_days
                                * MINUTES_PER_DAY / 60.0))
        self.limits: dict[str, _RunningLimit] = {
            rule.code: _RunningLimit(
                policy.baseline_alpha if _rule_is_adaptive(rule, self.world)
                else 0.0,
                self.warmup, _rule_spread(rule, self.world))
            for rule in self.rules}
        self.last_alarm: dict[str, int] = {}
        self.alarm_minutes: list[int] = []
        self.drafts: list[tuple[int, str, str, str, float]] = []
        self.orders: list[tuple[int, int, int, int, tuple[int, ...]]] = []
        self.last_order_minute = -(10 ** 9)
        self.refractory = int(round(policy.alarm_refractory_hours * 60.0))
        self.cooldown = int(round(policy.repair_cooldown_days
                                  * MINUTES_PER_DAY))
        self.window_minutes = int(round(policy.escalation_window_days
                                        * MINUTES_PER_DAY))
        self.background = self._draw_background()
        self._background_cursor = 0
        self._pending_alarm_ids: list[int] = []

    # -- background false alarms ------------------------------------------
    def _draw_background(self) -> tuple[tuple[int, str], ...]:
        """The false alarms this chamber will raise, whatever else happens.

        Drawn up front from a memoryless hazard, independently of any
        condition, so the null world carries them exactly as a faulted one
        does (`CAUSAL_MECHANISM_MODEL.md` §7). A chamber with no applicable
        rule raises none, because there is nothing for it to raise.
        """
        rate = self.world.alarms.background_rate_per_chamber_day
        if not self.rules or rate <= 0.0:
            return ()
        rng = self.rngs.stream("alarm.background", self.tool.tool_name,
                               self.chamber.chamber_name)
        drawn: list[tuple[int, str]] = []
        moment = rng.expovariate(rate / MINUTES_PER_DAY)
        while moment < self.horizon_minutes:
            rule = self.rules[rng.randrange(len(self.rules))]
            drawn.append((int(moment), rule.code))
            moment += rng.expovariate(rate / MINUTES_PER_DAY)
        return tuple(drawn)

    # -- the per-step hook -------------------------------------------------
    def observe(self, chamber: Any, index: int, minute: int,
                values: Mapping[str, float]) -> MaintenanceWindow | None:
        """Look at this chamber now; maybe complain, maybe raise an order."""
        fired: list[tuple[int, str, str, str, float]] = []

        # Background alarms are due when the clock passes them.
        while (self._background_cursor < len(self.background)
               and self.background[self._background_cursor][0] <= minute):
            at, code = self.background[self._background_cursor]
            self._background_cursor += 1
            fired.append((at, code, "background", "", 0.0))

        for rule in self.rules:
            deviation = self.limits[rule.code].update(
                _signal_value(rule, self.world, values))
            if deviation is None or deviation < rule.threshold_sigma:
                continue
            if minute - self.last_alarm.get(rule.code, -(10 ** 9)) \
                    < self.refractory:
                continue
            # A crossing is a *chance* to be noticed, not a notification: the
            # equipment misses things (`CAUSAL_MECHANISM_MODEL.md` §7), which
            # is what keeps "the latent crossed" and "an alarm exists" from
            # being the same statement.
            detect = self.rngs.stream("alarm.detection", self.tool.tool_name,
                                      self.chamber.chamber_name, rule.code,
                                      index)
            if detect.random() >= self.world.alarms.detection_probability:
                continue
            fired.append((minute, rule.code, "condition", rule.signal,
                          deviation))

        for at, code, kind, signal, deviation in fired:
            self.last_alarm[code] = at
            self.alarm_minutes.append(at)
            self.drafts.append((at, code, kind, signal, deviation))

        return self._maybe_order(minute) if fired else None

    # -- escalation --------------------------------------------------------
    def _maybe_order(self, minute: int) -> MaintenanceWindow | None:
        """N complaints inside the window become one work order.

        Counting alarms rather than causes is the point: a healthy chamber
        that draws three false alarms in a week gets maintenance, and that is
        what keeps a repair from being readable backwards into a fault.
        """
        policy = self.world.response
        recent = [m for m in self.alarm_minutes
                  if minute - m <= self.window_minutes]
        if len(recent) < policy.escalation_count:
            return None
        if minute - self.last_order_minute < self.cooldown:
            return None

        rng = self.rngs.stream("response.repair", self.tool.tool_name,
                               self.chamber.chamber_name, minute)
        delay = int(round(rng.expovariate(1.0 / policy.repair_delay_days_mean)
                          * MINUTES_PER_DAY))
        duration = policy.repair_duration.draw_minutes(rng)
        placed = self._place(minute + delay, duration)
        if placed is None:
            return None
        start, end = placed

        self.last_order_minute = minute
        maintenance = self.world.maintenance
        window = MaintenanceWindow(
            maint_id=-1,
            tool_id=self.tool.tool_id,
            chamber_id=self.chamber.chamber_id,
            # A requested repair is an unscheduled window like any other, with
            # the same technician roster and the same action-code vocabulary
            # as a breakdown: nothing about the observable row says which it
            # was (anti-leakage D2).
            maint_type="UNSCHEDULED",
            start_min=start,
            end_min=end,
            technician=rng.choice(maintenance.technicians),
            action_code=rng.choice(maintenance.unscheduled_action_codes),
        )
        self.orders.append((minute, start, end, delay,
                            tuple(range(len(self.drafts)))))
        self._occupy(start, end)
        return window

    def _place(self, not_before: int, duration: int) -> tuple[int, int] | None:
        """First slot at or after `not_before` that the chamber is free for.

        The chamber's calendar already holds its PMs, its breakdowns and their
        QUAL tails; a repair goes into the first gap that fits, exactly as the
        scheduler places a run. Production has not been laid out yet, so the
        window will block it rather than collide with it.
        """
        qual = int(round(self.world.maintenance.qual_duration_hours * 60.0))
        start = not_before
        for block_start, block_end in sorted(self.occupied):
            if block_end <= start:
                continue
            if block_start >= start + duration + qual:
                break
            start = block_end
        end = start + duration
        if end + qual > self.horizon_minutes:
            return None
        return start, end

    def _occupy(self, start: int, end: int) -> None:
        qual = int(round(self.world.maintenance.qual_duration_hours * 60.0))
        self.occupied.append((start, min(end + qual, self.horizon_minutes)))


class Responder:
    """The fab's response layer, dispatching per chamber.

    Handed to `fabsim.latent.realize`, which calls `observe` as the clock
    passes each chamber and applies the recovery of any window it returns.
    """

    def __init__(self, world: World, timeline: Timeline) -> None:
        self.world = world
        self.rngs = Substreams(timeline.seed)
        occupancy = maintenance_occupancy(world, timeline.maintenance,
                                          timeline.horizon_minutes)
        self.chambers = {
            chamber.chamber_id: _ChamberResponder(
                world=world,
                chamber=chamber,
                tool=world.tool(chamber.tool_id),
                rngs=self.rngs,
                horizon_minutes=timeline.horizon_minutes,
                occupied=[(start, end)
                          for start, end, _state in occupancy[
                              chamber.chamber_id]],
            )
            for chamber in world.chambers
        }
        self.windows: list[MaintenanceWindow] = []

    def observe(self, chamber: Any, index: int, minute: int,
                values: Mapping[str, float]) -> MaintenanceWindow | None:
        window = self.chambers[chamber.chamber_id].observe(
            chamber, index, minute, values)
        if window is not None:
            self.windows.append(window)
        return window

    # -- collected results -------------------------------------------------
    def collect(self) -> tuple[tuple[Alarm, ...], tuple[AlarmRealization, ...],
                               tuple[RepairRealization, ...],
                               tuple[MaintenanceWindow, ...]]:
        """Number the alarms and orders by the clock, and split the planes."""
        drafts = []
        for chamber_id, responder in self.chambers.items():
            for minute, code, kind, signal, deviation in responder.drafts:
                drafts.append((minute, chamber_id, code, kind, signal,
                               deviation, responder.tool.tool_id))
        drafts.sort(key=lambda d: (d[0], d[1], d[2]))

        alarms: list[Alarm] = []
        details: list[AlarmRealization] = []
        by_chamber: dict[int, list[tuple[int, int]]] = {}
        for index, (minute, chamber_id, code, kind, signal, deviation,
                    tool_id) in enumerate(drafts):
            rule = self.world.alarms.code_by_name(code)
            alarms.append(Alarm(alarm_id=index + 1, tool_id=tool_id,
                                chamber_id=chamber_id, minute=minute,
                                code=code, severity=rule.severity,
                                message=rule.message))
            details.append(AlarmRealization(
                alarm_id=index + 1, kind=kind, signal=signal or rule.signal,
                source=rule.source, deviation_sigma=deviation))
            by_chamber.setdefault(chamber_id, []).append((minute, index + 1))

        repairs: list[RepairRealization] = []
        for chamber_id, responder in self.chambers.items():
            for raised, start, end, delay, _draft_ids in responder.orders:
                window_minutes = responder.window_minutes
                escalating = tuple(
                    alarm_id for minute, alarm_id
                    in by_chamber.get(chamber_id, ())
                    if 0 <= raised - minute <= window_minutes)
                repairs.append(RepairRealization(
                    order_id=0, chamber_id=chamber_id,
                    tool_id=responder.tool.tool_id, raised_minute=raised,
                    start_min=start, end_min=end, delay_minutes=delay,
                    alarm_ids=escalating))
        repairs.sort(key=lambda r: (r.raised_minute, r.chamber_id))
        repairs = [RepairRealization(
            order_id=index + 1, chamber_id=r.chamber_id, tool_id=r.tool_id,
            raised_minute=r.raised_minute, start_min=r.start_min,
            end_min=r.end_min, delay_minutes=r.delay_minutes,
            alarm_ids=r.alarm_ids) for index, r in enumerate(repairs)]

        return (tuple(alarms), tuple(details), tuple(repairs),
                tuple(self.windows))


# ----------------------------------------------------------------- front door


def _rebind(realization: Realization,
            calendar: Sequence[MaintenanceWindow]) -> Realization:
    """Point every recovery record at the id its window has in the calendar.

    The response layer inserts repairs into a calendar the scheduler already
    numbered, so the whole calendar is renumbered by the clock afterwards.
    Recovery records are matched back by (chamber, minute) — the physical
    identity of the event, which no renumbering can change.
    """
    from fabsim.latent import LatentReset

    by_key: dict[tuple[int, int], int] = {}
    for window in calendar:
        chambers = ([window.chamber_id] if window.chamber_id is not None
                    else [c.chamber_id for c
                          in realization.world.chambers_of(window.tool_id)])
        for chamber_id in chambers:
            by_key[(chamber_id, window.end_min)] = window.maint_id

    resets = tuple(
        LatentReset(chamber_id=r.chamber_id, latent=r.latent, minute=r.minute,
                    maint_id=by_key.get((r.chamber_id, r.minute), -1),
                    fraction=r.fraction, kind=r.kind, action=r.action,
                    quality=r.quality, no_fix=r.no_fix, before=r.before,
                    after=r.after)
        for r in realization.resets
    )
    return Realization(
        model=realization.model, world=realization.world,
        grid=realization.grid, seed=realization.seed,
        latents=realization.latents, trajectories=realization.trajectories,
        offsets=realization.offsets, mechanisms=realization.mechanisms,
        distractors=realization.distractors, resets=resets,
    )


def respond(timeline: Timeline, *, events: Sequence[Any] = (),
            distractors: Sequence[Any] = ()
            ) -> tuple[Realization, Responder]:
    """Run the latent plane with the response layer attached.

    Returns the hidden realization and the responder that produced it; the
    caller is responsible for laying production out around the repairs the
    responder asked for.
    """
    responder = Responder(timeline.world, timeline)
    realization = realize(timeline, events=events, distractors=distractors,
                          responder=responder)
    return realization, responder


def respond_scenario(config: ScenarioConfig, seed: int | None = None, *,
                     world: World | None = None,
                     template_root: str | Path | None = None) -> FabResponse:
    """Simulate a scenario's fab response, end to end.

    Three passes, in the only order that is causally honest:

    1. **schedule** the fab's own calendar — PM and breakdowns — and lay
       production out against it. This pass is blind to faults, exactly as it
       has been since Step 2.
    2. **walk the clock** over the latent plane with the response layer
       attached: alarms fire, escalate, and become repair windows placed in
       the chamber's free time; every window that ends recovers latents.
    3. **re-lay production** against the completed calendar, so a repair
       blocks production the way a breakdown does. Routing sees the chamber as
       unavailable because by then it is.

    Pass 3 is why a fault can change the schedule at all — and it is honest
    data, not leakage: a chamber taken out of service loses exposure, and
    background breakdowns already do that to every chamber in the fab.
    """
    resolved = world if world is not None else load_world(config.world,
                                                          template_root)
    base = simulate_scenario(config, seed, world=resolved,
                             template_root=template_root)

    realization, responder = respond(
        base,
        events=resolve_events(resolved, config.events),
        distractors=resolve_distractors(resolved, config.distractors))
    alarms, details, repairs, windows = responder.collect()

    calendar = order_maintenance(tuple(base.maintenance) + windows)
    timeline = simulate(resolved, lots=config.lots,
                        horizon_days=config.horizon_days, seed=base.seed,
                        dedications=effective_dedications(resolved, config),
                        maintenance=calendar)

    by_key = {(r.chamber_id, r.end_min): r for r in repairs}
    numbered = []
    for window in calendar:
        key = (window.chamber_id, window.end_min)
        if key in by_key:
            found = by_key[key]
            numbered.append(RepairRealization(
                order_id=found.order_id, chamber_id=found.chamber_id,
                tool_id=found.tool_id, raised_minute=found.raised_minute,
                start_min=found.start_min, end_min=found.end_min,
                delay_minutes=found.delay_minutes, alarm_ids=found.alarm_ids,
                maint_id=window.maint_id))
    numbered.sort(key=lambda r: r.order_id)

    return FabResponse(
        model=RESPONSE_MODEL,
        world=resolved,
        seed=base.seed,
        timeline=timeline,
        alarms=alarms,
        maintenance=calendar,
        realization=_rebind(realization, calendar),
        alarm_details=details,
        repairs=tuple(numbered),
    )
