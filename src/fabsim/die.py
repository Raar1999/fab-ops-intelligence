"""
die.py — the die grid and the yield plane: what the tester found.

The last physical plane of Phase 1, and the one the audit's central defect
lived in. In v1 a wafer's yield was a number with a fault term subtracted from
it (`−0.08 if bad_tool`). Here yield is not computed at all: a grid of real
die is laid out on a real wafer, each one lives or dies for reasons that are
local to it, and the yield is what is left::

    product geometry ──▶ die lattice on a disc ──▶ eligible die
                                                        │
    process observations (3C) ──▶ local |metric − target| at this die's radius
                                                        │  parametric risk
    reported defects (3D) ──▶ which die a defect physically reached
                                                        │  defect risk
    product killer density ─────────────────────────────┤  background risk
                                                        ▼
                              P(dead) = 1 − Π (1 − pᵢ)
                                                        │  one Bernoulli
                                                        ▼
                              die bin ◀── tester symptom draw
                                                        │
                                                        ▼
                              wafer yield = good ÷ eligible

**This module cannot see a fault, and it cannot see the hidden plane at all.**
It is handed a timeline, the process observations and the defect population —
three *observable* collections — and nothing else. There is no `Realization`
parameter, so latent state, mechanism records, distractor records, the
counterfactual series and the hidden defect origin are not merely unread: they
are unreachable. ADR-004's "no term reads fault identity" is therefore a
property of this function's signature rather than of its discipline, and the
audited direct chain has nowhere to be written even by accident.

**The three risks are competing, not additive** (`CAUSAL_MECHANISM_MODEL.md`
§5). A die that a defect reached *and* that sat outside its parametric window
is one dead die, not two: the survival probabilities multiply, and exactly one
Bernoulli decides. Which risk to attribute it to — needed only to draw a bin —
is apportioned by each risk's share of the total hazard, which is the standard
competing-risks decomposition and not a fourth model.

**The edge signature is geometry, not a coefficient.** Nothing here knows that
`edge_uniformity` exists. A die's parametric risk is read from the wafer's own
measured radial profile *at that die's radius*, and a die's defect risk is read
from the defects whose coordinates fall on it. A chamber that etches its edge
badly moves `cd_nm_edge` and seeds an edge ring; the die at r ≈ R then die more
often because that is where the numbers are, and the die at the centre do not.
The audited `−0.03 if edge` term has no successor either.

**What a bin is.** A dead die's bin code is *drawn* through a symptom
distribution conditioned on how it died, so a defect kill is usually — not
always — OPEN_SHORT. Bins are evidence a diagnosis engine has to weigh, not the
kill model's answer spelled out. The cause itself lives in a separate hidden
record, exactly as a defect's origin does, and the observable `DieBin` has no
field for it.

What this module does **not** contain: any emitter, any file, any database,
any truth artifact, any benchmark, and any concept downstream of a wafer's
yield. The chain stops here.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from fabsim.defects import Defect, DefectPopulation
from fabsim.observation import Metrology, ProcessObservations, zone_radius
from fabsim.rng import Substreams
from fabsim.timeline import Timeline
from fabsim.world import (
    DIE_KILL_CAUSES,
    TEST_OPERATION,
    DieGridPolicy,
    Product,
    World,
)

__all__ = [
    "COVERAGE_INSIDE",
    "COVERAGE_OUTSIDE",
    "COVERAGE_PARTIAL",
    "DIE_MODEL",
    "Die",
    "DieBin",
    "DieGrid",
    "DieOutcome",
    "DiePopulation",
    "WaferYield",
    "die_grid",
    "probe",
    "probe_response",
]

#: Versioned name of the die/yield model, like every plane before it: a change
#: to how a die lives or dies is a visible version bump rather than a silent
#: reshuffle of every dataset ever generated.
DIE_MODEL = "fabsim.die/v1"

#: How much of a die's own footprint lies within the usable wafer. The
#: vocabulary exists so that "partial" is a *state a die is in* rather than an
#: implicit consequence of a comparison written somewhere in a loop.
COVERAGE_INSIDE = "inside"
COVERAGE_PARTIAL = "partial"
COVERAGE_OUTSIDE = "outside"

#: The one geometric tolerance in this module, in millimetres, and the only
#: place a floating-point comparison is softened. A die corner exactly on the
#: exclusion boundary is *inside*: the alternative is that whether a lattice
#: lands on 149.9999999 or 150.0000001 decides a die, which is arithmetic
#: deciding physics. It is ~10⁻⁴ of a street and ~10⁻⁷ of a die, so no die
#: whose status is not already a coin-flip can be moved by it.
_GEOMETRY_EPSILON_MM = 1e-9

#: `1/√2`, for the Gaussian tail below.
_INV_SQRT2 = 1.0 / math.sqrt(2.0)


def _normal_cdf(z: float) -> float:
    """Φ(z), from `math.erfc` so nothing is approximated by a series here."""
    return 0.5 * math.erfc(-z * _INV_SQRT2)


# ------------------------------------------------------------------ geometry


@dataclass(frozen=True)
class Die:
    """One cell of one product's die lattice. Geometry only — no wafer.

    The lattice is a property of the *product* (its wafer size and die area)
    and of the world's `die_grid` policy, so it is identical on every wafer of
    that product and is computed once. A `Die` therefore has no `wafer_id`:
    what varies per wafer is the outcome, not the geometry.
    """

    index: int
    #: Column and row of the lattice; `column` increases with x, `row`
    #: increases *downwards* from the top of the wafer, which is how a wafer
    #: map is read and what `die_bins.die_x` / `die_y` record.
    column: int
    row: int
    #: Centre of the die footprint, in wafer coordinates with the origin at
    #: the wafer centre.
    x_mm: float
    y_mm: float
    #: The footprint itself — the die, *without* the scribe lane around it.
    width_mm: float
    height_mm: float
    coverage: str
    #: Whether the partial-die policy admits this die to the grid.
    eligible: bool

    @property
    def radius_mm(self) -> float:
        """Distance of the die centre from the wafer centre."""
        return math.hypot(self.x_mm, self.y_mm)

    def contains(self, x_mm: float, y_mm: float, margin_mm: float = 0.0
                 ) -> bool:
        """Whether a point lies on this die, allowing a reach of `margin_mm`."""
        return (abs(x_mm - self.x_mm) <= self.width_mm / 2.0 + margin_mm
                and abs(y_mm - self.y_mm) <= self.height_mm / 2.0 + margin_mm)


@dataclass(frozen=True)
class DieGrid:
    """One product's die lattice on its own wafer.

    Everything about it is a pure function of declared geometry — the
    product's `wafer_size_mm` and `die_size_mm2`, and the world's edge
    exclusion, street width, aspect ratio and conventions. There is no RNG in
    this class and no seed reaches it: two builds of the same product produce
    the identical lattice, which is what lets `die_bins.die_x/die_y` mean the
    same physical place in every dataset.

    The conventions, stated once (`SCHEMA_V2_DESIGN.md` §2.22, `die_grid`):

    * **`wafer_center` origin.** Coordinates have (0, 0) at the wafer centre,
      and the lattice is centred on it — cell centres sit at
      `(i − (n−1)/2) · pitch`, which is symmetric under a half-turn for an odd
      and an even column count alike. No product gets a lattice phase of its
      own.
    * **Pitch is die plus street.** The footprint is `√(A·aspect) × √(A/aspect)`
      and the lattice steps by that plus `street_width_mm` in each direction,
      so the scribe lane is real space between real die — which is why a
      defect can land in it and kill nothing.
    * **`row_major` index order.** `index = row · columns + column`, rows
      numbered from the top of the wafer downwards and columns from the left,
      the way a wafer map is read.
    * **Edge exclusion applies to the footprint, not the centre.** A die is
      `inside` only if all four of *its own corners* clear the usable radius;
      it is `outside` if no part of it reaches inside; anything else is
      `partial`. A rule on the centre would admit die hanging over the edge.
    """

    product_id: int
    wafer_radius_mm: float
    usable_radius_mm: float
    die_width_mm: float
    die_height_mm: float
    pitch_x_mm: float
    pitch_y_mm: float
    columns: int
    rows: int
    partial_die_policy: str
    dies: tuple[Die, ...]

    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index["eligible"] = tuple(d for d in self.dies if d.eligible)
        self._index["by_cell"] = {(d.column, d.row): d for d in self.dies}

    @property
    def eligible(self) -> tuple[Die, ...]:
        """The die the partial-die policy admits, in index order."""
        return self._index["eligible"]

    def at(self, column: int, row: int) -> Die | None:
        return self._index["by_cell"].get((column, row))

    def cell_of(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        """Lattice cell a wafer coordinate falls in, clamped to the lattice.

        The inverse of the centring rule above. Used to look up the handful of
        cells a defect could possibly touch instead of walking every die.
        """
        column = int(math.floor(x_mm / self.pitch_x_mm
                                + self.columns / 2.0))
        row = int(math.floor(-y_mm / self.pitch_y_mm + self.rows / 2.0))
        return (min(max(column, 0), self.columns - 1),
                min(max(row, 0), self.rows - 1))

    def count(self, coverage: str) -> int:
        return sum(1 for d in self.dies if d.coverage == coverage)


def _coverage(x_mm: float, y_mm: float, half_w: float, half_h: float,
              usable_radius: float) -> str:
    """Where one die footprint sits relative to the usable wafer.

    Measured from the rectangle, not from its centre: the farthest corner
    decides `inside`, and the nearest point of the rectangle decides whether
    the die reaches the usable area at all.
    """
    far = math.hypot(abs(x_mm) + half_w, abs(y_mm) + half_h)
    if far <= usable_radius + _GEOMETRY_EPSILON_MM:
        return COVERAGE_INSIDE
    near_x = max(0.0, abs(x_mm) - half_w)
    near_y = max(0.0, abs(y_mm) - half_h)
    if math.hypot(near_x, near_y) >= usable_radius - _GEOMETRY_EPSILON_MM:
        return COVERAGE_OUTSIDE
    return COVERAGE_PARTIAL


def _admits(policy: str, coverage: str) -> bool:
    """Whether the declared partial-die policy admits this coverage.

    `PARTIAL_DIE_POLICIES` is a closed, versioned vocabulary with one member
    today. Dispatching on it explicitly — and refusing a value that is not in
    it — is the difference between implementing the contract and happening to
    agree with it: when a second policy is declared, this is the one function
    that has to answer for it.
    """
    if coverage == COVERAGE_OUTSIDE:
        return False
    if policy == "exclude":
        return coverage == COVERAGE_INSIDE
    raise ValueError(f"unknown partial die policy {policy!r}")


def die_grid(policy: DieGridPolicy, product: Product) -> DieGrid:
    """Lay out one product's die lattice. Deterministic; no seed, no RNG."""
    wafer_radius = product.wafer_size_mm / 2.0
    usable_radius = wafer_radius - policy.edge_exclusion_mm
    width = math.sqrt(product.die_size_mm2 * policy.die_aspect_ratio)
    height = math.sqrt(product.die_size_mm2 / policy.die_aspect_ratio)
    pitch_x = width + policy.street_width_mm
    pitch_y = height + policy.street_width_mm

    # Enough cells to cover the whole wafer: a stepper prints past the usable
    # area and edge exclusion is what takes those die away, so the lattice is
    # not clipped before the policy has had its say.
    columns = max(1, int(math.ceil(2.0 * wafer_radius / pitch_x)))
    rows = max(1, int(math.ceil(2.0 * wafer_radius / pitch_y)))

    dies: list[Die] = []
    for row in range(rows):
        for column in range(columns):
            x_mm = (column - (columns - 1) / 2.0) * pitch_x
            y_mm = ((rows - 1) / 2.0 - row) * pitch_y
            coverage = _coverage(x_mm, y_mm, width / 2.0, height / 2.0,
                                 usable_radius)
            dies.append(Die(
                index=row * columns + column, column=column, row=row,
                x_mm=x_mm, y_mm=y_mm, width_mm=width, height_mm=height,
                coverage=coverage,
                eligible=_admits(policy.partial_die_policy, coverage)))

    return DieGrid(
        product_id=product.product_id, wafer_radius_mm=wafer_radius,
        usable_radius_mm=usable_radius, die_width_mm=width,
        die_height_mm=height, pitch_x_mm=pitch_x, pitch_y_mm=pitch_y,
        columns=columns, rows=rows,
        partial_die_policy=policy.partial_die_policy, dies=tuple(dies))


