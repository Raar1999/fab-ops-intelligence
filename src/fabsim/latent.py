"""
latent.py — the hidden plane: per-chamber latent state over time, and the
internal realization that records what actually happened to it.

This is the middle layer ADR-004 requires. A fault does not reach yield, a
defect or a measurement; it reaches *this*, and later planes read this without
ever learning why it looks the way it does::

    scenario ─▶ world + timeline ─▶ LATENT PLANE ─▶ (3C) observations
                                          │         (3D) defects
                                          └────────▶(3E) die + yield

Three latents, per chamber, per grid point (`CAUSAL_MECHANISM_MODEL.md` §1),
each with its own baseline family and its own idea of what a PM means::

    edge_uniformity   ar1, signed        wander about 0    PM: untouched
    param_bias        ar1, signed        wander about 0    PM: partial recentre
    particle_load     accumulation, ≥ 0  sawtooth          PM: full clean

**Every chamber has all of them, always.** That is requirement F10 in one
sentence: the null world exercises the same distribution families as a faulted
one, so no component of the mixture exists only where something is wrong. It
is also why a mechanism can be *removed* from a realization without leaving a
hole — which is exactly how this module records what a mechanism did.

**The counterfactual.** Each trajectory is integrated twice on *identical*
draws: once as configured, and once with every mechanism drive set to zero.
The second is the null trajectory this chamber would have had at this seed,
and the difference between them is the realized mechanism effect — measured,
not asserted. Before an onset the two are bit-identical, which makes "the
fault had not started yet" a checkable property rather than a promise.

**Benign offsets are baseline, not distractor bookkeeping** (requirement F11).
Every tool and every chamber carries a permanent offset on every latent,
drawn from the same Gaussian family whose spread reaches into the subtle
severity band. A declared `benign_offset` distractor does not create one; it
widens the one that is already there. What separates a benign offset from a
fault is therefore only its *shape in time* — constant since day one, and
untouched by maintenance — which is a distinction the later diagnosis engine
has to earn from evidence, because nothing will ever label it.

**Severity is calibrated against the null, and against nothing else**
(`CAUSAL_MECHANISM_MODEL.md` §8). A magnitude is `severity_sigma ×
severity_reference`, where `severity_reference` is the σ of a *healthy*
chamber's weekly-mean latent, declared in the world template and checked
against the realized null by test. No yield, defect count, benchmark score or
diagnostic result is consulted — those do not exist yet, and calibrating
against them is how a project re-invents target leakage in a politer form.

What this module does **not** contain: alarms, repairs, recovery windows, FDC
values, metrology, defects, die grids, yield, or any observable row. The only
thing it produces is hidden physical state, and the only thing it writes is
the in-memory `Realization`, which is never emitted and is never handed to the
observable plane.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from fabsim.mechanisms import (
    MechanismContext,
    ResolvedDistractor,
    ResolvedEvent,
    mechanism,
    resolve_distractors,
    resolve_events,
)
from fabsim.rng import Substreams
from fabsim.scenario import ScenarioConfig
from fabsim.timeline import Timeline
from fabsim.world import MINUTES_PER_DAY, LatentDynamics, World

__all__ = [
    "LATENT_BLOCK_POINTS",
    "LATENT_GRID_MINUTES",
    "LATENT_MODEL",
    "BenignOffset",
    "DistractorRealization",
    "LatentGrid",
    "LatentReset",
    "LatentTrajectory",
    "MechanismRealization",
    "Realization",
    "realize",
    "realize_scenario",
]

#: The latent integration step, in minutes of the project's one clock. One
#: hour is fine enough to carry a several-day ramp and a PM sawtooth without
#: turning an 84-day world into a million-point integration.
#:
#: **This constant is part of the dataset identity.** Every latent process is
#: parameterized per grid step (an AR(1) φ is a φ *per step*), so changing it
#: changes every realization. It may only move together with a `fabsim`
#: version bump, which is what puts it into `build_fingerprint`.
LATENT_GRID_MINUTES = 60

#: Grid points drawn from one random substream — one simulated day. Latent
#: noise is addressed by (tool, chamber, day block) rather than by a single
#: per-chamber stream, so extending the horizon or changing the lot count
#: cannot perturb a day that was already realized. Per-point streams would
#: give the same guarantee at roughly twenty times the cost for no additional
#: property.
LATENT_BLOCK_POINTS = MINUTES_PER_DAY // LATENT_GRID_MINUTES

#: Versioned name of the latent model, in the spirit of the RNG's domain tag:
#: a future change to the integration is a visible version bump rather than a
#: silent reshuffle of every realization ever produced.
LATENT_MODEL = "fabsim.latent/v1"

#: Guard on the per-activation magnitude jitter. Severity is a target rather
#: than a guarantee, but a draw may not flip or annihilate an activation.
_MIN_JITTER = 0.1


# -------------------------------------------------------------------- the grid


@dataclass(frozen=True)
class LatentGrid:
    """The project clock, sampled at the latent step. Not a second timeline.

    Grid point `i` is the state during ``[i·step, (i+1)·step)`` minutes of the
    same simulated clock the timeline runs on, so a run at minute *m* and the
    latent state it saw are the same instant by construction.
    """

    step_minutes: int
    points: int
    horizon_minutes: int

    @classmethod
    def for_horizon(cls, horizon_minutes: int,
                    step_minutes: int = LATENT_GRID_MINUTES) -> "LatentGrid":
        points = max(1, -(-horizon_minutes // step_minutes))
        return cls(step_minutes=step_minutes, points=points,
                   horizon_minutes=horizon_minutes)

    @property
    def points_per_day(self) -> float:
        return MINUTES_PER_DAY / self.step_minutes

    @property
    def points_per_week(self) -> int:
        return max(1, int(round(7 * self.points_per_day)))

    def index_at(self, minute: float) -> int:
        """Grid point covering a minute of the simulated clock."""
        return min(self.points - 1, max(0, int(minute // self.step_minutes)))

    def minute_of(self, index: int) -> int:
        return index * self.step_minutes

    def day_of(self, index: int) -> float:
        return self.minute_of(index) / MINUTES_PER_DAY

    def week_of(self, index: int) -> int:
        return index // self.points_per_week

    @property
    def weeks(self) -> int:
        return -(-self.points // self.points_per_week)


# ------------------------------------------------------------- hidden records


@dataclass(frozen=True)
class BenignOffset:
    """One chamber's permanent, never-reset offset on one latent.

    Present on every chamber of every world, faulted or null. The distractor
    component is extra spread a scenario asked for — not the offset's reason
    for existing.
    """

    chamber_id: int
    latent: str
    tool_component: float
    chamber_component: float
    distractor_component: float

    @property
    def total(self) -> float:
        return (self.tool_component + self.chamber_component
                + self.distractor_component)


@dataclass(frozen=True)
class LatentReset:
    """A maintenance window's realized effect on one chamber's latent."""

    chamber_id: int
    latent: str
    minute: int
    maint_id: int
    #: Fraction of the current departure removed, as drawn for this PM.
    fraction: float


@dataclass(frozen=True)
class LatentTrajectory:
    """One chamber's history of one latent, and its mechanism-free twin."""

    grid: LatentGrid
    chamber_id: int
    latent: str
    #: The permanent benign offset already included in both series.
    offset: float
    values: tuple[float, ...]
    #: The same chamber, same seed, same draws, with every mechanism drive
    #: removed — the null this realization would have been.
    counterfactual: tuple[float, ...]

    def at(self, minute: float) -> float:
        return self.values[self.grid.index_at(minute)]

    def departure(self) -> tuple[float, ...]:
        """Realized mechanism effect: configured minus counterfactual."""
        return tuple(value - null
                     for value, null in zip(self.values, self.counterfactual))

    def weekly_means(self, series: Sequence[float] | None = None
                     ) -> tuple[float, ...]:
        """Week-by-week mean of `series` (the values by default).

        The weekly aggregate is the statistic severity is quoted against
        (`CAUSAL_MECHANISM_MODEL.md` §8), so it is the natural unit for every
        question this plane can answer about magnitude.
        """
        source = self.values if series is None else series
        buckets: list[list[float]] = [[] for _ in range(self.grid.weeks)]
        for index, value in enumerate(source):
            buckets[self.grid.week_of(index)].append(value)
        return tuple(sum(bucket) / len(bucket)
                     for bucket in buckets if bucket)


