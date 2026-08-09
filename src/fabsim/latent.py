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

**Maintenance acts on dynamics, not on causes** (ADR-020). A wander latent is
held as `offset + drive + credit + state`, and the two things maintenance can
do are told apart by the *kind of work*: a PM cleans or calibrates, so it
corrects a fraction of the whole departure; an unscheduled repair restores, so
it corrects a fraction of the **persistent** departure (`drive + credit`) and
leaves the mean-reverting `state` to revert on its own. Booking a permanent
credit against a transient is over-control, and it used to leave a repaired
chamber several σ from its own baseline in a world where nothing was wrong.

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
    "RECENTRE",
    "RESTORE",
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

#: The two things maintenance can physically do to a latent (ADR-020). The
#: distinction is about the **kind of work**, never about its cause:
#:
#: * `RECENTRE` — a clean or a calibration. It acts on the chamber's whole
#:   departure from nominal, because that is what it measures and corrects: a
#:   wet clean removes every particle it finds, and a recalibration nulls the
#:   delivered-versus-set deviation it reads, whatever produced it.
#: * `RESTORE` — a repair. It acts on the **persistent** departure only. A
#:   technician replacing a worn part removes a degradation that was not going
#:   to correct itself; they do not, and cannot, re-zero a mean-reverting
#:   fluctuation that will revert on its own. Booking a permanent credit
#:   against a transient is what left repaired chambers several σ from their
#:   own baseline before this decomposition existed.
RECENTRE = "recentre"
RESTORE = "restore"


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
    """A maintenance window's realized effect on one chamber's latent.

    Recorded for **every** maintenance event alike — scheduled PM, background
    breakdown and condition-driven repair — because the machinery that
    produces them is one machine (ADR-017). If only some kinds of maintenance
    moved latent state, "behaviour changed after a repair" would separate
    faulted chambers from healthy ones perfectly.
    """

    chamber_id: int
    latent: str
    minute: int
    #: Filled from the final calendar; `-1` until a response-created repair is
    #: rebound to the id its window gets there.
    maint_id: int
    #: Fraction removed on this latent — of *what* is `action`'s business.
    fraction: float
    #: `PM` or `UNSCHEDULED`, matching the window that caused it.
    kind: str = "PM"
    #: What the work physically did to this latent (ADR-020): `recentre` — a
    #: clean or a calibration, which acts on the whole departure from the
    #: chamber's nominal; `restore` — a repair, which acts on the persistent
    #: departure and leaves the self-correcting natural state alone.
    action: str = RECENTRE
    #: The intervention's drawn quality, shared by every latent this event
    #: touched; `None` for a PM, whose per-latent policy is stated directly.
    quality: float | None = None
    #: True when the draw said the intervention achieved nothing.
    no_fix: bool = False
    before: float = 0.0
    after: float = 0.0


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