# ----------------------------------------------------------- observable rows


@dataclass(frozen=True)
class DieBin:
    """One tested die (`SCHEMA_V2_DESIGN.md` §2.22).

    Position and a bin code, and nothing else. There is no field for why the
    die failed, no killer flag and no cause — a tester records a symptom, and
    a field that existed here is a field a later emitter could fill from the
    hidden plane without noticing.
    """

    wafer_id: int
    die_x: int
    die_y: int
    bin_code: str


@dataclass(frozen=True)
class WaferYield:
    """One wafer's final-test summary (`SCHEMA_V2_DESIGN.md` §2.21).

    `total_die` is the count of `die_bins` rows for this wafer and `good_die`
    the count of passing ones, so §4.3's reconciliation holds by construction
    rather than by a check afterwards. `yield_pct` is the quotient — it is not
    a modelled quantity and nothing anywhere adjusts it.
    """

    yield_id: int
    wafer_id: int
    lot_id: int
    total_die: int
    good_die: int
    yield_pct: float
    test_time_min: int


# ---------------------------------------------------------------- hidden side


@dataclass(frozen=True)
class DieOutcome:
    """Why one die died, and what each risk contributed. Hidden.

    Answer-key material for a later truth emitter and the instrument the
    mediation tests measure with. It is a separate collection for the same
    reason `DefectOrigin` is: the observable row has no field for a cause, so
    there is nothing for an emitter to leak by accident.
    """

    wafer_id: int
    die_index: int
    #: A member of `DIE_KILL_CAUSES`, or `None` on a die that passed.
    cause: str | None
    p_background: float
    p_defect: float
    p_parametric: float
    #: Reported defects whose footprint reached this die.
    defect_ids: tuple[int, ...]