@dataclass(frozen=True)
class MechanismRealization:
    """What one configured activation actually did to the latent plane.

    Intent (mechanism, target, onset, severity) *and* realization (the drawn
    magnitude, the measured shift, the chambers it reached) — the shape
    `GROUND_TRUTH_CONTRACT.md` §3 asks of an event, held in memory for a truth
    emitter that does not exist yet.
    """

    event_index: int
    mechanism: str
    latent: str
    tool_id: int
    tool_name: str
    chamber_name: str | None
    chamber_ids: tuple[int, ...]
    severity: str
    profile: Mapping[str, Any]
    onset_minute: int
    #: severity × the latent's healthy weekly σ, before the per-activation draw
    nominal_magnitude: float
    #: after it
    realized_magnitude: float
    #: Measured, not asserted: the largest weekly-mean departure the activation
    #: produced on its chambers, in units of the latent's healthy weekly σ.
    realized_shift_sigma: float
    active_from_minute: int
    active_to_minute: int


@dataclass(frozen=True)
class DistractorRealization:
    """What one declared benign structure actually added."""

    index: int
    mechanism: str
    tool_id: int
    tool_name: str
    chamber_name: str | None
    chamber_ids: tuple[int, ...]
    magnitude: str
    #: (chamber_id, latent, added offset) — the extra spread, not the whole
    #: offset, because the rest of it would have been there anyway.
    added: tuple[tuple[int, str, float], ...]


