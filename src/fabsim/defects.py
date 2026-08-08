"""
defects.py — the defect/inspection plane: what a wafer scanner found.

The last plane before die and yield, and the one the audit found most
circular. In v1 a defect's *type* decided its coordinates and the coordinates
then "confirmed" the type. Here the chain runs the other way and only once::

    latent state of the chambers an inspection covers
            │  declared per-origin sensitivities
            ▼
    intensity  Λ = Σ_origin ( base_rate × product scale + Σ_latent … )
            │  Poisson(Λ × scanned area)
            ▼
    defects, apportioned to origins by their share of Λ
            │  each origin has its own *geometry*
            ▼
    (x, y) coordinates on the wafer
            │  the inspection reports only what it can see
            ▼
    size threshold
            │  a noisy classifier over the hidden origin
            ▼
    classified_type  ──────▶ observable `defects` row

**The origin is physics, not an answer.** `edge_ring` is what a radial
non-uniformity leaves behind, whoever made the chamber non-uniform; a healthy
chamber with a large benign offset produces some too. The origin is kept in a
*separate hidden record* for a later truth emitter, and the observable defect
row has no field for it — a shape checked structurally, not by review.

**The classifier is an instrument, not a label.** `classified_type` is drawn
through the world's confusion matrix, so a `particle_cluster` is called
PARTICLE about 88% of the time and something else otherwise. Spatial
"confirmation" of a classified type can therefore genuinely fail, which is
exactly what the audited circularity made impossible.

**Coordinates are the artifact.** Nothing here writes a zone, a ring flag or
an edge label. An analyst derives the edge fraction, the radial profile and
the wafer-map signature from `(x_mm, y_mm)` — and so will 3E, which needs real
coordinates to intersect defects with a die grid.

What this module does **not** contain: `killer_flag` (deliberately dropped by
`SCHEMA_V2_DESIGN.md` §2.20 — killer status is something a fab learns from
test overlay, and 3E is where that happens), any die concept, and any path
from a defect to a yield number.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from fabsim.latent import LatentGrid, LatentTrajectory, Realization
from fabsim.rng import Substreams
from fabsim.timeline import Run, Timeline
from fabsim.world import DefectPolicy, ProcessStep, World

__all__ = [
    "DEFECT_MODEL",
    "Defect",
    "DefectOrigin",
    "DefectPopulation",
    "Inspection",
    "inspect",
    "inspect_response",
    "poisson",
]

#: Versioned name of the defect model, like the latent, response and
#: observation models': a change to how defects arise is a visible version
#: bump rather than a silent reshuffle of every dataset ever generated.
DEFECT_MODEL = "fabsim.defects/v1"

#: Poisson draws are chunked above this mean so that `exp(-λ)` never
#: underflows. A sum of Poissons is a Poisson, so the split is exact.
_POISSON_CHUNK = 30.0


def poisson(rng: Any, mean: float) -> int:
    """Knuth's Poisson, chunked so a large mean cannot underflow `exp`.

    The design keeps FabSim stdlib-only (`FABSIM_DESIGN.md` §8), and `random`
    has no Poisson; this is the "inverse-transform Poisson" §8 anticipates.
    """
    if mean <= 0.0:
        return 0
    total = 0
    remaining = mean
    while remaining > 0.0:
        chunk = min(remaining, _POISSON_CHUNK)
        remaining -= chunk
        limit = math.exp(-chunk)
        product = rng.random()
        count = 0
        while product > limit:
            count += 1
            product *= rng.random()
        total += count
    return total


# ----------------------------------------------------------- observable rows


@dataclass(frozen=True)
class Inspection:
    """One defect scan of one wafer (`SCHEMA_V2_DESIGN.md` §2.19).

    `total_defect_count` is the number of `Defect` rows that reference it —
    a reconciliation the emitter's self-test asserts (§4.3), and which holds
    here by construction because the count *is* the length of that list.
    """

    inspection_id: int
    wafer_id: int
    flow_step_id: int
    inspection_tool_id: int
    inspection_time_min: int
    total_defect_count: int
    scan_area_mm2: float


@dataclass(frozen=True)
class Defect:
    """One reported defect (`SCHEMA_V2_DESIGN.md` §2.20).

    No `killer_flag` — killer status is ground truth a fab only learns from
    test overlay, and its observable counterpart is `die_bins`. No origin
    either: what the scanner reports is where it saw something, how big, and
    what its classifier decided to call it.
    """

    defect_id: int
    inspection_id: int
    wafer_id: int
    x_mm: float
    y_mm: float
    size_um: float
    classified_type: str
    layer: str


# ---------------------------------------------------------------- hidden side


@dataclass(frozen=True)
class DefectOrigin:
    """What actually produced one defect, and where it came from. Hidden.

    Answer-key material for a later truth emitter: the physical component it
    was drawn from, and — where the component is latent-driven — which covered
    run's chamber contributed it. `-1` means the defect came from the
    background propensity every wafer has.
    """

    defect_id: int
    origin: str
    contributing_chamber_id: int
    contributing_flow_step_id: int


# ------------------------------------------------------------- the population


@dataclass(frozen=True)
class DefectPopulation:
    """One dataset's defect plane, with the two sides named apart.

    `inspections` and `defects` are observable-shaped and carry no cause;
    `origins` is the hidden record and is never emitted. A later emitter is
    handed the first two.
    """

    model: str
    inspections: tuple[Inspection, ...]
    defects: tuple[Defect, ...]
    origins: tuple[DefectOrigin, ...]

    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        by_inspection: dict[int, list[Defect]] = {}
        for defect in self.defects:
            by_inspection.setdefault(defect.inspection_id, []).append(defect)
        self._index["by_inspection"] = {k: tuple(v)
                                        for k, v in by_inspection.items()}
        by_wafer: dict[int, list[Inspection]] = {}
        for inspection in self.inspections:
            by_wafer.setdefault(inspection.wafer_id, []).append(inspection)
        self._index["by_wafer"] = {k: tuple(v) for k, v in by_wafer.items()}
        self._index["origin"] = {o.defect_id: o for o in self.origins}

    def of_inspection(self, inspection_id: int) -> tuple[Defect, ...]:
        return self._index["by_inspection"].get(inspection_id, ())

    def inspections_of(self, wafer_id: int) -> tuple[Inspection, ...]:
        return self._index["by_wafer"].get(wafer_id, ())

    def origin_of(self, defect_id: int) -> DefectOrigin:
        """Hidden physical origin of one defect."""
        return self._index["origin"][defect_id]

    def content_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(f"{self.model}\n".encode("ascii"))
        for inspection in self.inspections:
            digest.update(
                f"insp\t{inspection.inspection_id}\t{inspection.wafer_id}\t"
                f"{inspection.flow_step_id}\t{inspection.inspection_tool_id}\t"
                f"{inspection.inspection_time_min}\t"
                f"{inspection.total_defect_count}\n".encode("utf-8"))
        for defect in self.defects:
            digest.update(
                f"def\t{defect.defect_id}\t{defect.inspection_id}\t"
                f"{defect.x_mm!r}\t{defect.y_mm!r}\t{defect.size_um!r}\t"
                f"{defect.classified_type}\t{defect.layer}\n".encode("utf-8"))
        return digest.hexdigest()


# ------------------------------------------------------------------ the model


def _latent_mean(trajectory: LatentTrajectory, grid: LatentGrid,
                 start_min: int, end_min: int) -> float:
    """The chamber's latent state during a run, and never after it."""
    first = grid.index_at(start_min)
    last = grid.index_at(max(start_min, end_min - 1))
    if last <= first:
        return trajectory.values[first]
    window = trajectory.values[first:last + 1]
    return sum(window) / len(window)