# ------------------------------------------------------------- the population


@dataclass(frozen=True)
class DiePopulation:
    """One dataset's die plane, with the two sides named apart.

    `grids`, `die_bins` and `wafer_yield` are observable-shaped; `outcomes` is
    the hidden record and is never emitted. The grid is observable because it
    is *geometry* — a fab knows its own reticle layout — and because
    `die_bins` coordinates are meaningless without it.
    """

    model: str
    grids: tuple[DieGrid, ...]
    die_bins: tuple[DieBin, ...]
    wafer_yield: tuple[WaferYield, ...]
    outcomes: tuple[DieOutcome, ...]

    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index["grid"] = {g.product_id: g for g in self.grids}
        by_wafer_bins: dict[int, list[DieBin]] = {}
        for row in self.die_bins:
            by_wafer_bins.setdefault(row.wafer_id, []).append(row)
        self._index["bins"] = {k: tuple(v)
                               for k, v in by_wafer_bins.items()}
        self._index["yield"] = {y.wafer_id: y for y in self.wafer_yield}
        by_wafer_out: dict[int, list[DieOutcome]] = {}
        for outcome in self.outcomes:
            by_wafer_out.setdefault(outcome.wafer_id, []).append(outcome)
        self._index["outcomes"] = {k: tuple(v)
                                   for k, v in by_wafer_out.items()}

    def grid_of_product(self, product_id: int) -> DieGrid:
        return self._index["grid"][product_id]

    def bins_of(self, wafer_id: int) -> tuple[DieBin, ...]:
        return self._index["bins"].get(wafer_id, ())

    def yield_of(self, wafer_id: int) -> WaferYield | None:
        return self._index["yield"].get(wafer_id)

    def outcomes_of(self, wafer_id: int) -> tuple[DieOutcome, ...]:
        """Hidden per-die record of one wafer."""
        return self._index["outcomes"].get(wafer_id, ())

    def content_sha256(self) -> str:
        """Digest of the whole die plane; equal iff two testers agreed."""
        digest = hashlib.sha256()
        digest.update(f"{self.model}\n".encode("ascii"))
        for grid in self.grids:
            digest.update(
                f"grid\t{grid.product_id}\t{grid.columns}\t{grid.rows}\t"
                f"{len(grid.eligible)}\t{grid.die_width_mm!r}\t"
                f"{grid.die_height_mm!r}\n".encode("utf-8"))
        for row in self.die_bins:
            digest.update(
                f"bin\t{row.wafer_id}\t{row.die_x}\t{row.die_y}\t"
                f"{row.bin_code}\n".encode("utf-8"))
        for summary in self.wafer_yield:
            digest.update(
                f"yield\t{summary.yield_id}\t{summary.wafer_id}\t"
                f"{summary.lot_id}\t{summary.total_die}\t"
                f"{summary.good_die}\t{summary.yield_pct!r}\t"
                f"{summary.test_time_min}\n".encode("utf-8"))
        return digest.hexdigest()