# ------------------------------------------------------------- the realization


@dataclass(frozen=True)
class Realization:
    """The hidden plane of one dataset. Never emitted, never observable.

    Held in memory and handed *explicitly* to the slices allowed to read it.
    There is no registry, no singleton and no path on disk, so an observable
    projection cannot reach it by accident — it can only be given it, and
    `GROUND_TRUTH_CONTRACT.md` §4 says which callers may be.
    """

    model: str
    world: World
    grid: LatentGrid
    seed: int
    latents: tuple[str, ...]
    trajectories: tuple[LatentTrajectory, ...]
    offsets: tuple[BenignOffset, ...]
    mechanisms: tuple[MechanismRealization, ...]
    distractors: tuple[DistractorRealization, ...]
    resets: tuple[LatentReset, ...]

    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index["trajectory"] = {(t.chamber_id, t.latent): t
                                     for t in self.trajectories}
        self._index["offset"] = {(o.chamber_id, o.latent): o
                                 for o in self.offsets}

    def trajectory(self, chamber_id: int, latent: str) -> LatentTrajectory:
        return self._index["trajectory"][(chamber_id, latent)]

    def offset(self, chamber_id: int, latent: str) -> BenignOffset:
        return self._index["offset"][(chamber_id, latent)]

    def value_at(self, chamber_id: int, latent: str, minute: float) -> float:
        """Hidden latent state of one chamber at one minute of the clock."""
        return self.trajectory(chamber_id, latent).at(minute)

    def resets_of(self, chamber_id: int,
                  latent: str) -> tuple[LatentReset, ...]:
        return tuple(r for r in self.resets
                     if r.chamber_id == chamber_id and r.latent == latent)

    # -- identity ---------------------------------------------------------
    def content_sha256(self) -> str:
        """Digest of the whole hidden realization; equal iff they are.

        Floats are hashed by shortest round-trip repr, so the comparison is
        exact rather than tolerant — a determinism test that rounded first
        would be testing the rounding.
        """
        digest = hashlib.sha256()
        digest.update(f"{self.model}\t{self.seed}\n".encode("ascii"))
        for offset in self.offsets:
            digest.update(
                f"offset\t{offset.chamber_id}\t{offset.latent}\t"
                f"{offset.tool_component!r}\t{offset.chamber_component!r}\t"
                f"{offset.distractor_component!r}\n".encode("utf-8"))
        for trajectory in self.trajectories:
            digest.update(
                f"latent\t{trajectory.chamber_id}\t{trajectory.latent}\t"
                .encode("utf-8"))
            digest.update("\t".join(repr(v) for v in trajectory.values)
                          .encode("ascii"))
            digest.update(b"\n")
        for reset in self.resets:
            digest.update(
                f"reset\t{reset.chamber_id}\t{reset.latent}\t{reset.minute}\t"
                f"{reset.maint_id}\t{reset.fraction!r}\n".encode("utf-8"))
        for realized in self.mechanisms:
            digest.update(
                f"mechanism\t{realized.event_index}\t{realized.mechanism}\t"
                f"{realized.latent}\t{realized.realized_magnitude!r}\t"
                f"{realized.realized_shift_sigma!r}\n".encode("utf-8"))
        return digest.hexdigest()