class _LatentState:
    """One chamber's one latent, stepped forward one grid point at a time.

    Two states run side by side per latent — the configured one and its
    mechanism-free twin — sharing every draw and every maintenance action, so
    the difference between them is the mechanism and nothing else.

    A wander latent is held as **named parts**, not as one number, and that
    decomposition is the whole reason maintenance can be modelled honestly
    (ADR-020)::

        value = offset + drive + credit + state
                  │       │       │        └ the natural AR(1) wander:
                  │       │       │          transient, self-correcting,
                  │       │       │          nobody's fault and nobody's fix
                  │       │       └ standing correction maintenance has booked
                  │       └ what a mechanism imposes
                  └ permanent benign offset; never reset by anything

        persistent departure := drive + credit

    The parts differ in their **dynamics**, and that is what maintenance acts
    on. `state` reverts to zero on its own with a time constant of a couple of
    days; `drive` and `credit` do not revert at all. Booking a permanent
    credit against `state` — which is what a plain `value - offset` departure
    does — corrects something that was going to correct itself and leaves the
    chamber permanently biased the other way. That is the defect ADR-020
    fixes, and it is why the two maintenance actions differ by exactly one
    term in one bracket.

    Nothing here knows *why* a drive is nonzero, and nothing here is told
    which kind of window is calling: `apply` takes an action and a fraction.
    """

    def __init__(self, dynamics: LatentDynamics, offset: float, start: float,
                 step_days: float) -> None:
        self.dynamics = dynamics
        self.offset = offset
        self.step_days = step_days
        self.value = offset
        self._accumulating = dynamics.family == "accumulation"
        if self._accumulating:
            self._load = start
            self._floor = (0.0 if dynamics.floor is None
                           else float(dynamics.floor))
            self._step_sigma = (float(dynamics.sigma_per_day)
                                * math.sqrt(step_days))
        else:
            self._phi = float(dynamics.phi)
            self._sigma = float(dynamics.sigma)
            #: The natural mean-reverting wander. No maintenance ever writes
            #: to it; only `step` does.
            self._state = start
            #: Standing correction maintenance has booked on this latent —
            #: a PM's trim and a repair's restoration in one running total,
            #: because both are persistent and neither decays.
            self._credit = 0.0
            self._drive = 0.0

    # -- the assembled observable level ------------------------------------
    def _assemble(self) -> float:
        self.value = (self.offset + self._drive + self._credit + self._state)
        return self.value

    def step(self, epsilon: float, drive: float) -> float:
        """Advance one grid point and return the new value."""
        if self._accumulating:
            # The drive is an added *growth rate*: a mechanism makes a chamber
            # dirty faster rather than teleporting its load.
            self._load += ((float(self.dynamics.growth_per_day) + drive)
                           * self.step_days + self._step_sigma * epsilon)
            self._load = max(self._load, self._floor)
            self.value = self.offset + self._load
            return self.value
        # The drive is the level the process reverts *to*, not an impulse
        # inside the recursion: a sustained push inside a φ=0.98 recursion
        # would amplify fiftyfold, which is a modelling accident rather
        # than physics.
        self._state = self._phi * self._state + self._sigma * epsilon
        self._drive = drive
        return self._assemble()

    def recentre(self, fraction: float) -> float:
        """A clean or a calibration: act on the whole departure from nominal.

        This is what a PM does, and it acts on everything it can reach because
        that is what the *work* is: a wet clean takes out every particle in
        the chamber, and a recalibration reads the delivered-versus-set
        deviation and trims it out, without asking how much of it was a fault,
        how much was drift and how much was ordinary wander.

        Trimming against ordinary wander is over-control, and over-control
        adds variance — Deming's funnel, in a fab. That is honest behaviour of
        a calibration, it happens to every chamber on the same PM cadence, and
        it is deliberately kept: `CAUSAL_MECHANISM_MODEL.md` §6 says a PM
        *recentres* `param_bias`, and a recentre that skipped the wander would
        do nothing at all on a healthy chamber.
        """
        if self._accumulating:
            self._load *= (1.0 - fraction)
            self.value = self.offset + self._load
            return self.value
        self._credit -= fraction * (self._drive + self._credit + self._state)
        return self._assemble()

    def restore(self, fraction: float) -> float:
        """A repair: act on the **persistent** departure, and only on it.

        A technician replacing a worn part removes a degradation that was not
        going to go away by itself — including a standing trim an earlier PM
        left behind, which is as much a departure from nominal as the fault it
        was compensating. What they do not do is re-zero the chamber's
        ordinary fluctuation: there is nothing there to re-zero, and a credit
        booked against a transient becomes a permanent bias the moment the
        transient reverts.

        The one bracket below is the whole correction. Compared with
        `recentre` it is missing exactly one term — `self._state` — and that
        missing term is what used to send repaired chambers several σ from
        their own baseline in a world where nothing was wrong.

        For an accumulating load there is no such distinction to make: every
        particle in the chamber is real material, whoever put it there, and
        removing a share of it is removing a share of it.
        """
        if self._accumulating:
            self._load *= (1.0 - fraction)
            self.value = self.offset + self._load
            return self.value
        self._credit -= fraction * (self._drive + self._credit)
        return self._assemble()

    def apply(self, action: str, fraction: float) -> float:
        """Dispatch one maintenance action. The only entry point the walk has.

        It takes the *kind of work* and a fraction — never a mechanism, never
        a scenario, never a cause.
        """
        if action == RECENTRE:
            return self.recentre(fraction)
        if action == RESTORE:
            return self.restore(fraction)
        raise ValueError(f"unknown maintenance action {action!r}")


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