# ------------------------------------------------------------------ the model


def _radial_profile(rows: Iterable[Metrology], zones: Sequence[str]
                    ) -> dict[tuple[int, str], list[float | None]]:
    """Zone readings of one wafer, keyed by (measured flow step, channel).

    3C emits one row per zone plus a `_sigma` summary; the summary is derived
    from the zones and is dropped here rather than treated as a fourth radial
    position. A key with a missing zone is discarded by the caller — a
    profile with a hole cannot be interpolated, and guessing the hole would be
    inventing a measurement.
    """
    by_key: dict[tuple[int, str], list[float | None]] = {}
    positions = {zone: index for index, zone in enumerate(zones)}
    for row in rows:
        channel, _, zone = row.param_name.rpartition("_")
        position = positions.get(zone)
        if position is None:
            continue
        key = (row.flow_step_id, channel)
        readings = by_key.setdefault(key, [None] * len(zones))
        readings[position] = row.value
    return by_key


def _interpolate(readings: Sequence[float], radial: float) -> float:
    """The wafer's own profile, read at one die's normalized radius.

    Linear between adjacent zones, which is the same convention 3C *generated*
    them with (`zone_radius`: the zone list is centre-outward and evenly
    spaced). Reading them back the way they were written is what makes this an
    inverse rather than a second, incompatible radial model.
    """
    count = len(readings)
    if count == 1:
        return readings[0]
    position = min(max(radial, 0.0), 1.0) * (count - 1)
    lower = min(int(position), count - 2)
    weight = position - lower
    return readings[lower] * (1.0 - weight) + readings[lower + 1] * weight