# ---------------------------------------------------------------- integration


def _blocked_noise(rngs: Substreams, latent: str, tool_name: str,
                   chamber_name: str, points: int) -> tuple[float, ...]:
    """Standard normal draws for one chamber's latent, by day block."""
    noise: list[float] = []
    block = 0
    while len(noise) < points:
        rng = rngs.stream(f"latent.{latent}", tool_name, chamber_name, block)
        for _ in range(LATENT_BLOCK_POINTS):
            noise.append(rng.gauss(0.0, 1.0))
        block += 1
    return tuple(noise[:points])


def _integrate_ar1(dynamics: LatentDynamics, noise: Sequence[float],
                   start: float, drive: Sequence[float],
                   resets: Mapping[int, Sequence[float]],
                   offset: float) -> tuple[float, ...]:
    """AR(1) wander around a level a mechanism may move.

    The drive is the level the process reverts *to*, not an impulse added
    inside the recursion: a sustained push inside a φ=0.98 recursion would
    amplify fiftyfold, which is a modelling accident rather than physics. With
    no drive this is exactly the null process — the counterfactual costs no
    special case.

    A reset does not touch the noise process; it books a permanent credit
    against the current departure. So a PM removes 70% of what is there now
    and the process wanders on from there, which is what makes a partially
    recovered chamber different from a repaired one.
    """
    phi = float(dynamics.phi)
    sigma = float(dynamics.sigma)
    state = start
    carry = 0.0
    values: list[float] = []
    for index, epsilon in enumerate(noise):
        state = phi * state + sigma * epsilon
        total = drive[index] + state + carry
        for fraction in resets.get(index, ()):
            carry -= fraction * total
            total = drive[index] + state + carry
        values.append(offset + total)
    return tuple(values)


def _integrate_accumulation(dynamics: LatentDynamics, noise: Sequence[float],
                            start: float, drive: Sequence[float],
                            resets: Mapping[int, Sequence[float]],
                            offset: float,
                            step_days: float) -> tuple[float, ...]:
    """Load that climbs between cleans and is taken back down by them.

    The drive is an added *growth rate*, so a mechanism makes a chamber dirty
    faster rather than teleporting its load — and a PM in the middle of an
    excursion genuinely cleans it, after which the excursion starts climbing
    again from zero. That sequence is the whole of scenario I, and none of it
    is scripted here.
    """
    growth = float(dynamics.growth_per_day)
    step_sigma = float(dynamics.sigma_per_day) * math.sqrt(step_days)
    floor = 0.0 if dynamics.floor is None else float(dynamics.floor)
    load = start
    values: list[float] = []
    for index, epsilon in enumerate(noise):
        load += (growth + drive[index]) * step_days + step_sigma * epsilon
        load = max(load, floor)
        for fraction in resets.get(index, ()):
            load *= (1.0 - fraction)
        values.append(offset + load)
    return tuple(values)