def _propensity(world: World, latent: str, value: float) -> float:
    """A latent's magnitude, in severity-σ, as an intensity can use it.

    An intensity may not go negative, and a *signed* latent says nothing about
    which way is worse: a chamber that etches its edge fast and one that
    etches it slow are both non-uniform, and both leave a ring. The magnitude
    is therefore the absolute departure for a signed latent and the level
    itself for one that cannot be negative (task §22; the transformation is
    stated rather than clipped after the fact).
    """
    dynamics = world.latent(latent)
    magnitude = abs(value) if dynamics.family == "ar1" else max(0.0, value)
    return magnitude / dynamics.severity_reference


class _WaferGeometry:
    """The disc a defect must land on, from the product's own wafer size."""

    def __init__(self, wafer_size_mm: int) -> None:
        self.radius = wafer_size_mm / 2.0

    def clamp(self, x: float, y: float) -> tuple[float, float]:
        """Pull a point back onto the wafer along its own radius.

        Jitter can push a ring or a cluster past the edge; a defect off the
        wafer is not a defect. Scaling back along the radius keeps the angle,
        so the clamp cannot manufacture a preferred direction.
        """
        distance = math.hypot(x, y)
        if distance <= self.radius or distance == 0.0:
            return x, y
        scale = self.radius / distance
        return x * scale, y * scale