class _WaferRisk:
    """The three risks, for one wafer, evaluated per die.

    Built once per wafer so that the wafer-level work — resolving the radial
    profiles, bucketing the defects into lattice cells — happens once instead
    of once per die.
    """

    def __init__(self, world: World, grid: DieGrid, product: Product,
                 metrology: Sequence[Metrology], defects: Sequence[Defect],
                 flow_step_to_step: Mapping[int, int],
                 density: float) -> None:
        self.world = world
        self.grid = grid
        self.policy = world.die_kill

        # -- background: this wafer's own killer density over the die area.
        # The classical Poisson yield model, per die: a bigger die catches
        # more of the same density.
        area = grid.die_width_mm * grid.die_height_mm
        self.p_background = 1.0 - math.exp(-density * area)

        # -- parametric: one (limit, scatter, readings) per measured metric.
        self.profiles: list[tuple[float, float, tuple[float, ...]]] = []
        zones = world.observation.wafer_zones
        for (flow_step_id, channel), readings in sorted(
                _radial_profile(metrology, zones).items()):
            if any(value is None for value in readings):
                continue
            step_id = flow_step_to_step.get(flow_step_id)
            if step_id is None:
                continue
            recipe = world.recipe_for(step_id, product.product_id)
            if (recipe.metric_name != channel or recipe.metric_target is None
                    or recipe.metric_usl is None):
                continue
            tolerance = recipe.metric_usl - recipe.metric_target
            if tolerance <= 0.0:
                continue
            limit = tolerance * self.policy.parametric_kill_limit_tolerances
            scatter = limit * self.policy.parametric_within_die_sigma
            self.profiles.append((limit, scatter, tuple(
                value - recipe.metric_target for value in readings)))

        # -- defects: bucketed into the lattice cells they could reach.
        self.by_cell: dict[tuple[int, int], list[tuple[Defect, float]]] = {}
        halo_mm = self.policy.defect_halo_um / 1000.0
        for defect in defects:
            reach = defect.size_um / 2000.0 + halo_mm
            for cell in self._cells_near(defect.x_mm, defect.y_mm, reach):
                self.by_cell.setdefault(cell, []).append((defect, reach))

    def _cells_near(self, x_mm: float, y_mm: float, reach: float
                    ) -> set[tuple[int, int]]:
        """Lattice cells a point of this reach could touch.

        A reported defect is microns across and a die is millimetres, so this
        is one cell almost always and at most four when a defect lands in a
        street corner. It is what keeps the plane linear in defects instead of
        quadratic in defects × die.
        """
        return {self.grid.cell_of(x, y)
                for x in (x_mm - reach, x_mm + reach)
                for y in (y_mm - reach, y_mm + reach)}

    def parametric(self, die: Die) -> float:
        """P(this die misses a functional limit), from the wafer's profile.

        Two-sided: a die fails if its own parameter — the wafer's local value
        plus within-die scatter — lands outside `±limit`. The result is smooth
        and monotone in the local departure, is 0 at no departure only in the
        limit, and never leaves [0, 1]. Every metric the wafer was measured on
        is a separate way to fail, so they compose as survivals.

        Nothing in it knows which latent moved the profile, or that a latent
        exists. It reads a number the fab measured, at a radius the die
        happens to sit at.
        """
        if not self.profiles:
            return 0.0
        radial = (die.radius_mm / self.grid.wafer_radius_mm
                  if self.grid.wafer_radius_mm > 0.0 else 0.0)
        survival = 1.0
        for limit, scatter, deviations in self.profiles:
            local = _interpolate(deviations, radial)
            probability = (_normal_cdf((local - limit) / scatter)
                           + _normal_cdf((-limit - local) / scatter))
            survival *= 1.0 - min(1.0, max(0.0, probability))
        return 1.0 - survival

    def defect(self, die: Die) -> tuple[float, tuple[int, ...]]:
        """P(a reported defect killed this die), and which ones reached it.

        A defect reaches a die when its own footprint — its radius, plus the
        declared halo that stands for "on *or near*" — overlaps the die's.
        A defect that landed in a scribe lane and stayed there reaches nothing,
        which is why the street is modelled as real space.

        Given that it reached, whether it kills rises with its cross-section
        (`s²/(s² + half²)`) and is weighted by the layer it was reported at.
        Size and layer are fields on the observable defect row; the hidden
        origin is not consulted, and is not reachable from here.
        """
        candidates = self.by_cell.get((die.column, die.row))
        if not candidates:
            return 0.0, ()
        half = self.policy.defect_half_kill_size_um
        survival = 1.0
        reached: list[int] = []
        for defect, reach in candidates:
            if not die.contains(defect.x_mm, defect.y_mm, reach):
                continue
            reached.append(defect.defect_id)
            size = defect.size_um
            probability = (self.policy.layer_weight(defect.layer)
                           * size * size / (size * size + half * half))
            survival *= 1.0 - min(1.0, max(0.0, probability))
        return 1.0 - survival, tuple(sorted(reached))