# ------------------------------------------------------------------ the engine


def _benign_components(rngs: Substreams, dynamics: LatentDynamics,
                       tool_name: str, chamber_name: str,
                       distractors: Sequence[tuple[int, float]]
                       ) -> tuple[float, float, float]:
    """Draw one chamber's permanent offset on one latent, by component.

    Signed for a wander latent, folded non-negative for an accumulation one:
    a chamber can run edge-fast or edge-slow, but "residual contamination"
    below zero is not a thing. Both come from the same Gaussian family and the
    same spread, which is what makes a declared distractor a widening rather
    than an introduction (F11).
    """
    reference = dynamics.severity_reference

    tool_rng = rngs.stream("variation.tool", tool_name, dynamics.name)
    tool_component = tool_rng.gauss(0.0, dynamics.benign_tool_sd * reference)

    chamber_rng = rngs.stream("variation.chamber", tool_name, chamber_name,
                              dynamics.name)
    chamber_component = chamber_rng.gauss(
        0.0, dynamics.benign_chamber_sd * reference)

    added = 0.0
    for index, sigma in distractors:
        extra_rng = rngs.stream("variation.chamber", tool_name, chamber_name,
                                dynamics.name, "distractor", index)
        added += extra_rng.gauss(0.0, sigma * reference)

    if dynamics.family == "accumulation":
        return abs(tool_component), abs(chamber_component), abs(added)
    return tool_component, chamber_component, added


def _pm_resets(timeline: Timeline, chamber_id: int, grid: LatentGrid
               ) -> tuple[tuple[int, int, int], ...]:
    """PM windows that clean this chamber: (grid index, minute, maint id).

    Only PMs. Unscheduled repairs move latents too, but a repair is a
    *response* — it is triggered, delayed and partially effective — and all
    three of those belong to 3B. Bringing the reset forward without the
    trigger would have made "a repair that moved a latent" a fault
    fingerprint, since only faults would ever have caused one.
    """
    windows = [w for w in timeline.maintenance_of_chamber(chamber_id)
               if w.maint_type == "PM"]
    return tuple(sorted((grid.index_at(w.end_min), w.end_min, w.maint_id)
                        for w in windows))


def _drive_series(world: World, event: ResolvedEvent, grid: LatentGrid,
                  rngs: Substreams) -> tuple[tuple[float, ...], float, float]:
    """One activation's drive over the grid, and its magnitudes.

    Calibration happens here rather than inside the mechanism, so a mechanism
    cannot choose how big it is: the magnitude is severity × the latent's own
    healthy weekly σ, times a per-activation draw that keeps two activations
    at one severity from being identical.
    """
    dynamics = world.latent(event.latent)
    policy = world.mechanism_policy
    nominal = (world.observation.sigma_for(event.severity)
               * dynamics.severity_reference)

    jitter_rng = rngs.stream(f"mechanism.{event.mechanism}", event.index)
    jitter = max(_MIN_JITTER,
                 jitter_rng.gauss(1.0, policy.severity_jitter_sd))
    realized = nominal * jitter

    context = MechanismContext(
        grid=grid,
        onset_index=grid.index_at(event.onset_day * MINUTES_PER_DAY),
        profile=event.profile,
        magnitude=realized,
        defaults=policy.defaults_for(event.mechanism),
        profile_defaults=policy.profile_defaults(),
        rng=rngs.stream(f"mechanism.{event.mechanism}", event.index, "profile"),
    )
    return mechanism(event.mechanism).contribute(context), nominal, realized