def _chamber_maintenance(timeline: Timeline, chamber_id: int,
                         grid: LatentGrid
                         ) -> tuple[tuple[int, int, int, str], ...]:
    """Every window that touches this chamber: (grid index, minute, id, type).

    **Every** window, of every kind. Step 3A took only PMs, and deliberately
    left a debt: if a fault-driven repair moved latent state and a background
    breakdown did not, then "behaviour changed after a repair" would be a
    perfect fault fingerprint, because only faulted chambers would ever have
    had the first kind. One list, one recovery machine, no exceptions
    (ADR-017).
    """
    return tuple(sorted(
        (grid.index_at(w.end_min), w.end_min, w.maint_id, w.maint_type)
        for w in timeline.maintenance_of_chamber(chamber_id)))


def draw_recovery_quality(rng: Any, policy: Any) -> tuple[float, bool]:
    """How well one intervention worked, for one maintenance event.

    `CAUSAL_MECHANISM_MODEL.md` §6: Beta(8, 2) — mean 0.8 — with a 10% chance
    the repair achieves nothing. One draw per event, shared by every latent
    that event touches; what differs between latents is how much of it lands
    (`LatentDynamics.repair_efficacy`).

    The draw asks nothing about *why* the window exists. A background
    breakdown and a condition-driven repair reach this function through the
    same call with the same distribution, which is the whole reason a repair
    cannot be read backwards into a fault.
    """
    if rng.random() < policy.no_fix_probability:
        return 0.0, True
    return rng.betavariate(policy.quality_alpha, policy.quality_beta), False


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


def _recovery_fractions(world: World, rngs: Substreams, tool_name: str,
                        chamber_name: str, latents: Sequence[str],
                        maint_type: str, end_minute: int,
                        pm_rngs: Mapping[str, Any]
                        ) -> list[tuple[str, str, float, float | None, bool]]:
    """What one maintenance event does to each of a chamber's latents.

    Returns `(latent, action, fraction, quality, no_fix)` per latent. Two
    policies, chosen by the *kind of work*, never by its cause:

    * a **PM** is service work — cleaning and calibration. It applies each
      latent's declared `pm_recovery` as a `RECENTRE`: a full clean of the
      particle load, a partial recentre of the delivery bias, nothing at all
      to the hardware, exactly as `CAUSAL_MECHANISM_MODEL.md` §6 states;
    * **unscheduled** work is a repair — a background breakdown and a
      condition-driven repair are the same thing here. It draws one
      intervention quality, spreads it across the latents by their
      `repair_efficacy`, and applies it as a `RESTORE`.

    Nothing in this function can tell a breakdown from a requested repair, and
    nothing in it can see a mechanism. That is what makes "did something get
    repaired?" a question about a chamber's history rather than about the
    answer key.
    """
    if maint_type == "PM":
        result = []
        for latent in latents:
            dynamics = world.latent(latent)
            if not dynamics.pm_resets():
                continue
            fraction = min(1.0, max(0.0, pm_rngs[latent].gauss(
                dynamics.pm_recovery_mean, dynamics.pm_recovery_sd)))
            result.append((latent, RECENTRE, fraction, None, False))
        return result

    rng = rngs.stream("response.recovery", tool_name, chamber_name,
                      int(end_minute))
    quality, no_fix = draw_recovery_quality(rng, world.response.recovery)
    return [(latent, RESTORE,
             quality * world.latent(latent).repair_efficacy, quality, no_fix)
            for latent in latents]


