"""
observation.py — the process observation plane: what the fab measured.

The projection of hidden physical state into numbers an engineer could have
read off an FDC screen or a CD-SEM report::

    recipe setpoint          what the process was asked to do
        + fab-week wander    slow, shared across the whole fab
        + tool offset        benign, permanent, per (tool, channel)
        + chamber offset     benign, permanent, per (chamber, channel)
        + latent effect      sensitivity matrix × the chamber's latent state
        + lot effect         AR(1) across lots
        + run noise          iid, per run
        ─────────────────────────────────────────────────────────────
        = one `run_measurements` row

`CAUSAL_MECHANISM_MODEL.md` §2, term for term. Metrology follows the same
stack referenced to the recipe's `metric_target`, plus a within-wafer radial
term and a metrology-tool bias of its own.

**This module cannot see a fault.** It reads latent trajectories, recipes and
the clock; it never reads `Realization.mechanisms`, `.distractors` or
`.counterfactual`, and it has no scenario, no mechanism name and no entity
literal anywhere in it. A measurement moves because the chamber's *state*
moved, and the module does not know why the state moved — which is the whole
of ADR-004 expressed as an import boundary rather than as a promise.

**Units compose through the severity reference.** A channel's `sensitivity` is
read as *channel scales per latent severity-σ*::

    latent_effect(p) = Σ_latent  sensitivity[p][latent]
                                 × latent(c, τ) / severity_reference[latent]
                                 × scale[p]

Without the normalization the two planes would not meet: latent magnitudes are
O(0.01) in their own units and channel scales are O(1) in theirs, and their raw
product is four orders of magnitude below the run noise. With it, a severity
stated in `CAUSAL_MECHANISM_MODEL.md` §8's units arrives in the channel in the
same units, and the world template's sensitivities are what set how much of it
each channel sees (ADR-018).

**The effect is per-run small on purpose.** A moderate fault moves the *weekly
aggregate* by about 3σ (§8); one run sees a fraction of that, buried under run
noise, a lot term and a fab-week term. Per-wafer distributions overlap heavily
between a healthy chamber and a faulted one. That is the design's difficulty
axis, not a shortfall — the audited v1 gave the answer away in a single GROUP
BY, and this is what replaces it.

What this module does **not** produce: defect counts, defect coordinates,
classified types, die grids or wafer yield. The chain stops at measurements;
3D and 3E read these and the latent plane, and neither exists yet.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from fabsim.latent import LatentGrid, LatentTrajectory, Realization
from fabsim.rng import Substreams
from fabsim.timeline import Run, Timeline
from fabsim.world import MINUTES_PER_DAY, ObservationChannel, Recipe, World

__all__ = [
    "MINUTES_PER_WEEK",
    "OBSERVATION_MODEL",
    "Metrology",
    "ObservationEngine",
    "ProcessObservations",
    "RunMeasurement",
    "observe",
    "observe_response",
    "zone_radius",
]

#: Versioned name of the observation model, like the latent and response
#: models': a change to how measurements are produced is a visible version
#: bump rather than a silent reshuffle of every dataset ever generated.
OBSERVATION_MODEL = "fabsim.observation/v1"

#: The fab-week grain of the shared wander term.
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY

#: Suffix for the within-wafer spread a metrology channel also reports
#: (`SCHEMA_V2_DESIGN.md` §2.15).
SPREAD_SUFFIX = "sigma"


def zone_radius(index: int, count: int) -> float:
    """Radial position of a wafer zone, 0 at the centre and 1 at the edge.

    `observation.wafer_zones` is declared centre-outward, so a zone's radial
    position is its index normalized onto [0, 1]. Stating the convention here
    rather than adding a radius to every zone keeps the shipped contract
    unchanged and carries the same information for evenly spaced zones.
    """
    return 0.0 if count <= 1 else index / (count - 1)


# ----------------------------------------------------------- observable rows


@dataclass(frozen=True)
class RunMeasurement:
    """One FDC summary for one run (`SCHEMA_V2_DESIGN.md` §2.14).

    Deliberately narrow. `run_id` already carries the wafer, the flow step,
    the tool, the chamber, the recipe and the clock, so repeating any of them
    here would be denormalization — and a field that exists is a field a later
    emitter can fill from the wrong plane. `set_value` is the recipe setpoint
    this reading is *against*, which is what makes an FDC delta meaningful and
    is ordinary MES data.
    """

    run_meas_id: int
    run_id: int
    param_name: str
    value: float
    unit: str
    set_value: float


@dataclass(frozen=True)
class Metrology:
    """One post-process measurement of one wafer (`SCHEMA_V2_DESIGN.md` §2.15).

    `flow_step_id` is the **measured** step, not the metrology step: what a CD
    number indicts is the etch that produced it, and which step that is comes
    from the world's declared `measures` relation rather than from adjacency.
    """

    metrology_id: int
    wafer_id: int
    flow_step_id: int
    metrology_tool_id: int
    meas_time_min: int
    param_name: str
    value: float
    unit: str


@dataclass(frozen=True)
class ProcessObservations:
    """Everything the fab's instrumentation recorded. Observable plane only.

    No hidden field, no cause, no latent, no counterfactual. A later emitter
    is handed this and can serialize all of it.
    """

    model: str
    run_measurements: tuple[RunMeasurement, ...]
    metrology: tuple[Metrology, ...]

    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        by_run: dict[int, list[RunMeasurement]] = {}
        for measurement in self.run_measurements:
            by_run.setdefault(measurement.run_id, []).append(measurement)
        self._index["by_run"] = {k: tuple(v) for k, v in by_run.items()}
        by_wafer: dict[int, list[Metrology]] = {}
        for row in self.metrology:
            by_wafer.setdefault(row.wafer_id, []).append(row)
        self._index["by_wafer"] = {k: tuple(v) for k, v in by_wafer.items()}

    def of_run(self, run_id: int) -> tuple[RunMeasurement, ...]:
        return self._index["by_run"].get(run_id, ())

    def of_wafer(self, wafer_id: int) -> tuple[Metrology, ...]:
        return self._index["by_wafer"].get(wafer_id, ())

    def channel(self, param_name: str) -> tuple[RunMeasurement, ...]:
        return tuple(m for m in self.run_measurements
                     if m.param_name == param_name)

    def content_sha256(self) -> str:
        """Digest of every emitted value; equal iff two runs measured alike."""
        digest = hashlib.sha256()
        digest.update(f"{self.model}\n".encode("ascii"))
        for measurement in self.run_measurements:
            digest.update(
                f"fdc\t{measurement.run_meas_id}\t{measurement.run_id}\t"
                f"{measurement.param_name}\t{measurement.value!r}\t"
                f"{measurement.set_value!r}\n".encode("utf-8"))
        for row in self.metrology:
            digest.update(
                f"met\t{row.metrology_id}\t{row.wafer_id}\t{row.flow_step_id}\t"
                f"{row.metrology_tool_id}\t{row.meas_time_min}\t"
                f"{row.param_name}\t{row.value!r}\n".encode("utf-8"))
        return digest.hexdigest()


# --------------------------------------------------------------- the engine


def _latent_mean(trajectory: LatentTrajectory, grid: LatentGrid,
                 start_min: int, end_min: int) -> float:
    """The chamber's latent state *during* a run, and never after it.

    A run is a window on the same clock the latent grid samples, so the value
    a run saw is the mean of the grid points its window covers. A run shorter
    than the grid step reads one point; a longer one reads the average, which
    is what an FDC summary of a whole run is. Nothing past `end_min` is read,
    so a measurement can never be informed by its own future.
    """
    first = grid.index_at(start_min)
    last = grid.index_at(max(start_min, end_min - 1))
    if last <= first:
        return trajectory.values[first]
    window = trajectory.values[first:last + 1]
    return sum(window) / len(window)


class ObservationEngine:
    """Turns realized physical state into measurements, one run at a time.

    Holds the draws that must be *shared* — the permanent tool and chamber
    offsets, the fab-week wander, the lot AR(1) — so that two runs on one
    chamber agree about the chamber and two chambers in one week agree about
    the week. Everything else is drawn per run.
    """

    def __init__(self, world: World, timeline: Timeline,
                 trajectories: Mapping[tuple[int, str], LatentTrajectory],
                 grid: LatentGrid) -> None:
        self.world = world
        self.timeline = timeline
        self.trajectories = trajectories
        self.grid = grid
        self.rngs = Substreams(timeline.seed)
        self.variation = world.observation.variation
        self.zones = world.observation.wafer_zones
        self._tool: dict[tuple[int, str], float] = {}
        self._chamber: dict[tuple[int, str], float] = {}
        self._week: dict[tuple[int, str], float] = {}
        self._lot: dict[str, tuple[float, ...]] = {}

    # -- the shared draws --------------------------------------------------
    def tool_offset(self, tool_id: int, channel: str) -> float:
        """Permanent, per (tool, channel). Never reset, never conditional."""
        key = (tool_id, channel)
        if key not in self._tool:
            tool = self.world.tool(tool_id)
            rng = self.rngs.stream("observation.tool", tool.tool_name, channel)
            self._tool[key] = rng.gauss(0.0, self.variation.tool_offset)
        return self._tool[key]

    def chamber_offset(self, chamber_id: int, channel: str) -> float:
        """Permanent, per (chamber, channel).

        This is the *second* benign layer: the latent plane already gives each
        chamber a permanent offset on each latent (rule F11), and this adds
        the per-instrument difference §2 asks for on top. Both survive
        maintenance, because neither is a condition.
        """
        key = (chamber_id, channel)
        if key not in self._chamber:
            chamber = self.world.chamber(chamber_id)
            tool = self.world.tool(chamber.tool_id)
            rng = self.rngs.stream("observation.chamber", tool.tool_name,
                                   chamber.chamber_name, channel)
            self._chamber[key] = rng.gauss(0.0, self.variation.chamber_offset)
        return self._chamber[key]

    def week_effect(self, minute: int, channel: str) -> float:
        """Slow fab-wide wander, shared by every chamber in a given week.

        Shared is the point: it is what lets "the whole fab moved" be
        distinguishable from "this chamber moved", and what stops a
        cross-chamber comparison from being trivially clean.
        """
        key = (minute // MINUTES_PER_WEEK, channel)
        if key not in self._week:
            rng = self.rngs.stream("observation.fab_week", channel, key[0])
            self._week[key] = rng.gauss(0.0, self.variation.fab_week)
        return self._week[key]

    def lot_effect(self, lot_id: int, channel: str) -> float:
        """AR(1) across lots: consecutive lots resemble each other.

        Each lot's innovation comes from its own stream and the recursion runs
        forward, so releasing more lots appends to the series rather than
        reshuffling the lots that were already there.
        """
        if channel not in self._lot:
            phi = self.variation.lot_ar1_phi
            sigma = self.variation.lot_ar1_sd
            series: list[float] = []
            state = 0.0
            for lot in self.timeline.lots:
                rng = self.rngs.stream("observation.lot", channel, lot.lot_id)
                state = phi * state + sigma * rng.gauss(0.0, 1.0)
                series.append(state)
            self._lot[channel] = tuple(series)
        return self._lot[channel][lot_id - 1]

    # -- the latent term ---------------------------------------------------
    def latent_effect(self, chamber_id: int, channel: ObservationChannel,
                      start_min: int, end_min: int,
                      radial: float | None = None) -> float:
        """The declared sensitivity matrix, applied to realized latent state.

        In units of `channel.scale` per latent severity-σ (ADR-018). The sign
        follows the latent's: a signed latent that went negative pulls the
        channel down, and an accumulating one — which cannot be negative —
        only ever pushes in the direction its sensitivity states.

        `radial` is the zone's position from centre (0) to edge (1). Latents
        declare how much of their effect is radial rather than uniform, which
        is what makes `cd_nm_edge` and `cd_nm_sigma` respond to a uniformity
        fault while `cd_nm_center` barely moves (§2), without this function
        ever learning which latent that is.
        """
        total = 0.0
        for latent, sensitivity in channel.sensitivities:
            if sensitivity == 0.0:
                continue
            dynamics = self.world.latent(latent)
            trajectory = self.trajectories[(chamber_id, latent)]
            magnitude = _latent_mean(trajectory, self.grid, start_min, end_min)
            weight = 1.0
            if radial is not None:
                shape = dynamics.radial_weight
                weight = (1.0 - shape) + shape * radial
            total += (sensitivity * weight
                      * magnitude / dynamics.severity_reference)
        return total * channel.scale

    # -- the observations --------------------------------------------------
    def observe_run(self, run: Run, recipe: Recipe,
                    next_id: int) -> list[RunMeasurement]:
        """Every FDC channel this run's recipe gives a setpoint for.

        The recipe decides which channels exist for a step, which is what
        keeps an FDC reading grounded in something the fab actually set
        (`SCHEMA_V2_DESIGN.md` §2.14) rather than in a number the observation
        model invented.
        """
        step = self.world.step(run.step_id)
        settings = dict(recipe.settings)
        rows: list[RunMeasurement] = []
        for channel in self.world.channels_for_operation(step.operation_type):
            if channel.kind != "fdc" or channel.name not in settings:
                continue
            rng = self.rngs.stream("observation.run", run.run_id, channel.name)
            setpoint = settings[channel.name]
            value = (
                setpoint
                + channel.scale * (
                    self.week_effect(run.start_min, channel.name)
                    + self.tool_offset(run.tool_id, channel.name)
                    + self.chamber_offset(run.chamber_id, channel.name)
                    + self.lot_effect(run.lot_id, channel.name)
                    + self.variation.run_noise * rng.gauss(0.0, 1.0))
                + self.latent_effect(run.chamber_id, channel,
                                     run.start_min, run.end_min)
            )
            rows.append(RunMeasurement(
                run_meas_id=next_id + len(rows), run_id=run.run_id,
                param_name=channel.name, value=value, unit=channel.unit,
                set_value=setpoint))
        return rows

    def observe_metrology(self, run: Run, measured: Run, recipe: Recipe,
                          next_id: int) -> list[Metrology]:
        """Read out, on a metrology tool, what an earlier step produced.

        Two attributions matter and both come from the world rather than from
        adjacency. *What* is measured is the `measures` relation; *whose*
        state produced it is the measured run's chamber at the measured run's
        time — not the metrology tool's. A CD indicts the etch chamber; the
        metrology tool contributes only its own bias and its own noise.
        """
        step = self.world.step(measured.step_id)
        metric = step.metric
        if metric is None or recipe.metric_target is None:
            return []
        rows: list[Metrology] = []
        for channel in self.world.channels_for_operation(step.operation_type):
            if channel.kind != "metrology" or channel.name != metric.name:
                continue
            values: list[float] = []
            common = (
                recipe.metric_target
                + channel.scale * (
                    self.week_effect(measured.start_min, channel.name)
                    + self.tool_offset(measured.tool_id, channel.name)
                    + self.chamber_offset(measured.chamber_id, channel.name)
                    + self.lot_effect(measured.lot_id, channel.name)
                    # The metrology tool's own permanent bias, small.
                    + self.tool_offset(run.tool_id, channel.name))
            )
            for index, zone in enumerate(self.zones):
                rng = self.rngs.stream("metrology.measurement", run.run_id,
                                       channel.name, zone)
                value = (
                    common
                    + self.latent_effect(measured.chamber_id, channel,
                                         measured.start_min, measured.end_min,
                                         radial=zone_radius(index,
                                                            len(self.zones)))
                    + channel.scale * self.variation.metrology_noise
                    * rng.gauss(0.0, 1.0)
                )
                values.append(value)
                rows.append(Metrology(
                    metrology_id=next_id + len(rows), wafer_id=run.wafer_id,
                    flow_step_id=measured.flow_step_id,
                    metrology_tool_id=run.tool_id, meas_time_min=run.end_min,
                    param_name=f"{channel.name}_{zone}", value=value,
                    unit=channel.unit))
            # The within-wafer spread: a uniformity fault widens it while
            # leaving the centre alone, which is the signature §2 describes.
            mean = sum(values) / len(values)
            spread = math.sqrt(sum((v - mean) ** 2 for v in values)
                               / len(values))
            rows.append(Metrology(
                metrology_id=next_id + len(rows), wafer_id=run.wafer_id,
                flow_step_id=measured.flow_step_id,
                metrology_tool_id=run.tool_id, meas_time_min=run.end_min,
                param_name=f"{channel.name}_{SPREAD_SUFFIX}", value=spread,
                unit=channel.unit))
        return rows


# ----------------------------------------------------------------- front door


def observe(timeline: Timeline, realization: Realization
            ) -> ProcessObservations:
    """Project one realization into the measurements the fab would record.

    Takes the trajectories and nothing else from the hidden plane: the
    mechanism records, the distractors and the counterfactual series are never
    read, and `test_the_observation_engine_cannot_see_a_mechanism` checks that
    structurally rather than by review.
    """
    world = timeline.world
    engine = ObservationEngine(
        world, timeline,
        {(t.chamber_id, t.latent): t for t in realization.trajectories},
        realization.grid)

    runs_of_wafer = {wafer.wafer_id: timeline.runs_of_wafer(wafer.wafer_id)
                     for wafer in timeline.wafers}
    measurements: list[RunMeasurement] = []
    metrology: list[Metrology] = []

    for run in timeline.runs:
        step = world.step(run.step_id)
        product_id = timeline.lot(run.lot_id).product_id
        measurements.extend(engine.observe_run(
            run, world.recipe_for(run.step_id, product_id),
            len(measurements) + 1))

        measured_step = world.measured_step(run.step_id)
        if measured_step is None:
            continue
        measured = next((earlier for earlier in runs_of_wafer[run.wafer_id]
                         if earlier.step_id == measured_step.step_id
                         and earlier.end_min <= run.end_min), None)
        if measured is None:
            # The wafer was measured for a step the horizon cut off. A fab
            # cannot report a number for a run that never happened.
            continue
        metrology.extend(engine.observe_metrology(
            run, measured,
            world.recipe_for(measured.step_id, product_id),
            len(metrology) + 1))

    return ProcessObservations(model=OBSERVATION_MODEL,
                               run_measurements=tuple(measurements),
                               metrology=tuple(metrology))


def observe_response(response: Any) -> ProcessObservations:
    """Measure the fab a `fabsim.response.FabResponse` describes."""
    return observe(response.timeline, response.realization)