def realize(timeline: Timeline, *,
            events: Sequence[ResolvedEvent] = (),
            distractors: Sequence[ResolvedDistractor] = ()) -> Realization:
    """Integrate the latent plane over a realized timeline.

    Takes the timeline rather than a world and a seed, so a realization can
    never be paired with a schedule it did not come from: the clock, the
    maintenance calendar and the master seed all arrive together.
    """
    world = timeline.world
    rngs = Substreams(timeline.seed)
    grid = LatentGrid.for_horizon(timeline.horizon_minutes)
    step_days = grid.step_minutes / MINUTES_PER_DAY
    latents = world.observation.latents

    # Each activation's drive is computed once: it does not depend on the
    # chamber, because a mechanism is never told which chamber it is on.
    drives: dict[int, tuple[float, ...]] = {}
    magnitudes: dict[int, tuple[float, float]] = {}
    for event in events:
        series, nominal, realized = _drive_series(world, event, grid, rngs)
        drives[event.index] = series
        magnitudes[event.index] = (nominal, realized)

    trajectories: list[LatentTrajectory] = []
    offsets: list[BenignOffset] = []
    resets: list[LatentReset] = []
    added_by_distractor: dict[int, list[tuple[int, str, float]]] = {
        d.index: [] for d in distractors}

    for chamber in world.chambers:
        tool = world.tool(chamber.tool_id)
        pm_windows = _pm_resets(timeline, chamber.chamber_id, grid)
        for latent in latents:
            dynamics = world.latent(latent)

            declared = [(d.index, mechanism(d.mechanism).offset_sigma(
                            d.magnitude,
                            world.mechanism_policy.defaults_for(d.mechanism)))
                        for d in distractors
                        if chamber.chamber_id in d.chamber_ids]
            tool_part, chamber_part, added = _benign_components(
                rngs, dynamics, tool.tool_name, chamber.chamber_name, declared)
            offset = BenignOffset(
                chamber_id=chamber.chamber_id, latent=latent,
                tool_component=tool_part, chamber_component=chamber_part,
                distractor_component=added)
            offsets.append(offset)
            for index, _sigma in declared:
                added_by_distractor[index].append(
                    (chamber.chamber_id, latent, offset.distractor_component))

            noise = _blocked_noise(rngs, latent, tool.tool_name,
                                   chamber.chamber_name, grid.points)
            init_rng = rngs.stream(f"latent.{latent}", tool.tool_name,
                                   chamber.chamber_name, "init")

            reset_map: dict[int, list[float]] = {}
            if dynamics.pm_resets():
                reset_rng = rngs.stream(f"latent.{latent}", tool.tool_name,
                                        chamber.chamber_name, "pm")
                for index, minute, maint_id in pm_windows:
                    fraction = min(1.0, max(0.0, reset_rng.gauss(
                        dynamics.pm_recovery_mean, dynamics.pm_recovery_sd)))
                    reset_map.setdefault(index, []).append(fraction)
                    resets.append(LatentReset(
                        chamber_id=chamber.chamber_id, latent=latent,
                        minute=minute, maint_id=maint_id, fraction=fraction))

            drive = [0.0] * grid.points
            for event in events:
                if (event.latent == latent
                        and chamber.chamber_id in event.chamber_ids):
                    series = drives[event.index]
                    drive = [a + b for a, b in zip(drive, series)]
            null = [0.0] * grid.points

            if dynamics.family == "ar1":
                start = (float(dynamics.sigma)
                         / math.sqrt(1.0 - float(dynamics.phi) ** 2)
                         * init_rng.gauss(0.0, 1.0))
                values = _integrate_ar1(dynamics, noise, start, drive,
                                        reset_map, offset.total)
                counterfactual = _integrate_ar1(dynamics, noise, start, null,
                                                reset_map, offset.total)
            else:
                # A random phase of the PM sawtooth, so chambers are not all
                # spotless at minute zero — a synchronization that would be a
                # fab-wide artefact rather than a fab.
                start = (init_rng.random() * float(dynamics.growth_per_day)
                         * world.maintenance.pm_interval_days)
                values = _integrate_accumulation(dynamics, noise, start, drive,
                                                 reset_map, offset.total,
                                                 step_days)
                counterfactual = _integrate_accumulation(
                    dynamics, noise, start, null, reset_map, offset.total,
                    step_days)

            trajectories.append(LatentTrajectory(
                grid=grid, chamber_id=chamber.chamber_id, latent=latent,
                offset=offset.total, values=values,
                counterfactual=counterfactual))

    by_key = {(t.chamber_id, t.latent): t for t in trajectories}
    return Realization(
        model=LATENT_MODEL,
        world=world,
        grid=grid,
        seed=timeline.seed,
        latents=latents,
        trajectories=tuple(trajectories),
        offsets=tuple(offsets),
        mechanisms=_mechanism_records(world, grid, by_key, events, drives,
                                      magnitudes),
        distractors=tuple(
            DistractorRealization(
                index=d.index, mechanism=d.mechanism, tool_id=d.tool_id,
                tool_name=d.tool_name, chamber_name=d.chamber_name,
                chamber_ids=d.chamber_ids, magnitude=d.magnitude,
                added=tuple(added_by_distractor[d.index]))
            for d in distractors),
        resets=tuple(resets),
    )