def realize(timeline: Timeline, *,
            events: Sequence[ResolvedEvent] = (),
            distractors: Sequence[ResolvedDistractor] = (),
            responder: Any = None) -> Realization:
    """Integrate the latent plane over a realized timeline.

    Takes the timeline rather than a world and a seed, so a realization can
    never be paired with a schedule it did not come from: the clock, the
    maintenance calendar and the master seed all arrive together.

    `responder`, when given, is the fab's response layer (`fabsim.response`).
    It is shown each chamber's latent values as the walk passes them and may
    schedule a repair; the walk then applies that repair's recovery when its
    window ends, exactly as it applies a PM's or a breakdown's. This module
    knows nothing about alarms, thresholds or escalation — it knows that
    maintenance recovers latents, and the responder knows when maintenance
    happens. Neither of them knows why.
    """
    world = timeline.world
    rngs = Substreams(timeline.seed)
    grid = LatentGrid.for_horizon(timeline.horizon_minutes)
    step_days = grid.step_minutes / MINUTES_PER_DAY
    latents = world.observation.latents
    recovery_policy = world.response.recovery

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
        scheduled = _chamber_maintenance(timeline, chamber.chamber_id, grid)
        pm_rngs: dict[str, Any] = {}
        states: dict[str, _LatentState] = {}
        nulls: dict[str, _LatentState] = {}
        noises: dict[str, Sequence[float]] = {}
        chamber_drives: dict[str, list[float]] = {}
        recorded: dict[str, list[float]] = {latent: [] for latent in latents}
        recorded_null: dict[str, list[float]] = {latent: []
                                                 for latent in latents}

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

            noises[latent] = _blocked_noise(rngs, latent, tool.tool_name,
                                            chamber.chamber_name, grid.points)
            init_rng = rngs.stream(f"latent.{latent}", tool.tool_name,
                                   chamber.chamber_name, "init")
            if dynamics.pm_resets():
                pm_rngs[latent] = rngs.stream(f"latent.{latent}",
                                              tool.tool_name,
                                              chamber.chamber_name, "pm")

            drive = [0.0] * grid.points
            for event in events:
                if (event.latent == latent
                        and chamber.chamber_id in event.chamber_ids):
                    drive = [a + b
                             for a, b in zip(drive, drives[event.index])]
            chamber_drives[latent] = drive

            if dynamics.family == "ar1":
                start = (float(dynamics.sigma)
                         / math.sqrt(1.0 - float(dynamics.phi) ** 2)
                         * init_rng.gauss(0.0, 1.0))
            else:
                # A random phase of the PM sawtooth, so chambers are not all
                # spotless at minute zero — a synchronization that would be a
                # fab-wide artefact rather than a fab.
                start = (init_rng.random() * float(dynamics.growth_per_day)
                         * world.maintenance.pm_interval_days)
            states[latent] = _LatentState(dynamics, offset.total, start,
                                          step_days)
            nulls[latent] = _LatentState(dynamics, offset.total, start,
                                         step_days)

        # One forward walk of the clock. Each grid point: advance every latent,
        # let the response layer look at where they are, then apply whatever
        # maintenance ends here — scheduled, background or requested alike.
        due = {index: [] for index, _m, _i, _t in scheduled}
        for index, minute, maint_id, maint_type in scheduled:
            due[index].append((minute, maint_id, maint_type))
        requested: dict[int, list[tuple[int, int, str]]] = {}

        for index in range(grid.points):
            minute = grid.minute_of(index)
            for latent in latents:
                epsilon = noises[latent][index]
                recorded[latent].append(
                    states[latent].step(epsilon, chamber_drives[latent][index]))
                recorded_null[latent].append(nulls[latent].step(epsilon, 0.0))

            if responder is not None:
                window = responder.observe(
                    chamber, index, minute,
                    {latent: states[latent].value for latent in latents})
                if window is not None:
                    ends = grid.index_at(window.end_min)
                    if ends >= index:
                        requested.setdefault(ends, []).append(
                            (window.end_min, -1, "UNSCHEDULED"))

            ending = due.get(index, []) + requested.get(index, [])
            for end_minute, maint_id, maint_type in sorted(ending):
                fractions = _recovery_fractions(
                    world, rngs, tool.tool_name, chamber.chamber_name,
                    latents, maint_type, end_minute, pm_rngs)
                for latent, action, fraction, quality, no_fix in fractions:
                    before = states[latent].value
                    after = states[latent].apply(action, fraction)
                    # The twin sees the same work with the same realized
                    # fraction. Where the twin moves and the configured state
                    # does not — or the other way round — that difference is
                    # the mechanism, measured rather than asserted.
                    nulls[latent].apply(action, fraction)
                    recorded[latent][-1] = after
                    recorded_null[latent][-1] = nulls[latent].value
                    resets.append(LatentReset(
                        chamber_id=chamber.chamber_id, latent=latent,
                        minute=end_minute, maint_id=maint_id,
                        fraction=fraction, kind=maint_type, action=action,
                        quality=quality, no_fix=no_fix, before=before,
                        after=after))

        for latent in latents:
            trajectories.append(LatentTrajectory(
                grid=grid, chamber_id=chamber.chamber_id, latent=latent,
                offset=states[latent].offset,
                values=tuple(recorded[latent]),
                counterfactual=tuple(recorded_null[latent])))

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