def _attribute(rng: Any, risks: Sequence[tuple[str, float]]) -> str:
    """Which of the competing risks took a die that died.

    The standard decomposition: a survival `Π(1 − pᵢ)` is `exp(−Σ hᵢ)` with
    `hᵢ = −ln(1 − pᵢ)`, so each risk's share of the total hazard is its share
    of the deaths. This is bookkeeping over the probabilities that already
    decided the outcome — it introduces no fourth model and cannot change
    whether the die died, only what the tester will call it.
    """
    hazards = [(cause, -math.log(max(1e-300, 1.0 - probability)))
               for cause, probability in risks]
    total = sum(hazard for _cause, hazard in hazards)
    if total <= 0.0:
        return hazards[0][0]
    threshold = rng.random() * total
    cumulative = 0.0
    for cause, hazard in hazards:
        cumulative += hazard
        if threshold < cumulative:
            return cause
    return hazards[-1][0]


def _killer_density(rngs: Substreams, policy: Any, product: Product,
                    lot_id: int, wafer_id: int) -> float:
    """This wafer's own background killer density, about its product's mean.

    Background defectivity is not a constant a product carries forever: it
    moves with the lot and with the individual wafer, the way every other
    benign term in this simulator does (`CAUSAL_MECHANISM_MODEL.md` §3). The
    draw is lognormal so a density stays positive, and the `−σ²/2` correction
    keeps the *mean* density equal to the product's declared one, so widening
    the spread does not quietly move the fab's average yield.

    It is keyed by lot and wafer and by nothing else. No chamber, no latent,
    no measurement and no defect reaches it — this is benign structure, and a
    diagnosis engine that mistakes a dirty lot for a bad chamber is making the
    mistake the null world is there to punish.
    """
    lot_sigma = policy.background_lot_log_sigma
    wafer_sigma = policy.background_wafer_log_sigma
    lot_rng = rngs.stream("die.defectivity.lot", product.product_name, lot_id)
    wafer_rng = rngs.stream("die.defectivity.wafer", wafer_id)
    exponent = (lot_sigma * lot_rng.gauss(0.0, 1.0)
                + wafer_sigma * wafer_rng.gauss(0.0, 1.0)
                - 0.5 * (lot_sigma ** 2 + wafer_sigma ** 2))
    return product.killer_density_per_mm2 * math.exp(exponent)