def _mechanism_records(world: World, grid: LatentGrid,
                       trajectories: Mapping[tuple[int, str],
                                             LatentTrajectory],
                       events: Sequence[ResolvedEvent],
                       drives: Mapping[int, Sequence[float]],
                       magnitudes: Mapping[int, tuple[float, float]]
                       ) -> tuple[MechanismRealization, ...]:
    """Measure what each activation did, from the trajectories themselves.

    The effect size is read off the realized departure, not copied from the
    configured intent — the thin calibration probe `PHASE_1_ACCEPTANCE.md`
    allows, and the reason `severity_realized` can differ from `severity`.
    It measures the latent plane against the null latent plane and touches
    nothing downstream, because nothing downstream exists.
    """
    records: list[MechanismRealization] = []
    for event in events:
        dynamics = world.latent(event.latent)
        series = drives[event.index]
        active = [i for i, value in enumerate(series) if value != 0.0]
        nominal, realized_magnitude = magnitudes[event.index]

        peaks: list[float] = []
        for chamber_id in event.chamber_ids:
            trajectory = trajectories[(chamber_id, event.latent)]
            weekly = trajectory.weekly_means(trajectory.departure())
            peaks.append(max(weekly, default=0.0))
        shift = (sum(peaks) / len(peaks)) if peaks else 0.0

        records.append(MechanismRealization(
            event_index=event.index,
            mechanism=event.mechanism,
            latent=event.latent,
            tool_id=event.tool_id,
            tool_name=event.tool_name,
            chamber_name=event.chamber_name,
            chamber_ids=event.chamber_ids,
            severity=event.severity,
            profile=event.profile,
            onset_minute=int(event.onset_day * MINUTES_PER_DAY),
            nominal_magnitude=nominal,
            realized_magnitude=realized_magnitude,
            realized_shift_sigma=shift / dynamics.severity_reference,
            active_from_minute=grid.minute_of(active[0]) if active else 0,
            active_to_minute=(grid.minute_of(active[-1]) if active
                              else 0),
        ))
    return tuple(records)


def realize_scenario(config: ScenarioConfig, timeline: Timeline
                     ) -> Realization:
    """Realize the latent plane a scenario configuration implies.

    This is the first slice that reads a scenario's `events` and
    `distractors`, and it is the only one that ever should: everything
    downstream reads *latent state*, which is why a fault's effect on an
    observation is mediated by construction rather than by discipline.
    """
    world = timeline.world
    return realize(timeline,
                   events=resolve_events(world, config.events),
                   distractors=resolve_distractors(world, config.distractors))