def _place(rng: Any, origin: str, policy: DefectPolicy,
           geometry: _WaferGeometry,
           seed: tuple[float, float, float] | None) -> tuple[float, float]:
    """Coordinates for one defect, from its origin's own geometry (§4.3).

    Nothing here writes a zone or a label: the shape *is* the signature, and
    an analyst reading `(x, y)` recovers it without being told. `seed` is the
    clump a clustered origin belongs to — a point and a direction, shared by
    every defect of that clump.
    """
    radius = geometry.radius
    angle = rng.random() * 2.0 * math.pi

    if origin == "edge_ring":
        # An annulus with radial jitter, so the ring overlaps the background
        # rather than partitioning the wafer.
        base = (policy.edge_inner_fraction
                + policy.edge_width_fraction * rng.random())
        jitter = rng.gauss(0.0, policy.edge_jitter_fraction)
        distance = max(0.0, base + jitter) * radius
        return geometry.clamp(distance * math.cos(angle),
                              distance * math.sin(angle))

    if origin == "center":
        sigma = policy.center_sigma_fraction * radius
        return geometry.clamp(rng.gauss(0.0, sigma), rng.gauss(0.0, sigma))

    if origin == "particle_cluster" and seed is not None:
        spread = policy.cluster_radius_mm / 2.0
        return geometry.clamp(seed[0] + rng.gauss(0.0, spread),
                              seed[1] + rng.gauss(0.0, spread))

    if origin == "scratch" and seed is not None:
        # A stroke, not a cloud: defects lie along the seed's direction with a
        # little scatter across it.
        origin_x, origin_y, direction = seed
        length = policy.scratch_length_fraction * radius
        along = (rng.random() - 0.5) * 2.0 * length
        across = rng.gauss(0.0, policy.scratch_jitter_mm)
        return geometry.clamp(
            origin_x + along * math.cos(direction)
            - across * math.sin(direction),
            origin_y + along * math.sin(direction)
            + across * math.cos(direction))

    # Uniform over the disc: √u keeps it uniform per unit *area* rather than
    # per unit radius, which would pile everything at the centre.
    distance = radius * math.sqrt(rng.random())
    return geometry.clamp(distance * math.cos(angle),
                          distance * math.sin(angle))


def _classify(rng: Any, row: Sequence[tuple[str, float]]) -> str:
    """Draw an observable class from a hidden origin's confusion row.

    The one place the two planes touch, and it is a *draw*: the reported class
    is wrong a configured fraction of the time, which is what stops a
    classified type from being a restatement of the physics that made it.
    """
    threshold = rng.random()
    cumulative = 0.0
    for label, probability in row:
        cumulative += probability
        if threshold < cumulative:
            return label
    return row[-1][0]


def _intensities(world: World, realization: Realization,
                 covered: Sequence[Run], defect_scale: float
                 ) -> list[tuple[str, float, int, int]]:
    """Per-origin intensity, and which covered run drove the latent part.

    An inspection sees the wafer, not one step of it: a defect found after
    metal could have come from any of the steps that layer covers. Every
    covered run's chamber contributes according to the latents it was carrying
    while it ran, which is what makes "which chamber did this?" a question
    with evidence rather than a lookup.
    """
    grid = realization.grid
    rows: list[tuple[str, float, int, int]] = []
    for policy in world.defects.origins:
        base = policy.base_rate * defect_scale
        rows.append((policy.origin, base, -1, -1))
        for latent, sensitivity in policy.sensitivities:
            if sensitivity == 0.0:
                continue
            for run in covered:
                trajectory = realization.trajectory(run.chamber_id, latent)
                magnitude = _propensity(
                    world, latent,
                    _latent_mean(trajectory, grid, run.start_min, run.end_min))
                added = sensitivity * magnitude
                if added > 0.0:
                    rows.append((policy.origin, added, run.chamber_id,
                                 run.flow_step_id))
    return rows


def _pick(rng: Any, components: Sequence[tuple[str, float, int, int]],
          total: float) -> tuple[str, int, int]:
    """Apportion one defect to a component by its share of the intensity."""
    threshold = rng.random() * total
    cumulative = 0.0
    for component in components:
        cumulative += component[1]
        if threshold < cumulative:
            return component[0], component[2], component[3]
    last = components[-1]
    return last[0], last[2], last[3]


# ------------------------------------------------------------------ front door