def _draw_bin(rng: Any, row: Sequence[tuple[str, float]]) -> str:
    """A tester's symptom code for a die that died of a given cause."""
    threshold = rng.random()
    cumulative = 0.0
    for code, probability in row:
        cumulative += probability
        if threshold < cumulative:
            return code
    return row[-1][0]


# ------------------------------------------------------------------ front door


def probe(timeline: Timeline, observations: ProcessObservations,
          population: DefectPopulation) -> DiePopulation:
    """Test every wafer that reached the tester, and report what it found.

    Wafer probe: the die grid is resolved, each die faces the three competing
    risks, one Bernoulli decides, and the yield is the count of survivors.

    The parameters are the whole of the argument for ADR-004 here. A timeline,
    the measurements and the reported defects are three *observable*
    collections; the hidden `Realization` is not among them, so there is no
    latent value, no mechanism record and no defect origin within reach of
    this function. A fault reaches a die only by having moved a number the fab
    measured or by having put a defect somewhere a die was.
    """
    world = timeline.world
    rngs = Substreams(timeline.seed)
    tester = world.die_kill.tester

    grids = {product.product_id: die_grid(world.die_grid, product)
             for product in world.products}
    flow_step_to_step = {fs.flow_step_id: fs.step_id
                         for fs in world.flow_steps}
    defects_of_wafer: dict[int, list[Defect]] = {}
    for defect in population.defects:
        defects_of_wafer.setdefault(defect.wafer_id, []).append(defect)
    test_run_of_wafer = {
        run.wafer_id: run for run in timeline.runs
        if world.step(run.step_id).operation_type == TEST_OPERATION}

    die_bins: list[DieBin] = []
    summaries: list[WaferYield] = []
    outcomes: list[DieOutcome] = []

    for wafer in timeline.wafers:
        run = test_run_of_wafer.get(wafer.wafer_id)
        if run is None:
            # The wafer never reached the tester inside the horizon. A fab
            # cannot report a yield for a wafer it has not finished.
            continue
        lot = timeline.lot(wafer.lot_id)
        product = world.product(lot.product_id)
        grid = grids[product.product_id]
        risk = _WaferRisk(world, grid, product,
                          observations.of_wafer(wafer.wafer_id),
                          defects_of_wafer.get(wafer.wafer_id, ()),
                          flow_step_to_step,
                          _killer_density(rngs, world.die_kill, product,
                                          wafer.lot_id, wafer.wafer_id))

        kill_rng = rngs.stream("die.kill", wafer.wafer_id)
        bin_rng = rngs.stream("die.bin", wafer.wafer_id)
        good = 0
        for die in grid.eligible:
            p_defect, reached = risk.defect(die)
            p_parametric = risk.parametric(die)
            # Competing risks: one die, one death. The survivals multiply and
            # a single draw decides, so a die that two risks reached is one
            # dead die rather than two.
            dead_probability = 1.0 - ((1.0 - risk.p_background)
                                      * (1.0 - p_defect)
                                      * (1.0 - p_parametric))
            dead = kill_rng.random() < dead_probability
            cause = None
            code = tester.pass_code
            if dead:
                cause = _attribute(bin_rng, list(zip(
                    DIE_KILL_CAUSES,
                    (risk.p_background, p_defect, p_parametric))))
                code = _draw_bin(bin_rng, tester.row(cause))
            else:
                good += 1
            die_bins.append(DieBin(wafer_id=wafer.wafer_id, die_x=die.column,
                                   die_y=die.row, bin_code=code))
            outcomes.append(DieOutcome(
                wafer_id=wafer.wafer_id, die_index=die.index, cause=cause,
                p_background=risk.p_background, p_defect=p_defect,
                p_parametric=p_parametric, defect_ids=reached))

        total = len(grid.eligible)
        summaries.append(WaferYield(
            yield_id=len(summaries) + 1, wafer_id=wafer.wafer_id,
            lot_id=wafer.lot_id, total_die=total, good_die=good,
            yield_pct=(100.0 * good / total) if total else 0.0,
            test_time_min=run.end_min))

    return DiePopulation(
        model=DIE_MODEL,
        grids=tuple(grids[product.product_id] for product in world.products),
        die_bins=tuple(die_bins), wafer_yield=tuple(summaries),
        outcomes=tuple(outcomes))


def probe_response(response: Any, observations: ProcessObservations,
                   population: DefectPopulation) -> DiePopulation:
    """Test the fab a `fabsim.response.FabResponse` describes.

    The measurements and the defects are passed in rather than recomputed:
    they are what the tester's wafers actually saw, and producing a second
    realization of them here would let the die plane disagree with the data
    the same dataset reports.
    """
    return probe(response.timeline, observations, population)