def inspect(timeline: Timeline, realization: Realization) -> DefectPopulation:
    """Scan every wafer that reached an inspection, and report what was found.

    Takes latent trajectories and the schedule, and nothing else from the
    hidden plane: the mechanism records, the distractors and the counterfactual
    series are never read.
    """
    world = timeline.world
    rngs = Substreams(timeline.seed)
    policy = world.defects
    classifier = world.observation.classifier
    confusion = dict(classifier.confusion)

    inspections: list[Inspection] = []
    defects: list[Defect] = []
    origins: list[DefectOrigin] = []
    runs_of_wafer = {wafer.wafer_id: timeline.runs_of_wafer(wafer.wafer_id)
                     for wafer in timeline.wafers}

    for run in timeline.runs:
        step: ProcessStep = world.step(run.step_id)
        if not step.is_inspection:
            continue
        covered_ids = {covered.step_id for covered in world.covered_steps(
            step.step_id)}
        covered = [earlier for earlier in runs_of_wafer[run.wafer_id]
                   if earlier.step_id in covered_ids
                   and earlier.end_min <= run.start_min]
        if len(covered) != len(covered_ids):
            # The wafer reached the scanner without having had everything the
            # scan covers. A fab cannot report on a step that never ran.
            continue

        product = world.product(timeline.lot(run.lot_id).product_id)
        geometry = _WaferGeometry(product.wafer_size_mm)
        area = dict(step.settings)["scan_area_mm2"]
        threshold_um = dict(step.settings)["sensitivity_threshold_um"]

        components = _intensities(world, realization, covered,
                                  product.defect_scale)
        total = sum(rate for _o, rate, _c, _f in components)

        count = poisson(rngs.stream("defect.count", run.run_id), total * area)
        origin_rng = rngs.stream("defect.origin", run.run_id)
        location_rng = rngs.stream("defect.location", run.run_id)
        size_rng = rngs.stream("defect.size", run.run_id)
        class_rng = rngs.stream("defect.classification", run.run_id)
        cluster_rng = rngs.stream("defect.cluster", run.run_id)

        inspection_id = len(inspections) + 1
        seeds: dict[str, tuple[float, float, float]] = {}
        remaining: dict[str, int] = {}
        reported: list[Defect] = []

        for _ in range(count):
            origin, chamber_id, flow_step_id = _pick(origin_rng, components,
                                                     total)
            seed = None
            if origin in ("particle_cluster", "scratch"):
                # Clumped origins share a seed until the clump is spent, which
                # is what makes a cluster a cluster rather than a rate.
                if remaining.get(origin, 0) <= 0:
                    distance = geometry.radius * math.sqrt(
                        cluster_rng.random())
                    angle = cluster_rng.random() * 2.0 * math.pi
                    seeds[origin] = (distance * math.cos(angle),
                                     distance * math.sin(angle),
                                     cluster_rng.random() * 2.0 * math.pi)
                    remaining[origin] = max(1, poisson(
                        cluster_rng, policy.cluster_mean_defects))
                remaining[origin] -= 1
                seed = seeds[origin]

            x_mm, y_mm = _place(location_rng, origin, policy, geometry, seed)
            size_um = math.exp(size_rng.gauss(
                math.log(policy.size_median_um), policy.size_log_sigma))
            classified = _classify(class_rng, confusion[origin])
            if size_um < threshold_um:
                # Below what this scanner can see. Drawn first so the
                # inspection's own sensitivity thins the population rather
                # than reshaping which origins survive it.
                continue

            defect_id = len(defects) + len(reported) + 1
            reported.append(Defect(
                defect_id=defect_id, inspection_id=inspection_id,
                wafer_id=run.wafer_id, x_mm=x_mm, y_mm=y_mm,
                size_um=size_um, classified_type=classified,
                layer=step.layer or ""))
            origins.append(DefectOrigin(
                defect_id=defect_id, origin=origin,
                contributing_chamber_id=chamber_id,
                contributing_flow_step_id=flow_step_id))

        defects.extend(reported)
        inspections.append(Inspection(
            inspection_id=inspection_id, wafer_id=run.wafer_id,
            flow_step_id=run.flow_step_id, inspection_tool_id=run.tool_id,
            inspection_time_min=run.end_min,
            total_defect_count=len(reported), scan_area_mm2=area))

    return DefectPopulation(model=DEFECT_MODEL,
                            inspections=tuple(inspections),
                            defects=tuple(defects),
                            origins=tuple(origins))


def inspect_response(response: Any) -> DefectPopulation:
    """Scan the fab a `fabsim.response.FabResponse` describes."""
    return inspect(response.timeline, response.realization)
