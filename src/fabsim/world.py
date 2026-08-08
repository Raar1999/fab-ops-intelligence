"""
world.py — the static fab world: entities, their relationships, and the
`fabsim.world/v1` template contract that declares them.

A **world template** holds everything that is scenario-independent
(`SCENARIO_SPECIFICATION.md` §2 notes): the entity rosters, the route, the
recipes, the routing policy, the release cadence and the maintenance cadence.
Scenarios are short diffs against a shared world, which is also what stops
constants from being tuned per demo (anti-leakage rule D7).

The hierarchy the template declares, and this module instantiates::

    product ─┬─▶ flow ─▶ flow step ─▶ process step ─▶ operation type
             │                                            │
             └─▶ recipe (product × step) ◀────────────────┘
                                                          │
                                          qualified tools ─▶ chamber

A flow step names an *operation type*; every tool qualified for that operation
type offers its chambers as routing candidates (`TEMPORAL_MODEL.md` §2.2). The
etch steps therefore share one candidate pool of several multi-chamber tools,
which is what lets gate etch and metal etch be assigned independently instead
of collapsing into the audited 100% collinearity.

Two relations are declared rather than inferred (Step 3 gate condition F1)::

    metrology step ──measures──▶ process step        (exactly one)
    inspection step ──covers──▶ process step(s)      (one or more) + layer

"The metrology step after the etch measures the etch" is a *convention*, and a
convention is not a contract: a route may interleave modules, a wafer may be
measured two steps later, and a later slice that guessed from step order would
be guessing about which chamber a measurement indicts. So the template says it,
and a dangling or type-inappropriate reference is a rejection.

The template also carries the generic configuration that the later observation,
alarm and yield slices consume — FDC channels, the variation stack, latent
sensitivities, classifier confusion, severity calibration, alarm thresholds and
die geometry. Every one of those is keyed by operation type, step, product,
channel, latent or defect origin, and never by a tool or chamber name (rule
D6): the world states *machinery*, never an answer.

What this module deliberately does **not** contain:

* **no latent state, no benign offsets, no noise.** The observation model is a
  later slice (`CAUSAL_MECHANISM_MODEL.md` §2); a world is the static stage,
  not the physics on it.
* **no faults and no fault-shaped constants.** Every constant here is keyed by
  step, product or operation type — never by a tool or chamber name (rule D6).
  The roster contains ETCH-02 because a fab contains an ETCH-02, and the world
  is exactly as neutral about it as about ETCH-01.
* **no observable rows.** These are the simulator's internal entities. The
  schema v2 emitter is a later slice and reads them; the shapes here are
  deliberately close to `SCHEMA_V2_DESIGN.md` §2 so that translation stays a
  projection rather than a redesign.

Entity ids are assigned in template order, starting at 1, per entity type —
never in an order that could depend on which entity a scenario later targets
(rule D5).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "ALARM_SOURCES",
    "CHANNEL_KINDS",
    "CONTRACT",
    "DIE_INDEX_ORDERS",
    "DIE_ORIGINS",
    "HEADER_KEY",
    "HEADER_VALUE",
    "INSPECTION_OPERATION",
    "METROLOGY_OPERATION",
    "MINUTES_PER_DAY",
    "PARTIAL_DIE_POLICIES",
    "SEVERITY_LEVELS",
    "SHIFTS",
    "WORLD_DOCUMENTATION_KEYS",
    "WORLD_IDENTITY_DOMAIN",
    "WORLD_TEMPLATE_ROOT",
    "AlarmCode",
    "AlarmPolicy",
    "Chamber",
    "ClassifierPolicy",
    "Dedication",
    "DieGridPolicy",
    "HourRange",
    "FlowStep",
    "LotReleasePolicy",
    "MaintenancePolicy",
    "MinuteDistribution",
    "ObservationChannel",
    "ObservationPolicy",
    "Operator",
    "ProcessFlow",
    "ProcessStep",
    "Product",
    "QueuePolicy",
    "Recipe",
    "RoutingPolicy",
    "StepMetric",
    "Tool",
    "VariationStack",
    "World",
    "WorldTemplateError",
    "available_worlds",
    "build_world",
    "load_world",
    "load_world_template",
    "world_identity_json",
    "world_sha256",
    "world_template_path",
]

# ------------------------------------------------------------------ contract

#: The world template contract this loader implements. In a file it is spelled
#: as the header field ``"fabsim": "world/v1"``, exactly as the scenario
#: contract spells its own header.
CONTRACT = "fabsim.world/v1"
HEADER_KEY = "fabsim"
HEADER_VALUE = "world/v1"

#: Domain-separation tag for the world digest, versioned like the RNG's and the
#: dataset identity's, so a future change to the derivation is a visible version
#: bump rather than a silent reshuffle of every fingerprint ever recorded.
WORLD_IDENTITY_DOMAIN = "fabsim.world/v1"

#: Template fields that document a world rather than define it. Excluded from
#: `world_sha256` for the same reason `name`/`description` are excluded from a
#: scenario's identity: prose never reaches an observable, so editing it must
#: not claim that a different dataset was produced.
WORLD_DOCUMENTATION_KEYS = ("description",)

#: Where the template registry lives (`FABSIM_DESIGN.md` §5 layout notes:
#: world templates sit beside scenarios). Resolution is by template *name*, so
#: nothing environmental — no path, no cwd — reaches a generated dataset.
WORLD_TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "scenarios" / "worlds"

#: One simulated day. The clock's resolution is minutes (`TEMPORAL_MODEL.md`
#: §1), so every duration in this package is an integer count of them.
MINUTES_PER_DAY = 24 * 60

#: Operator shifts, 8 hours each from midnight. Operators are causally inert by
#: design (`SCHEMA_V2_DESIGN.md` §2.10) — a hypothesis dimension that must be
#: *exonerable*, which requires that it exist.
SHIFTS = ("A", "B", "C")

#: The two operation types the relation contracts key off. A *metrology* step
#: reads out a measurable produced by an earlier step (`measures`); an
#: *inspection* step scans a wafer for defects accumulated over the steps it
#: `covers`. Both are generic fab vocabulary, not fault vocabulary.
METROLOGY_OPERATION = "METROLOGY"
INSPECTION_OPERATION = "INSPECTION"

#: What an observation channel is: an FDC summary the *process* tool records
#: (grounded in a recipe setpoint), or a metrology read-out (grounded in a step
#: metric). `SCHEMA_V2_DESIGN.md` §2.14 / §2.15.
CHANNEL_KINDS = ("fdc", "metrology")

#: What an alarm rule watches: a declared observation channel, or a declared
#: latent. Both are generic signals; neither names an event or an entity.
ALARM_SOURCES = ("channel", "latent")

#: Severity levels the scenario contract may state, and which the world
#: calibrates in σ-units of the weekly aggregate (`CAUSAL_MECHANISM_MODEL.md`
#: §8). Kept here as a literal rather than imported: a world template is
#: scenario-independent and must not depend on the scenario module.
#: `test_severity_vocabularies_agree` holds the two copies together.
SEVERITY_LEVELS = ("subtle", "moderate", "obvious")

#: Die-grid coordinate conventions. Closed one-member vocabularies today: the
#: point is that the convention is *stated and versioned*, so the later yield
#: model derives die coordinates from declared geometry rather than from an
#: assumption nobody wrote down.
DIE_ORIGINS = ("wafer_center",)
DIE_INDEX_ORDERS = ("row_major",)
PARTIAL_DIE_POLICIES = ("exclude",)

_TEMPLATE_REQUIRED = (HEADER_KEY, "name", "time_origin", "wafers_per_lot",
                      "operation_types", "layers", "products", "process_steps",
                      "process_flows", "tools", "operators", "routing",
                      "lot_release", "queue", "maintenance", "observation",
                      "alarms", "die_grid")
_TEMPLATE_OPTIONAL = ("description", "recipes")
_TEMPLATE_KEYS = _TEMPLATE_REQUIRED + _TEMPLATE_OPTIONAL

_PRODUCT_REQUIRED = ("name", "flow", "technology_node_nm", "wafer_size_mm",
                     "die_size_mm2", "target_yield_pct", "mix_weight",
                     "metric_scale")
_STEP_REQUIRED = ("name", "operation_type", "duration_minutes")
_STEP_OPTIONAL = ("is_inspection", "metric", "settings", "measures", "covers",
                  "layer")
_FLOW_KEYS = ("name", "steps")
_TOOL_REQUIRED = ("name", "tool_type", "operations", "chambers")
_TOOL_OPTIONAL = ("vendor", "install_date", "location_bay")
_CHAMBER_REQUIRED = ("name",)
_CHAMBER_OPTIONAL = ("install_date",)
_OPERATOR_REQUIRED = ("name", "shift")
_OPERATOR_OPTIONAL = ("certification_level",)
_METRIC_KEYS = ("name", "target", "tolerance")
_DURATION_KEYS = ("mean", "sd", "min")
_HOURS_KEYS = ("min", "max")
_ROUTING_REQUIRED = ("stickiness",)
_ROUTING_OPTIONAL = ("dedications",)
_DEDICATION_KEYS = ("product", "tool", "operation_type", "start_day", "end_day",
                    "share")
_LOT_RELEASE_KEYS = ("first_release_day", "mean_interval_days", "jitter_days")
_QUEUE_KEYS = ("delay_minutes",)
_MAINTENANCE_KEYS = ("pm_interval_days", "pm_jitter_days", "pm_duration_hours",
                     "qual_duration_hours", "breakdown_mtbf_days",
                     "breakdown_duration_hours", "technicians",
                     "pm_action_codes", "unscheduled_action_codes")
_RECIPES_KEYS = ("version",)

_OBSERVATION_KEYS = ("latents", "wafer_zones", "channels", "variation_stack",
                     "severity_calibration", "classifier")
_CHANNEL_REQUIRED = ("name", "kind", "operation_types", "scale",
                     "sensitivities")
_CHANNEL_OPTIONAL = ("unit",)
_VARIATION_KEYS = ("fab_week", "tool_offset", "chamber_offset", "lot_ar1_phi",
                   "lot_ar1_sd", "run_noise", "metrology_noise")
_CLASSIFIER_KEYS = ("classes", "origins", "confusion")

_ALARM_KEYS = ("severities", "codes", "background_rate_per_chamber_day",
               "detection_probability")
_ALARM_CODE_KEYS = ("code", "source", "signal", "operation_types",
                    "threshold_sigma", "severity", "message")

_DIE_GRID_REQUIRED = ("edge_exclusion_mm", "street_width_mm",
                      "die_aspect_ratio")
_DIE_GRID_OPTIONAL = ("origin", "index_order", "partial_die_policy")
_DIE_GRID_KEYS = _DIE_GRID_REQUIRED + _DIE_GRID_OPTIONAL

#: Names that are code-level identifiers (template and flow names) versus names
#: that are shop-floor vocabulary (``ETCH-02``, ``B``, ``OP-101``).
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]*")
_ENTITY_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_OPERATION_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_PARAM_RE = re.compile(r"[a-z][a-z0-9_]*")

_BOM = chr(0xFEFF)


class WorldTemplateError(ValueError):
    """A world template was rejected.

    Like `ScenarioConfigError`, the exception names the offending field path
    (``tools[4].chambers[1].name``) so a rejection tells an author where to
    look rather than merely that something is wrong.
    """

    def __init__(self, message: str, path: str = "") -> None:
        self.path = path
        self.reason = message
        super().__init__(f"{path}: {message}" if path else message)


# ------------------------------------------------------- validation plumbing
#
# These mirror the private helpers of `scenario.py`. They are deliberately not
# shared yet: `scenario.py` is a frozen contract, and two consumers are not
# enough to justify extracting a third module (the third consumer will be the
# emitter, and that is when the extraction pays for itself).


def _at(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _require(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise WorldTemplateError(f"missing required field {key!r}",
                                 _at(path, key))
    return obj[key]


def _reject_unknown(obj: Mapping[str, Any], allowed: Sequence[str],
                    path: str) -> None:
    unknown = sorted(k for k in obj if k not in allowed)
    if unknown:
        raise WorldTemplateError(
            "unknown field(s) " + ", ".join(repr(k) for k in unknown)
            + "; allowed: " + ", ".join(sorted(allowed)),
            path,
        )


def _as_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorldTemplateError(
            f"expected an object, got {_type_name(value)}", path)
    return value


def _as_array(value: Any, path: str, *, minimum_length: int = 0) -> list[Any]:
    if not isinstance(value, list):
        raise WorldTemplateError(
            f"expected an array, got {_type_name(value)}", path)
    if len(value) < minimum_length:
        raise WorldTemplateError(
            f"must hold at least {minimum_length} item(s), got {len(value)}",
            path)
    return value


def _as_text(value: Any, path: str, pattern: re.Pattern[str] | None = None,
             *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise WorldTemplateError(
            f"expected a string, got {_type_name(value)}", path)
    text = value.strip()
    if not text and not allow_empty:
        raise WorldTemplateError("must not be empty", path)
    if pattern is not None and text and not pattern.fullmatch(text):
        raise WorldTemplateError(
            f"{text!r} does not match {pattern.pattern!r}", path)
    return text


def _as_choice(value: Any, path: str, choices: Sequence[str]) -> str:
    text = _as_text(value, path)
    if text not in choices:
        raise WorldTemplateError(
            f"{text!r} is not one of: " + ", ".join(sorted(choices)), path)
    return text


def _as_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise WorldTemplateError(
            f"expected a boolean, got {_type_name(value)}", path)
    return value


def _as_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorldTemplateError(
            f"expected an integer, got {_type_name(value)}", path)
    if minimum is not None and value < minimum:
        raise WorldTemplateError(f"must be >= {minimum}, got {value}", path)
    return value


def _as_number(value: Any, path: str, *, minimum: float | None = None,
               maximum: float | None = None,
               greater_than: float | None = None,
               less_than: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldTemplateError(
            f"expected a number, got {_type_name(value)}", path)
    number = float(value)
    if minimum is not None and number < minimum:
        raise WorldTemplateError(f"must be >= {minimum}, got {number}", path)
    if maximum is not None and number > maximum:
        raise WorldTemplateError(f"must be <= {maximum}, got {number}", path)
    if greater_than is not None and number <= greater_than:
        raise WorldTemplateError(f"must be > {greater_than}, got {number}",
                                 path)
    if less_than is not None and number >= less_than:
        raise WorldTemplateError(f"must be < {less_than}, got {number}", path)
    return number


def _as_date(value: Any, path: str) -> date:
    text = _as_text(value, path)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise WorldTemplateError(f"{text!r} is not an ISO date: {exc}",
                                 path) from exc


def _as_datetime(value: Any, path: str) -> datetime:
    text = _as_text(value, path)
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorldTemplateError(f"{text!r} is not an ISO timestamp: {exc}",
                                 path) from exc
    if stamp.tzinfo is not None:
        raise WorldTemplateError(
            "must be a naive local timestamp; the simulated clock has no time "
            "zone (`TEMPORAL_MODEL.md` §1)", path)
    return stamp


def _unique(names: Sequence[str], path: str, what: str) -> None:
    seen: set[str] = set()
    for index, name in enumerate(names):
        if name in seen:
            raise WorldTemplateError(f"duplicate {what} {name!r}",
                                     f"{path}[{index}]")
        seen.add(name)


# ------------------------------------------------------------- value objects


@dataclass(frozen=True)
class MinuteDistribution:
    """A duration in minutes: normal around `mean`, floored at `minimum`.

    Durations are drawn per (wafer, flow step) and never depend on which
    chamber was chosen — a chamber that ran visibly faster or slower than its
    peers would be a distributional fingerprint (leakage class T8) sitting in
    the timestamps themselves.
    """

    mean: float
    sd: float
    minimum: float

    def draw(self, rng: Any) -> int:
        value = rng.normalvariate(self.mean, self.sd) if self.sd > 0 else self.mean
        return max(1, int(round(max(value, self.minimum))))


@dataclass(frozen=True)
class HourRange:
    """A duration drawn uniformly from an hour range, returned in minutes."""

    minimum: float
    maximum: float

    def draw_minutes(self, rng: Any) -> int:
        hours = (rng.uniform(self.minimum, self.maximum)
                 if self.maximum > self.minimum else self.minimum)
        return max(1, int(round(hours * 60.0)))


@dataclass(frozen=True)
class StepMetric:
    """The measurable a step targets (post-etch CD, deposited thickness…)."""

    name: str
    target: float
    tolerance: float


# ----------------------------------------------------------------- entities


@dataclass(frozen=True)
class Product:
    product_id: int
    product_name: str
    flow_id: int
    technology_node_nm: int
    wafer_size_mm: int
    die_size_mm2: float
    target_yield_pct: float
    #: Relative frequency in the released lot mix. Generator-side only.
    mix_weight: int
    #: Scales every step metric target for this product — the dimensional
    #: signature that makes products statistically distinguishable without any
    #: product ever being "the answer".
    metric_scale: float


@dataclass(frozen=True)
class ProcessStep:
    step_id: int
    step_name: str
    operation_type: str
    is_inspection: bool
    duration: MinuteDistribution
    metric: StepMetric | None
    settings: tuple[tuple[str, float], ...]
    #: What a metrology step reads out. Declared, never inferred from the
    #: route: "the step before" is a convention, and a later slice that guessed
    #: would be guessing about which chamber a measurement indicts (F1).
    measures_step_id: int | None = None
    #: What an inspection step scans for — the steps whose defects it can see.
    #: One inspection covers a *module*, not the single step in front of it.
    covers_step_ids: tuple[int, ...] = ()
    #: The layer an inspection reports its defects at (`SCHEMA_V2_DESIGN.md`
    #: §2.20). `None` on every step that is not an inspection.
    layer: str | None = None


@dataclass(frozen=True)
class FlowStep:
    """One position in one route: the grain `runs` are recorded at."""

    flow_step_id: int
    flow_id: int
    step_id: int
    step_sequence: int


@dataclass(frozen=True)
class ProcessFlow:
    flow_id: int
    flow_name: str
    flow_step_ids: tuple[int, ...]


@dataclass(frozen=True)
class Chamber:
    """The primary locus of behaviour (`SCHEMA_V2_DESIGN.md` §2.9)."""

    chamber_id: int
    tool_id: int
    chamber_name: str
    install_date: date


@dataclass(frozen=True)
class Tool:
    tool_id: int
    tool_name: str
    tool_type: str
    vendor: str
    install_date: date
    location_bay: str
    #: Operation types this tool is qualified for — the qualification map a
    #: flow step routes through.
    operations: tuple[str, ...]
    chamber_ids: tuple[int, ...]


@dataclass(frozen=True)
class Recipe:
    recipe_id: int
    step_id: int
    product_id: int
    recipe_name: str
    version: str
    metric_name: str | None
    metric_target: float | None
    metric_usl: float | None
    metric_lsl: float | None
    settings: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Operator:
    operator_id: int
    operator_name: str
    shift: str
    certification_level: str


# ------------------------------------------------------------------ policies


@dataclass(frozen=True)
class Dedication:
    """A product preferentially routed to one **tool** during a window.

    Dedication is a *routing policy* and it is visible in the emitted routing
    shares — the confounder of scenario G is honest data, not hidden state
    (`SCENARIO_SPECIFICATION.md` §4 G). Two things about its shape are
    load-bearing, and both are enforced by the loaders rather than by habit:

    * **`share`, not a filter.** A dedication raises the probability that the
      lot's traffic lands on the dedicated tool; it never removes the other
      qualified tools from the pool (ADR-015). A hard filter would make product
      and chamber exposure the *same* variable inside the window, and "the
      chamber effect survives within-product comparison" — the whole point of
      scenario G — would have no data to be true or false against.
    * **tool-level, never chamber-level.** A dedication that named a chamber
      would aim traffic at exactly the grain a later fault is attributed at,
      and the confounder would stop being a confounder and start being a
      pointer. The field does not exist, and stating it is an error.

    The world template declares *standing* policy; a scenario layers
    time-bounded experimental conditions on top (`fabsim.routing`). The
    baseline world declares none.
    """

    product_name: str
    tool_name: str
    operation_type: str
    start_day: float
    end_day: float
    #: Probability, in (0, 1), that a covered routing decision is restricted to
    #: the dedicated tool. Realized share is a little higher, because the
    #: remaining traffic can still reach that tool on availability.
    share: float

    def covers(self, product_name: str, operation_type: str,
               day: float) -> bool:
        return (product_name == self.product_name
                and operation_type == self.operation_type
                and self.start_day <= day < self.end_day)


@dataclass(frozen=True)
class RoutingPolicy:
    #: Probability that a lot reuses the chamber its previous wafer used at
    #: this step (`TEMPORAL_MODEL.md` §2.2) — realistic lot/tool clustering,
    #: and mild honest confounding.
    stickiness: float
    dedications: tuple[Dedication, ...]


@dataclass(frozen=True)
class LotReleasePolicy:
    first_release_day: float
    mean_interval_days: float
    jitter_days: float


@dataclass(frozen=True)
class QueuePolicy:
    """The wait between finishing one step and being ready for the next.

    Transport, carrier handling and queue time in one distribution. It is the
    dominant term in a real fab's cycle time, and it is what makes lots overlap
    in the line: with a release every few days and a route that takes longer
    than that to walk, several lots are always in flight at once — the
    condition the audit found missing when one lot per fortnight made time and
    product mix the same variable.
    """

    delay: MinuteDistribution


@dataclass(frozen=True)
class ObservationChannel:
    """One measurable the world can report, and what moves it.

    A channel is either an FDC summary the process tool records (`fdc`,
    grounded in a recipe setpoint of the same name) or a metrology read-out
    (`metrology`, grounded in a step metric of the same name). Either way its
    `operation_types` are those of the *process* step that produces it — a CD
    is produced by an etch and merely read out at a metrology tool, which is
    exactly the relation `ProcessStep.measures_step_id` records.

    `sensitivities` is the latent → channel row of the sensitivity matrix of
    `CAUSAL_MECHANISM_MODEL.md` §2, in units of `scale`. It is keyed by latent
    name; there is no key by which it could be keyed to an entity.
    """

    name: str
    kind: str
    operation_types: tuple[str, ...]
    unit: str
    #: The channel's natural variation unit. Every variation-stack term and
    #: every sensitivity is a multiple of it, which is what lets one stack
    #: serve channels measured in nm, mtorr and watts.
    scale: float
    sensitivities: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class VariationStack:
    """The benign variation every channel carries (`CAUSAL_MECHANISM_MODEL.md`
    §2–§3), as dimensionless multiples of a channel's `scale`.

    This is the reason tool and chamber differences exist *everywhere without
    being faults* — the audit's requirement that "tool differences are not
    automatically faults" is a property of this stack, not of a caveat in a
    report.
    """

    fab_week: float
    tool_offset: float
    chamber_offset: float
    lot_ar1_phi: float
    lot_ar1_sd: float
    run_noise: float
    metrology_noise: float


@dataclass(frozen=True)
class ClassifierPolicy:
    """The noisy defect classifier (`CAUSAL_MECHANISM_MODEL.md` §4.5).

    `confusion` maps each hidden *origin* to a distribution over the observable
    *classes*. It is what breaks the audited type ⇒ coordinates ⇒ "confirmed"
    circularity: spatial confirmation of a classified type can genuinely fail,
    because the label is a draw and not a restatement of the geometry.
    """

    classes: tuple[str, ...]
    origins: tuple[str, ...]
    #: origin → ((class, probability), …), rows in `origins` order, each row
    #: summing to 1.
    confusion: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]

    def row(self, origin: str) -> tuple[tuple[str, float], ...]:
        return dict(self.confusion)[origin]


@dataclass(frozen=True)
class ObservationPolicy:
    """Everything the later observation models need, and nothing they must
    not have.

    Keyed by operation type, step, product, channel, latent and defect origin
    — never by tool, chamber, or event (rule D6). A mechanism may shift a
    latent; what that latent does to a channel is stated here, once, for every
    entity alike.
    """

    latents: tuple[str, ...]
    #: Radial zones a wafer is summarized in: the resolution at which
    #: within-wafer effects become visible without adding a site-level grain
    #: (`SCHEMA_V2_DESIGN.md` §2.15).
    wafer_zones: tuple[str, ...]
    channels: tuple[ObservationChannel, ...]
    variation: VariationStack
    #: severity → aggregate shift in σ of the weekly statistic
    #: (`CAUSAL_MECHANISM_MODEL.md` §8). The difficulty axis, stated once.
    severity_calibration: tuple[tuple[str, float], ...]
    classifier: ClassifierPolicy

    def sigma_for(self, severity: str) -> float:
        return dict(self.severity_calibration)[severity]


@dataclass(frozen=True)
class AlarmCode:
    """One generic observation/response rule the fab's equipment applies.

    A rule says "this signal, this far outside its normal spread, on tools of
    these operation types, is worth this alarm code" — a *threshold*, not a
    verdict. No rule may reference an event, a mechanism, a tool or a chamber:
    a code that could only fire on the affected entity would be leakage class
    T3, and every code here can fire on any qualified tool, with a background
    rate that guarantees it does (`ANTI_LEAKAGE_DESIGN.md` D2).
    """

    code: str
    #: Which plane the rule watches: a declared `channel`, or a `latent`.
    source: str
    signal: str
    operation_types: tuple[str, ...]
    #: Departure, in σ of the signal's own spread, at which the rule may fire.
    threshold_sigma: float
    severity: str
    #: Fab-wide templated text. Observable free text comes from this list only
    #: (`ANTI_LEAKAGE_DESIGN.md` §4.3).
    message: str


@dataclass(frozen=True)
class AlarmPolicy:
    severities: tuple[str, ...]
    codes: tuple[AlarmCode, ...]
    #: False alarms per chamber per day, on *every* chamber — the nonzero
    #: support that stops "this chamber has alarms" from being an answer.
    background_rate_per_chamber_day: float
    #: Probability that a threshold crossing actually emits. Alarms are noisy,
    #: not guaranteed (`CAUSAL_MECHANISM_MODEL.md` §7).
    detection_probability: float

    def code_by_name(self, code: str) -> AlarmCode:
        return {c.code: c for c in self.codes}[code]


@dataclass(frozen=True)
class DieGridPolicy:
    """Physical die geometry: enough to derive a die grid, and nothing else.

    The grid itself is a later slice. What matters here is that the derivation
    is *declared* — wafer size and die area come from the product, the usable
    radius and the packing come from this policy — so a die's coordinates are a
    function of geometry alone. Nothing in the chain needs to know which
    chamber a wafer saw, which is what makes ADR-004's "no term reads fault
    identity" checkable in the yield model rather than merely intended.
    """

    #: Ring at the wafer edge that carries no die.
    edge_exclusion_mm: float
    #: Scribe lane between neighbouring die.
    street_width_mm: float
    #: Die width ÷ height. 1.0 is square; area comes from the product.
    die_aspect_ratio: float
    origin: str
    index_order: str
    partial_die_policy: str


@dataclass(frozen=True)
class MaintenancePolicy:
    pm_interval_days: float
    pm_jitter_days: float
    pm_duration: HourRange
    qual_duration_hours: float
    breakdown_mtbf_days: float
    breakdown_duration: HourRange
    #: Closed, fab-wide vocabularies: every code can occur on any tool, so no
    #: code is a fault fingerprint (`ANTI_LEAKAGE_DESIGN.md` D2).
    technicians: tuple[str, ...]
    pm_action_codes: tuple[str, ...]
    unscheduled_action_codes: tuple[str, ...]


# --------------------------------------------------------------------- world


@dataclass(frozen=True)
class World:
    """The instantiated static fab: entities, relationships, policies.

    Everything is a tuple in id order, so iteration is deterministic without
    the caller having to remember to sort (`FABSIM_DESIGN.md` §6).
    """

    template_name: str
    description: str
    time_origin: datetime
    wafers_per_lot: int
    operation_types: tuple[str, ...]
    layers: tuple[str, ...]
    products: tuple[Product, ...]
    process_steps: tuple[ProcessStep, ...]
    process_flows: tuple[ProcessFlow, ...]
    flow_steps: tuple[FlowStep, ...]
    tools: tuple[Tool, ...]
    chambers: tuple[Chamber, ...]
    recipes: tuple[Recipe, ...]
    operators: tuple[Operator, ...]
    routing: RoutingPolicy
    lot_release: LotReleasePolicy
    queue: QueuePolicy
    maintenance: MaintenancePolicy
    observation: ObservationPolicy
    alarms: AlarmPolicy
    die_grid: DieGridPolicy
    #: SHA-256 of the template's semantic content — one of the inputs of the
    #: dataset build fingerprint (`SCENARIO_SPECIFICATION.md` §5). A world is
    #: as much an input to a dataset as its scenario is, so a dataset built
    #: against a changed world is a different build and says so.
    world_sha256: str

    # Derived indexes. Excluded from equality and repr: they are lookups over
    # the fields above, not state of their own.
    _index: dict[str, Any] = field(default_factory=dict, init=False,
                                   repr=False, compare=False)

    def __post_init__(self) -> None:
        by_id = {
            "product": {p.product_id: p for p in self.products},
            "step": {s.step_id: s for s in self.process_steps},
            "flow": {f.flow_id: f for f in self.process_flows},
            "flow_step": {f.flow_step_id: f for f in self.flow_steps},
            "tool": {t.tool_id: t for t in self.tools},
            "chamber": {c.chamber_id: c for c in self.chambers},
            "recipe": {r.recipe_id: r for r in self.recipes},
            "operator": {o.operator_id: o for o in self.operators},
        }
        self._index.update(by_id)
        self._index["product_by_name"] = {p.product_name: p
                                          for p in self.products}
        self._index["step_by_name"] = {s.step_name: s
                                       for s in self.process_steps}
        self._index["flow_by_name"] = {f.flow_name: f
                                       for f in self.process_flows}
        self._index["tool_by_name"] = {t.tool_name: t for t in self.tools}
        self._index["chamber_by_name"] = {
            (self.tool(c.tool_id).tool_name, c.chamber_name): c
            for c in self.chambers
        }
        self._index["recipe_by_pair"] = {(r.step_id, r.product_id): r
                                         for r in self.recipes}
        self._index["operators_by_shift"] = {
            shift: tuple(o for o in self.operators if o.shift == shift)
            for shift in SHIFTS
        }
        self._index["flow_steps_of"] = {
            flow.flow_id: tuple(self.flow_step(i) for i in flow.flow_step_ids)
            for flow in self.process_flows
        }
        self._index["tools_for_operation"] = {
            operation: tuple(t for t in self.tools
                             if operation in t.operations)
            for operation in self.operation_types
        }
        self._index["eligible"] = {
            fs.flow_step_id: tuple(
                chamber
                for tool in self._index["tools_for_operation"][
                    self.step(fs.step_id).operation_type]
                for chamber in self.chambers_of(tool.tool_id)
            )
            for fs in self.flow_steps
        }
        # The declared relations, and their inverses. Both directions are asked
        # for: "what does this metrology row measure?" and "where is this
        # step's CD read out?" are the same relation from two ends.
        self._index["metrology_for"] = {
            step.step_id: tuple(s for s in self.process_steps
                                if s.measures_step_id == step.step_id)
            for step in self.process_steps
        }
        self._index["inspections_for"] = {
            step.step_id: tuple(s for s in self.process_steps
                                if step.step_id in s.covers_step_ids)
            for step in self.process_steps
        }
        self._index["channel_by_name"] = {c.name: c
                                          for c in self.observation.channels}
        self._index["channels_for_operation"] = {
            operation: tuple(c for c in self.observation.channels
                             if operation in c.operation_types)
            for operation in self.operation_types
        }

    # -- lookups by id ----------------------------------------------------
    def product(self, product_id: int) -> Product:
        return self._index["product"][product_id]

    def step(self, step_id: int) -> ProcessStep:
        return self._index["step"][step_id]

    def flow(self, flow_id: int) -> ProcessFlow:
        return self._index["flow"][flow_id]

    def flow_step(self, flow_step_id: int) -> FlowStep:
        return self._index["flow_step"][flow_step_id]

    def tool(self, tool_id: int) -> Tool:
        return self._index["tool"][tool_id]

    def chamber(self, chamber_id: int) -> Chamber:
        return self._index["chamber"][chamber_id]

    def recipe(self, recipe_id: int) -> Recipe:
        return self._index["recipe"][recipe_id]

    def operator(self, operator_id: int) -> Operator:
        return self._index["operator"][operator_id]

    # -- lookups by name --------------------------------------------------
    def product_by_name(self, name: str) -> Product:
        return self._index["product_by_name"][name]

    def step_by_name(self, name: str) -> ProcessStep:
        return self._index["step_by_name"][name]

    def flow_by_name(self, name: str) -> ProcessFlow:
        return self._index["flow_by_name"][name]

    def tool_by_name(self, name: str) -> Tool:
        return self._index["tool_by_name"][name]

    def chamber_by_name(self, tool_name: str, chamber_name: str) -> Chamber:
        return self._index["chamber_by_name"][(tool_name, chamber_name)]

    # -- relationships ----------------------------------------------------
    def chambers_of(self, tool_id: int) -> tuple[Chamber, ...]:
        return tuple(self.chamber(cid)
                     for cid in self.tool(tool_id).chamber_ids)

    def flow_steps_of(self, flow_id: int) -> tuple[FlowStep, ...]:
        """The route of `flow_id`, in step-sequence order."""
        return self._index["flow_steps_of"][flow_id]

    def tools_for_operation(self, operation_type: str) -> tuple[Tool, ...]:
        """The qualification map: tools that can run this operation type."""
        return self._index["tools_for_operation"][operation_type]

    def eligible_chambers(self, flow_step_id: int) -> tuple[Chamber, ...]:
        """Routing candidates for a flow step, in chamber-id order.

        Every chamber of every tool qualified for the step's operation type.
        Two flow steps of the same operation type therefore share one pool —
        which is what makes independent gate-etch/metal-etch assignment
        possible rather than structurally collinear.
        """
        return self._index["eligible"][flow_step_id]

    def recipe_for(self, step_id: int, product_id: int) -> Recipe:
        return self._index["recipe_by_pair"][(step_id, product_id)]

    def operators_on_shift(self, shift: str) -> tuple[Operator, ...]:
        return self._index["operators_by_shift"][shift]

    # -- the declared relations (F1) --------------------------------------
    def measured_step(self, step_id: int) -> ProcessStep | None:
        """The process step a metrology step reads out, or `None`."""
        measures = self.step(step_id).measures_step_id
        return None if measures is None else self.step(measures)

    def metrology_steps_for(self, step_id: int) -> tuple[ProcessStep, ...]:
        """Metrology steps that measure `step_id`, in step-id order."""
        return self._index["metrology_for"][step_id]

    def covered_steps(self, step_id: int) -> tuple[ProcessStep, ...]:
        """The process steps an inspection step scans for, in route order."""
        return tuple(self.step(covered)
                     for covered in self.step(step_id).covers_step_ids)

    def inspection_steps_for(self, step_id: int) -> tuple[ProcessStep, ...]:
        """Inspection steps that cover `step_id`, in step-id order."""
        return self._index["inspections_for"][step_id]

    # -- observation configuration ----------------------------------------
    def channel(self, name: str) -> ObservationChannel:
        return self._index["channel_by_name"][name]

    def channels_for_operation(self, operation_type: str
                               ) -> tuple[ObservationChannel, ...]:
        return self._index["channels_for_operation"][operation_type]

    # -- the clock --------------------------------------------------------
    def at(self, minutes: int) -> datetime:
        """Wall-clock timestamp of a minute offset on the simulated clock."""
        return self.time_origin + timedelta(minutes=minutes)

    def shift_at(self, minutes: int) -> str:
        """Which 8-hour shift a minute offset falls in."""
        return SHIFTS[(minutes % MINUTES_PER_DAY) // (8 * 60)]


# --------------------------------------------------------------- template IO


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys rather than silently keeping the last one."""
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise WorldTemplateError(f"duplicate key {key!r}")
        seen.add(key)
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise WorldTemplateError(f"{name} is not a valid template value")


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text.lstrip(_BOM),
                          object_pairs_hook=_object_pairs_hook,
                          parse_constant=_reject_constant)
    except WorldTemplateError:
        raise
    except json.JSONDecodeError as exc:
        raise WorldTemplateError(f"invalid JSON: {exc}") from exc


# ------------------------------------------------------------ world identity


def _dumps(obj: Any) -> str:
    """The one serialization used for the world digest.

    Identical in spirit and in flags to `scenario._dumps`: sorted keys and
    compact separators remove formatting from the equation, `ensure_ascii`
    removes the encoding from it, and `allow_nan` off keeps the output valid
    JSON.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def world_identity_json(template: Mapping[str, Any]) -> str:
    """The template's semantic content, in one canonical text.

    Key order, indentation and a byte-order mark are gone by the time a
    template gets here — they are parse-level artefacts, and a digest that
    moved when an editor reindented a file would be measuring the editor.
    Documentation is removed for the same reason a scenario's prose is.

    The digest is conservative in one direction on purpose: a template that
    omits an optional field and one that writes that field's default are
    different texts, so they get different digests even though they build the
    same world. Reporting "different" for two identical worlds costs a
    rebuild; reporting "same" for two different ones would cost the
    reproducibility claim.
    """
    view = {key: value for key, value in template.items()
            if key not in WORLD_DOCUMENTATION_KEYS}
    try:
        return _dumps(view)
    except (TypeError, ValueError) as exc:
        raise WorldTemplateError(
            f"template holds a value that is not JSON: {exc}") from exc


def world_sha256(template: Mapping[str, Any]) -> str:
    """SHA-256 over a world template's semantic content."""
    return hashlib.sha256(
        WORLD_IDENTITY_DOMAIN.encode("ascii") + b"\x00"
        + world_identity_json(template).encode("utf-8")
    ).hexdigest()


def world_template_path(name: str, root: str | Path | None = None) -> Path:
    """Where the template registry expects `name` to live."""
    return Path(root or WORLD_TEMPLATE_ROOT) / f"{name}.json"


def available_worlds(root: str | Path | None = None) -> tuple[str, ...]:
    """Template names the registry can resolve, in sorted order."""
    directory = Path(root or WORLD_TEMPLATE_ROOT)
    if not directory.is_dir():
        return ()
    return tuple(sorted(p.stem for p in directory.glob("*.json")))


def load_world_template(name: str, root: str | Path | None = None) -> dict[str, Any]:
    """Read and parse a world template by name (no validation yet)."""
    path = world_template_path(name, root)
    if not path.is_file():
        known = ", ".join(available_worlds(root)) or "(none)"
        raise WorldTemplateError(
            f"unknown world template {name!r}; the registry at "
            f"{Path(root or WORLD_TEMPLATE_ROOT)} holds: {known}", "world")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WorldTemplateError(
            f"{path}: template must be UTF-8 text ({exc})") from exc
    return _parse_json(text)


def load_world(name: str, root: str | Path | None = None) -> World:
    """Resolve a world template by name and instantiate it."""
    world = build_world(load_world_template(name, root))
    if world.template_name != name:
        raise WorldTemplateError(
            f"template file {name!r} declares name {world.template_name!r}; "
            "a template's name is how scenarios refer to it and must match "
            "its file", "name")
    return world


# ---------------------------------------------------------------- builders


def _build_distribution(raw: Any, path: str) -> MinuteDistribution:
    obj = _as_object(raw, path)
    _reject_unknown(obj, _DURATION_KEYS, path)
    mean = _as_number(_require(obj, "mean", path), _at(path, "mean"),
                      greater_than=0.0)
    sd = _as_number(obj.get("sd", 0.0), _at(path, "sd"), minimum=0.0)
    minimum = _as_number(obj.get("min", 1.0), _at(path, "min"),
                         greater_than=0.0)
    if minimum > mean:
        raise WorldTemplateError(
            f"min {minimum} exceeds mean {mean}", _at(path, "min"))
    return MinuteDistribution(mean=mean, sd=sd, minimum=minimum)


def _build_hours(raw: Any, path: str) -> HourRange:
    obj = _as_object(raw, path)
    _reject_unknown(obj, _HOURS_KEYS, path)
    minimum = _as_number(_require(obj, "min", path), _at(path, "min"),
                         greater_than=0.0)
    maximum = _as_number(_require(obj, "max", path), _at(path, "max"),
                         greater_than=0.0)
    if maximum < minimum:
        raise WorldTemplateError(f"max {maximum} is below min {minimum}",
                                 _at(path, "max"))
    return HourRange(minimum=minimum, maximum=maximum)


def _build_settings(raw: Any, path: str) -> tuple[tuple[str, float], ...]:
    obj = _as_object(raw, path)
    for key in obj:
        _as_text(key, path, _PARAM_RE)
    return tuple(sorted(
        (key, _as_number(value, _at(path, key)))
        for key, value in obj.items()
    ))


def _build_metric(raw: Any, path: str) -> StepMetric | None:
    if raw is None:
        return None
    obj = _as_object(raw, path)
    _reject_unknown(obj, _METRIC_KEYS, path)
    return StepMetric(
        name=_as_text(_require(obj, "name", path), _at(path, "name"),
                      _PARAM_RE),
        target=_as_number(_require(obj, "target", path), _at(path, "target"),
                          greater_than=0.0),
        tolerance=_as_number(_require(obj, "tolerance", path),
                             _at(path, "tolerance"), greater_than=0.0),
    )


def _build_operation_types(raw: Any) -> tuple[str, ...]:
    array = _as_array(raw, "operation_types", minimum_length=1)
    operations = tuple(
        _as_text(item, f"operation_types[{i}]", _OPERATION_RE)
        for i, item in enumerate(array)
    )
    _unique(operations, "operation_types", "operation type")
    return operations


def _build_layers(raw: Any) -> tuple[str, ...]:
    """The closed layer vocabulary an inspection may report at.

    Closed for the same reason every other categorical is: a layer minted per
    inspection would be a value that exists only where something happened
    (leakage class T3).
    """
    array = _as_array(raw, "layers", minimum_length=1)
    layers = tuple(_as_text(item, f"layers[{i}]", _OPERATION_RE)
                   for i, item in enumerate(array))
    _unique(layers, "layers", "layer")
    return layers


def _is_process_step(step: ProcessStep) -> bool:
    """A step that *does* something to a wafer, rather than looking at it.

    Metrology and inspection observe; everything else processes. Only a
    processing step can be measured or covered — a relation pointing at an
    observation would be a measurement of a measurement.
    """
    return not step.is_inspection and step.operation_type != METROLOGY_OPERATION


def _resolve_measures(obj: Mapping[str, Any], path: str, step: ProcessStep,
                      by_name: Mapping[str, ProcessStep]) -> int | None:
    """Resolve a metrology step's `measures` relation (F1).

    Required on metrology steps and rejected everywhere else: a metrology step
    that measures nothing is not a metrology step, and inferring the relation
    from step order would quietly encode "the step before" as physics.
    """
    if step.operation_type != METROLOGY_OPERATION:
        if "measures" in obj:
            raise WorldTemplateError(
                f"'measures' is valid only on {METROLOGY_OPERATION} steps; "
                f"{step.step_name!r} is a {step.operation_type} step",
                _at(path, "measures"))
        return None

    field_path = _at(path, "measures")
    name = _as_text(_require(obj, "measures", path), field_path,
                    _ENTITY_NAME_RE)
    measured = by_name.get(name)
    if measured is None:
        raise WorldTemplateError(f"unknown process step {name!r}", field_path)
    if measured.step_id == step.step_id:
        raise WorldTemplateError("a step cannot measure itself", field_path)
    if not _is_process_step(measured):
        raise WorldTemplateError(
            f"{name!r} is an observation step, not a process step; metrology "
            "reads out what a process step produced", field_path)
    if measured.metric is None:
        raise WorldTemplateError(
            f"{name!r} declares no metric, so there is nothing to read out",
            field_path)
    return measured.step_id


def _resolve_covers(obj: Mapping[str, Any], path: str, step: ProcessStep,
                    by_name: Mapping[str, ProcessStep],
                    layers: Sequence[str]) -> tuple[tuple[int, ...],
                                                    str | None]:
    """Resolve an inspection step's `covers` relation and its layer (F1).

    An inspection sees the defects a *module* left behind, not the ones the
    single step in front of it left behind, so coverage is a list and the
    template states it. `layer` is the vocabulary the defects are reported at
    (`SCHEMA_V2_DESIGN.md` §2.20) and comes from the world's closed list.
    """
    if not step.is_inspection:
        for key in ("covers", "layer"):
            if key in obj:
                raise WorldTemplateError(
                    f"{key!r} is valid only on inspection steps; "
                    f"{step.step_name!r} is not one", _at(path, key))
        return (), None

    covers_path = _at(path, "covers")
    names = _as_array(_require(obj, "covers", path), covers_path,
                      minimum_length=1)
    resolved: list[str] = []
    ids: list[int] = []
    for position, item in enumerate(names):
        item_path = f"{covers_path}[{position}]"
        name = _as_text(item, item_path, _ENTITY_NAME_RE)
        covered = by_name.get(name)
        if covered is None:
            raise WorldTemplateError(f"unknown process step {name!r}",
                                     item_path)
        if covered.step_id == step.step_id:
            raise WorldTemplateError("a step cannot cover itself", item_path)
        if not _is_process_step(covered):
            raise WorldTemplateError(
                f"{name!r} is an observation step, not a process step; an "
                "inspection covers the steps whose defects it can see",
                item_path)
        resolved.append(name)
        ids.append(covered.step_id)
    _unique(resolved, covers_path, "covered step")
    layer = _as_choice(_require(obj, "layer", path), _at(path, "layer"), layers)
    return tuple(ids), layer


def _build_steps(raw: Any, operations: Sequence[str],
                 layers: Sequence[str]) -> tuple[ProcessStep, ...]:
    """Parse the steps, then resolve the relations between them.

    Two passes, because `measures` and `covers` are references into the same
    list: a metrology step may be declared before the step it reads out, and
    the loader must not make declaration order meaningful when the design says
    route order already isn't.
    """
    array = _as_array(raw, "process_steps", minimum_length=1)
    parsed: list[tuple[str, dict[str, Any], ProcessStep]] = []
    for index, item in enumerate(array):
        path = f"process_steps[{index}]"
        obj = _as_object(item, path)
        _reject_unknown(obj, _STEP_REQUIRED + _STEP_OPTIONAL, path)
        operation_type = _as_choice(_require(obj, "operation_type", path),
                                    _at(path, "operation_type"), operations)
        is_inspection = _as_bool(obj.get("is_inspection", False),
                                 _at(path, "is_inspection"))
        if is_inspection and operation_type != INSPECTION_OPERATION:
            raise WorldTemplateError(
                f"an inspection step must be an {INSPECTION_OPERATION} "
                f"operation, not {operation_type}; defect inspection and "
                "metrology are different tools doing different work",
                _at(path, "operation_type"))
        parsed.append((path, obj, ProcessStep(
            step_id=index + 1,
            step_name=_as_text(_require(obj, "name", path), _at(path, "name"),
                               _ENTITY_NAME_RE),
            operation_type=operation_type,
            is_inspection=is_inspection,
            duration=_build_distribution(
                _require(obj, "duration_minutes", path),
                _at(path, "duration_minutes")),
            metric=_build_metric(obj.get("metric"), _at(path, "metric")),
            settings=_build_settings(obj.get("settings", {}),
                                     _at(path, "settings")),
        )))

    drafts = [draft for _path, _obj, draft in parsed]
    _unique([s.step_name for s in drafts], "process_steps", "step name")
    by_name = {s.step_name: s for s in drafts}

    steps: list[ProcessStep] = []
    for path, obj, draft in parsed:
        covers, layer = _resolve_covers(obj, path, draft, by_name, layers)
        steps.append(ProcessStep(
            step_id=draft.step_id,
            step_name=draft.step_name,
            operation_type=draft.operation_type,
            is_inspection=draft.is_inspection,
            duration=draft.duration,
            metric=draft.metric,
            settings=draft.settings,
            measures_step_id=_resolve_measures(obj, path, draft, by_name),
            covers_step_ids=covers,
            layer=layer,
        ))
    return tuple(steps)


def _build_flows(raw: Any, steps: Sequence[ProcessStep]
                 ) -> tuple[tuple[ProcessFlow, ...], tuple[FlowStep, ...]]:
    array = _as_array(raw, "process_flows", minimum_length=1)
    by_name = {s.step_name: s for s in steps}
    flows: list[ProcessFlow] = []
    flow_steps: list[FlowStep] = []
    for index, item in enumerate(array):
        path = f"process_flows[{index}]"
        obj = _as_object(item, path)
        _reject_unknown(obj, _FLOW_KEYS, path)
        flow_id = index + 1
        names = _as_array(_require(obj, "steps", path), _at(path, "steps"),
                          minimum_length=1)
        ids: list[int] = []
        for position, name in enumerate(names):
            step_path = f"{path}.steps[{position}]"
            step_name = _as_text(name, step_path, _ENTITY_NAME_RE)
            if step_name not in by_name:
                raise WorldTemplateError(
                    f"unknown process step {step_name!r}", step_path)
            flow_step = FlowStep(
                flow_step_id=len(flow_steps) + 1,
                flow_id=flow_id,
                step_id=by_name[step_name].step_id,
                step_sequence=position + 1,
            )
            flow_steps.append(flow_step)
            ids.append(flow_step.flow_step_id)
        flows.append(ProcessFlow(
            flow_id=flow_id,
            flow_name=_as_text(_require(obj, "name", path), _at(path, "name"),
                               _IDENTIFIER_RE),
            flow_step_ids=tuple(ids),
        ))
    _unique([f.flow_name for f in flows], "process_flows", "flow name")
    return tuple(flows), tuple(flow_steps)


def _build_products(raw: Any, flows: Sequence[ProcessFlow]
                    ) -> tuple[Product, ...]:
    array = _as_array(raw, "products", minimum_length=1)
    by_name = {f.flow_name: f for f in flows}
    products: list[Product] = []
    for index, item in enumerate(array):
        path = f"products[{index}]"
        obj = _as_object(item, path)
        _reject_unknown(obj, _PRODUCT_REQUIRED, path)
        flow_name = _as_text(_require(obj, "flow", path), _at(path, "flow"),
                             _IDENTIFIER_RE)
        if flow_name not in by_name:
            raise WorldTemplateError(f"unknown process flow {flow_name!r}",
                                     _at(path, "flow"))
        products.append(Product(
            product_id=index + 1,
            product_name=_as_text(_require(obj, "name", path),
                                  _at(path, "name"), _ENTITY_NAME_RE),
            flow_id=by_name[flow_name].flow_id,
            technology_node_nm=_as_int(
                _require(obj, "technology_node_nm", path),
                _at(path, "technology_node_nm"), minimum=1),
            wafer_size_mm=_as_int(_require(obj, "wafer_size_mm", path),
                                  _at(path, "wafer_size_mm"), minimum=1),
            die_size_mm2=_as_number(_require(obj, "die_size_mm2", path),
                                    _at(path, "die_size_mm2"),
                                    greater_than=0.0),
            target_yield_pct=_as_number(
                _require(obj, "target_yield_pct", path),
                _at(path, "target_yield_pct"), minimum=0.0, maximum=100.0),
            mix_weight=_as_int(_require(obj, "mix_weight", path),
                               _at(path, "mix_weight"), minimum=1),
            metric_scale=_as_number(_require(obj, "metric_scale", path),
                                    _at(path, "metric_scale"),
                                    greater_than=0.0),
        ))
    _unique([p.product_name for p in products], "products", "product name")
    return tuple(products)


def _build_tools(raw: Any, operations: Sequence[str]
                 ) -> tuple[tuple[Tool, ...], tuple[Chamber, ...]]:
    array = _as_array(raw, "tools", minimum_length=1)
    tools: list[Tool] = []
    chambers: list[Chamber] = []
    for index, item in enumerate(array):
        path = f"tools[{index}]"
        obj = _as_object(item, path)
        _reject_unknown(obj, _TOOL_REQUIRED + _TOOL_OPTIONAL, path)
        tool_id = index + 1
        install_date = _as_date(obj.get("install_date", "2020-01-01"),
                                _at(path, "install_date"))
        qualified = _as_array(_require(obj, "operations", path),
                              _at(path, "operations"), minimum_length=1)
        tool_operations = tuple(
            _as_choice(op, f"{path}.operations[{i}]", operations)
            for i, op in enumerate(qualified)
        )
        _unique(tool_operations, f"{path}.operations", "operation type")

        chamber_array = _as_array(_require(obj, "chambers", path),
                                  _at(path, "chambers"), minimum_length=1)
        chamber_ids: list[int] = []
        chamber_names: list[str] = []
        for position, chamber_raw in enumerate(chamber_array):
            chamber_path = f"{path}.chambers[{position}]"
            chamber_obj = _as_object(chamber_raw, chamber_path)
            _reject_unknown(chamber_obj,
                            _CHAMBER_REQUIRED + _CHAMBER_OPTIONAL,
                            chamber_path)
            chamber = Chamber(
                chamber_id=len(chambers) + 1,
                tool_id=tool_id,
                chamber_name=_as_text(_require(chamber_obj, "name",
                                               chamber_path),
                                      _at(chamber_path, "name"),
                                      _ENTITY_NAME_RE),
                install_date=_as_date(
                    chamber_obj.get("install_date", install_date.isoformat()),
                    _at(chamber_path, "install_date")),
            )
            chambers.append(chamber)
            chamber_ids.append(chamber.chamber_id)
            chamber_names.append(chamber.chamber_name)
        _unique(chamber_names, f"{path}.chambers", "chamber name")

        tools.append(Tool(
            tool_id=tool_id,
            tool_name=_as_text(_require(obj, "name", path), _at(path, "name"),
                               _ENTITY_NAME_RE),
            tool_type=_as_choice(_require(obj, "tool_type", path),
                                 _at(path, "tool_type"), operations),
            vendor=_as_text(obj.get("vendor", "UNKNOWN"), _at(path, "vendor")),
            install_date=install_date,
            location_bay=_as_text(obj.get("location_bay", "BAY-0"),
                                  _at(path, "location_bay"), _ENTITY_NAME_RE),
            operations=tool_operations,
            chamber_ids=tuple(chamber_ids),
        ))
    _unique([t.tool_name for t in tools], "tools", "tool name")
    return tuple(tools), tuple(chambers)


def _build_operators(raw: Any) -> tuple[Operator, ...]:
    array = _as_array(raw, "operators", minimum_length=1)
    operators: list[Operator] = []
    for index, item in enumerate(array):
        path = f"operators[{index}]"
        obj = _as_object(item, path)
        _reject_unknown(obj, _OPERATOR_REQUIRED + _OPERATOR_OPTIONAL, path)
        operators.append(Operator(
            operator_id=index + 1,
            operator_name=_as_text(_require(obj, "name", path),
                                   _at(path, "name"), _ENTITY_NAME_RE),
            shift=_as_choice(_require(obj, "shift", path), _at(path, "shift"),
                             SHIFTS),
            certification_level=_as_text(
                obj.get("certification_level", "QUALIFIED"),
                _at(path, "certification_level"), _OPERATION_RE),
        ))
    _unique([o.operator_name for o in operators], "operators",
            "operator name")
    covered = {o.shift for o in operators}
    missing = [s for s in SHIFTS if s not in covered]
    if missing:
        raise WorldTemplateError(
            "every shift must be staffed; no operator on shift(s) "
            + ", ".join(missing), "operators")
    return tuple(operators)


def _build_recipes(products: Sequence[Product], flows: Sequence[ProcessFlow],
                   flow_steps: Sequence[FlowStep],
                   steps: Sequence[ProcessStep], version: str
                   ) -> tuple[Recipe, ...]:
    """One recipe per (product, step-on-the-product's-route).

    Recipes carry the per-product target and spec limits, which is what makes
    "was the measurement out of spec *for its product*?" answerable and what
    makes product effects statistically real (`SCHEMA_V2_DESIGN.md` §2.6).
    Specs scale with the product; they are never keyed by tool or chamber.
    """
    by_id = {s.step_id: s for s in steps}
    flow_step_ids = {f.flow_id: f.flow_step_ids for f in flows}
    by_flow_step = {f.flow_step_id: f for f in flow_steps}
    recipes: list[Recipe] = []
    for product in products:
        route_step_ids: list[int] = []
        for flow_step_id in flow_step_ids[product.flow_id]:
            step_id = by_flow_step[flow_step_id].step_id
            if step_id not in route_step_ids:
                route_step_ids.append(step_id)
        for step_id in sorted(route_step_ids):
            step = by_id[step_id]
            metric = step.metric
            target = tolerance = None
            if metric is not None:
                target = round(metric.target * product.metric_scale, 4)
                tolerance = round(metric.tolerance * product.metric_scale, 4)
            recipes.append(Recipe(
                recipe_id=len(recipes) + 1,
                step_id=step_id,
                product_id=product.product_id,
                recipe_name=f"{step.step_name}-{product.product_name}",
                version=version,
                metric_name=metric.name if metric else None,
                metric_target=target,
                metric_usl=(round(target + tolerance, 4)
                            if target is not None else None),
                metric_lsl=(round(target - tolerance, 4)
                            if target is not None else None),
                settings=step.settings,
            ))
    return tuple(recipes)


#: The rejection a chamber-scoped dedication earns, worded once so the world
#: template and the scenario contract give the same answer.
CHAMBER_DEDICATION_REJECTION = (
    "dedication is tool-level; a chamber-scoped dedication would aim traffic "
    "at exactly the grain a fault is attributed at, and the confounder would "
    "stop being a confounder and start being a pointer"
)


def _build_routing(raw: Any, products: Sequence[Product],
                   tools: Sequence[Tool],
                   operations: Sequence[str]) -> RoutingPolicy:
    obj = _as_object(raw, "routing")
    _reject_unknown(obj, _ROUTING_REQUIRED + _ROUTING_OPTIONAL, "routing")
    stickiness = _as_number(_require(obj, "stickiness", "routing"),
                            "routing.stickiness", minimum=0.0, maximum=1.0)
    product_names = {p.product_name for p in products}
    tools_by_name = {t.tool_name: t for t in tools}
    dedications: list[Dedication] = []
    for index, item in enumerate(_as_array(obj.get("dedications", []),
                                           "routing.dedications")):
        path = f"routing.dedications[{index}]"
        entry = _as_object(item, path)
        if "chamber" in entry:
            raise WorldTemplateError(CHAMBER_DEDICATION_REJECTION,
                                     _at(path, "chamber"))
        _reject_unknown(entry, _DEDICATION_KEYS, path)
        product_name = _as_text(_require(entry, "product", path),
                                _at(path, "product"), _ENTITY_NAME_RE)
        if product_name not in product_names:
            raise WorldTemplateError(f"unknown product {product_name!r}",
                                     _at(path, "product"))
        tool_name = _as_text(_require(entry, "tool", path), _at(path, "tool"),
                             _ENTITY_NAME_RE)
        if tool_name not in tools_by_name:
            raise WorldTemplateError(f"unknown tool {tool_name!r}",
                                     _at(path, "tool"))
        operation = _as_choice(_require(entry, "operation_type", path),
                               _at(path, "operation_type"), operations)
        if operation not in tools_by_name[tool_name].operations:
            raise WorldTemplateError(
                f"tool {tool_name!r} is not qualified for {operation!r}, so it "
                "cannot be dedicated to it", _at(path, "operation_type"))
        start_day = _as_number(_require(entry, "start_day", path),
                               _at(path, "start_day"), minimum=0.0)
        end_day = _as_number(_require(entry, "end_day", path),
                             _at(path, "end_day"), greater_than=start_day)
        # Open interval: 0 would be no dedication at all, 1 would be an
        # exclusive assignment, and an exclusive assignment is the hard filter
        # ADR-015 replaced.
        share = _as_number(_require(entry, "share", path), _at(path, "share"),
                           greater_than=0.0, less_than=1.0)
        dedications.append(Dedication(
            product_name=product_name, tool_name=tool_name,
            operation_type=operation, start_day=start_day, end_day=end_day,
            share=share,
        ))
    return RoutingPolicy(stickiness=stickiness,
                         dedications=tuple(dedications))


def _build_lot_release(raw: Any) -> LotReleasePolicy:
    obj = _as_object(raw, "lot_release")
    _reject_unknown(obj, _LOT_RELEASE_KEYS, "lot_release")
    mean_interval = _as_number(_require(obj, "mean_interval_days",
                                        "lot_release"),
                               "lot_release.mean_interval_days",
                               greater_than=0.0)
    jitter = _as_number(_require(obj, "jitter_days", "lot_release"),
                        "lot_release.jitter_days", minimum=0.0)
    if jitter >= mean_interval:
        raise WorldTemplateError(
            f"jitter {jitter} must stay below the mean interval "
            f"{mean_interval}; releases must keep their order",
            "lot_release.jitter_days")
    return LotReleasePolicy(
        first_release_day=_as_number(
            _require(obj, "first_release_day", "lot_release"),
            "lot_release.first_release_day", minimum=0.0),
        mean_interval_days=mean_interval,
        jitter_days=jitter,
    )


def _build_queue(raw: Any) -> QueuePolicy:
    obj = _as_object(raw, "queue")
    _reject_unknown(obj, _QUEUE_KEYS, "queue")
    return QueuePolicy(delay=_build_distribution(
        _require(obj, "delay_minutes", "queue"),
        "queue.delay_minutes"))


def _build_code_list(raw: Any, path: str) -> tuple[str, ...]:
    array = _as_array(raw, path, minimum_length=1)
    codes = tuple(_as_text(item, f"{path}[{i}]", _ENTITY_NAME_RE)
                  for i, item in enumerate(array))
    _unique(codes, path, "value")
    return codes


def _build_channel(raw: Any, path: str, operations: Sequence[str],
                   latents: Sequence[str],
                   steps: Sequence[ProcessStep]) -> ObservationChannel:
    obj = _as_object(raw, path)
    _reject_unknown(obj, _CHANNEL_REQUIRED + _CHANNEL_OPTIONAL, path)
    name = _as_text(_require(obj, "name", path), _at(path, "name"), _PARAM_RE)
    kind = _as_choice(_require(obj, "kind", path), _at(path, "kind"),
                      CHANNEL_KINDS)
    operation_path = _at(path, "operation_types")
    declared = _as_array(_require(obj, "operation_types", path),
                         operation_path, minimum_length=1)
    channel_operations = tuple(
        _as_choice(item, f"{operation_path}[{i}]", operations)
        for i, item in enumerate(declared)
    )
    _unique(channel_operations, operation_path, "operation type")

    # A channel must be grounded in something the world already declares: an
    # FDC summary reports a setpoint's delivered value, a metrology channel
    # reports a step metric. A channel grounded in nothing would be a number
    # with no reference — and `SCHEMA_V2_DESIGN.md` §2.14 exists precisely so
    # that FDC deltas *have* a reference.
    if kind == "fdc":
        grounded = any(name in dict(step.settings) for step in steps
                       if step.operation_type in channel_operations)
        what = "a recipe setting"
    else:
        grounded = any(step.metric is not None and step.metric.name == name
                       for step in steps
                       if step.operation_type in channel_operations)
        what = "a step metric"
    if not grounded:
        raise WorldTemplateError(
            f"no {', '.join(channel_operations)} step declares {what} named "
            f"{name!r}; an observation channel reports something the world "
            "declares, not something the observation model invents",
            _at(path, "name"))

    sensitivity_path = _at(path, "sensitivities")
    sensitivities = _as_object(_require(obj, "sensitivities", path),
                               sensitivity_path)
    for key in sensitivities:
        if key not in latents:
            raise WorldTemplateError(
                f"{key!r} is not a declared latent; sensitivities are keyed by "
                "latent, never by an entity or an event",
                _at(sensitivity_path, key))
    return ObservationChannel(
        name=name,
        kind=kind,
        operation_types=channel_operations,
        unit=_as_text(obj.get("unit", ""), _at(path, "unit"),
                      allow_empty=True),
        scale=_as_number(_require(obj, "scale", path), _at(path, "scale"),
                         greater_than=0.0),
        sensitivities=tuple(sorted(
            (key, _as_number(value, _at(sensitivity_path, key)))
            for key, value in sensitivities.items()
        )),
    )


def _build_variation(raw: Any) -> VariationStack:
    obj = _as_object(raw, "observation.variation_stack")
    _reject_unknown(obj, _VARIATION_KEYS, "observation.variation_stack")

    def term(key: str, **bounds: Any) -> float:
        path = f"observation.variation_stack.{key}"
        return _as_number(_require(obj, key, "observation.variation_stack"),
                          path, **bounds)

    return VariationStack(
        fab_week=term("fab_week", minimum=0.0),
        tool_offset=term("tool_offset", minimum=0.0),
        chamber_offset=term("chamber_offset", minimum=0.0),
        # φ = 1 would make the lot term a random walk with no mean to return
        # to, and lot-to-lot wander would stop being wander.
        lot_ar1_phi=term("lot_ar1_phi", minimum=0.0, less_than=1.0),
        lot_ar1_sd=term("lot_ar1_sd", minimum=0.0),
        run_noise=term("run_noise", greater_than=0.0),
        metrology_noise=term("metrology_noise", minimum=0.0),
    )


def _build_classifier(raw: Any) -> ClassifierPolicy:
    path = "observation.classifier"
    obj = _as_object(raw, path)
    _reject_unknown(obj, _CLASSIFIER_KEYS, path)
    classes = _build_code_list(_require(obj, "classes", path),
                               _at(path, "classes"))
    origin_path = _at(path, "origins")
    origins = tuple(
        _as_text(item, f"{origin_path}[{i}]", _PARAM_RE)
        for i, item in enumerate(_as_array(_require(obj, "origins", path),
                                           origin_path, minimum_length=1))
    )
    _unique(origins, origin_path, "defect origin")

    confusion_path = _at(path, "confusion")
    confusion_obj = _as_object(_require(obj, "confusion", path),
                               confusion_path)
    for key in confusion_obj:
        if key not in origins:
            raise WorldTemplateError(
                f"{key!r} is not a declared defect origin; confusion rows are "
                "keyed by origin, never by an entity or an event",
                _at(confusion_path, key))
    rows: list[tuple[str, tuple[tuple[str, float], ...]]] = []
    for origin in origins:
        row_path = _at(confusion_path, origin)
        if origin not in confusion_obj:
            raise WorldTemplateError(
                f"origin {origin!r} has no confusion row; every origin must be "
                "classifiable, or the classifier is a partition of the truth",
                row_path)
        row = _as_object(confusion_obj[origin], row_path)
        weights: list[tuple[str, float]] = []
        for label, value in row.items():
            if label not in classes:
                raise WorldTemplateError(
                    f"{label!r} is not a declared class", _at(row_path, label))
            weights.append((label, _as_number(value, _at(row_path, label),
                                              minimum=0.0, maximum=1.0)))
        total = sum(weight for _label, weight in weights)
        if abs(total - 1.0) > 1e-9:
            raise WorldTemplateError(
                f"probabilities must sum to 1, got {total}", row_path)
        rows.append((origin, tuple(sorted(weights))))
    return ClassifierPolicy(classes=classes, origins=origins,
                            confusion=tuple(rows))


def _build_observation(raw: Any, operations: Sequence[str],
                       steps: Sequence[ProcessStep]) -> ObservationPolicy:
    obj = _as_object(raw, "observation")
    _reject_unknown(obj, _OBSERVATION_KEYS, "observation")

    latent_path = "observation.latents"
    latents = tuple(
        _as_text(item, f"{latent_path}[{i}]", _PARAM_RE)
        for i, item in enumerate(_as_array(_require(obj, "latents",
                                                    "observation"),
                                           latent_path, minimum_length=1))
    )
    _unique(latents, latent_path, "latent")

    zone_path = "observation.wafer_zones"
    zones = tuple(
        _as_text(item, f"{zone_path}[{i}]", _PARAM_RE)
        for i, item in enumerate(_as_array(_require(obj, "wafer_zones",
                                                    "observation"),
                                           zone_path, minimum_length=2))
    )
    _unique(zones, zone_path, "wafer zone")

    channel_path = "observation.channels"
    channels = tuple(
        _build_channel(item, f"{channel_path}[{i}]", operations, latents, steps)
        for i, item in enumerate(_as_array(_require(obj, "channels",
                                                    "observation"),
                                           channel_path, minimum_length=1))
    )
    _unique([c.name for c in channels], channel_path, "channel")

    calibration_path = "observation.severity_calibration"
    calibration_obj = _as_object(_require(obj, "severity_calibration",
                                          "observation"), calibration_path)
    _reject_unknown(calibration_obj, SEVERITY_LEVELS, calibration_path)
    calibration = tuple(
        (level, _as_number(_require(calibration_obj, level, calibration_path),
                           _at(calibration_path, level), greater_than=0.0))
        for level in SEVERITY_LEVELS
    )
    # The difficulty axis has to be an axis: a calibration in which `subtle`
    # were not smaller than `obvious` would make severity a label rather than a
    # detectability setting (`CAUSAL_MECHANISM_MODEL.md` §8).
    sigmas = [sigma for _level, sigma in calibration]
    if sigmas != sorted(sigmas) or len(set(sigmas)) != len(sigmas):
        raise WorldTemplateError(
            "severity must increase strictly from "
            + " to ".join(SEVERITY_LEVELS) + f", got {sigmas}",
            calibration_path)

    return ObservationPolicy(
        latents=latents,
        wafer_zones=zones,
        channels=channels,
        variation=_build_variation(_require(obj, "variation_stack",
                                            "observation")),
        severity_calibration=calibration,
        classifier=_build_classifier(_require(obj, "classifier",
                                              "observation")),
    )


def _build_alarms(raw: Any, operations: Sequence[str], tools: Sequence[Tool],
                  observation: ObservationPolicy) -> AlarmPolicy:
    """The generic observation/response rules the equipment applies.

    Rules describe thresholds on declared signals. Nothing here decides that an
    alarm fires — that is the alarm model's job later — and nothing here can
    name what would make it fire: there is no field for an event, a mechanism,
    a tool or a chamber, so a rule of the shape "if <this chamber is faulty>
    then <this code>" is not expressible.
    """
    obj = _as_object(raw, "alarms")
    _reject_unknown(obj, _ALARM_KEYS, "alarms")
    severities = _build_code_list(_require(obj, "severities", "alarms"),
                                  "alarms.severities")
    channel_names = {c.name for c in observation.channels}
    qualified = {op for tool in tools for op in tool.operations}

    codes: list[AlarmCode] = []
    code_path = "alarms.codes"
    for index, item in enumerate(_as_array(_require(obj, "codes", "alarms"),
                                           code_path, minimum_length=1)):
        path = f"{code_path}[{index}]"
        entry = _as_object(item, path)
        _reject_unknown(entry, _ALARM_CODE_KEYS, path)
        source = _as_choice(_require(entry, "source", path),
                            _at(path, "source"), ALARM_SOURCES)
        signal = _as_text(_require(entry, "signal", path), _at(path, "signal"),
                          _PARAM_RE)
        known = channel_names if source == "channel" else set(
            observation.latents)
        if signal not in known:
            raise WorldTemplateError(
                f"unknown {source} {signal!r}; an alarm rule watches a signal "
                "the world declares", _at(path, "signal"))
        operation_path = _at(path, "operation_types")
        declared = _as_array(_require(entry, "operation_types", path),
                             operation_path, minimum_length=1)
        code_operations = tuple(
            _as_choice(op, f"{operation_path}[{i}]", operations)
            for i, op in enumerate(declared)
        )
        _unique(code_operations, operation_path, "operation type")
        if not any(op in qualified for op in code_operations):
            raise WorldTemplateError(
                "no tool is qualified for any of "
                + ", ".join(code_operations)
                + "; a code no tool can raise has no support anywhere, which "
                "is what makes a code a fingerprint", operation_path)
        codes.append(AlarmCode(
            code=_as_text(_require(entry, "code", path), _at(path, "code"),
                          _OPERATION_RE),
            source=source,
            signal=signal,
            operation_types=code_operations,
            threshold_sigma=_as_number(_require(entry, "threshold_sigma", path),
                                       _at(path, "threshold_sigma"),
                                       greater_than=0.0),
            severity=_as_choice(_require(entry, "severity", path),
                                _at(path, "severity"), severities),
            message=_as_text(_require(entry, "message", path),
                             _at(path, "message")),
        ))
    _unique([c.code for c in codes], code_path, "alarm code")

    return AlarmPolicy(
        severities=severities,
        codes=tuple(codes),
        background_rate_per_chamber_day=_as_number(
            _require(obj, "background_rate_per_chamber_day", "alarms"),
            "alarms.background_rate_per_chamber_day", greater_than=0.0),
        detection_probability=_as_number(
            _require(obj, "detection_probability", "alarms"),
            "alarms.detection_probability", greater_than=0.0, maximum=1.0),
    )


def _build_die_grid(raw: Any, products: Sequence[Product]) -> DieGridPolicy:
    """Physical die geometry, validated against the products it must fit."""
    obj = _as_object(raw, "die_grid")
    _reject_unknown(obj, _DIE_GRID_KEYS, "die_grid")
    edge_exclusion = _as_number(_require(obj, "edge_exclusion_mm", "die_grid"),
                                "die_grid.edge_exclusion_mm", minimum=0.0)
    street = _as_number(_require(obj, "street_width_mm", "die_grid"),
                        "die_grid.street_width_mm", minimum=0.0)
    aspect = _as_number(_require(obj, "die_aspect_ratio", "die_grid"),
                        "die_grid.die_aspect_ratio", greater_than=0.0)

    for product in products:
        usable = product.wafer_size_mm - 2.0 * edge_exclusion
        if usable <= 0.0:
            raise WorldTemplateError(
                f"edge exclusion {edge_exclusion} mm leaves no usable area on "
                f"a {product.wafer_size_mm} mm wafer",
                "die_grid.edge_exclusion_mm")
        width = (product.die_size_mm2 * aspect) ** 0.5 + street
        height = (product.die_size_mm2 / aspect) ** 0.5 + street
        if max(width, height) > usable:
            raise WorldTemplateError(
                f"a {product.die_size_mm2} mm² die of {product.product_name} "
                f"does not fit in {usable} mm of usable wafer", "die_grid")

    return DieGridPolicy(
        edge_exclusion_mm=edge_exclusion,
        street_width_mm=street,
        die_aspect_ratio=aspect,
        origin=_as_choice(obj.get("origin", DIE_ORIGINS[0]),
                          "die_grid.origin", DIE_ORIGINS),
        index_order=_as_choice(obj.get("index_order", DIE_INDEX_ORDERS[0]),
                               "die_grid.index_order", DIE_INDEX_ORDERS),
        partial_die_policy=_as_choice(
            obj.get("partial_die_policy", PARTIAL_DIE_POLICIES[0]),
            "die_grid.partial_die_policy", PARTIAL_DIE_POLICIES),
    )


def _build_maintenance(raw: Any) -> MaintenancePolicy:
    obj = _as_object(raw, "maintenance")
    _reject_unknown(obj, _MAINTENANCE_KEYS, "maintenance")
    interval = _as_number(_require(obj, "pm_interval_days", "maintenance"),
                          "maintenance.pm_interval_days", greater_than=0.0)
    jitter = _as_number(_require(obj, "pm_jitter_days", "maintenance"),
                        "maintenance.pm_jitter_days", minimum=0.0)
    if jitter >= interval:
        raise WorldTemplateError(
            f"jitter {jitter} must stay below the PM interval {interval}",
            "maintenance.pm_jitter_days")
    return MaintenancePolicy(
        pm_interval_days=interval,
        pm_jitter_days=jitter,
        pm_duration=_build_hours(_require(obj, "pm_duration_hours",
                                          "maintenance"),
                                 "maintenance.pm_duration_hours"),
        qual_duration_hours=_as_number(
            _require(obj, "qual_duration_hours", "maintenance"),
            "maintenance.qual_duration_hours", minimum=0.0),
        breakdown_mtbf_days=_as_number(
            _require(obj, "breakdown_mtbf_days", "maintenance"),
            "maintenance.breakdown_mtbf_days", greater_than=0.0),
        breakdown_duration=_build_hours(
            _require(obj, "breakdown_duration_hours", "maintenance"),
            "maintenance.breakdown_duration_hours"),
        technicians=_build_code_list(
            _require(obj, "technicians", "maintenance"),
            "maintenance.technicians"),
        pm_action_codes=_build_code_list(
            _require(obj, "pm_action_codes", "maintenance"),
            "maintenance.pm_action_codes"),
        unscheduled_action_codes=_build_code_list(
            _require(obj, "unscheduled_action_codes", "maintenance"),
            "maintenance.unscheduled_action_codes"),
    )


def _check_relations_on_routes(flows: Sequence[ProcessFlow],
                               flow_steps: Sequence[FlowStep],
                               steps: Sequence[ProcessStep]) -> None:
    """A declared relation must be one a wafer could actually have.

    On every route that carries an observation step, whatever it measures or
    covers has to come earlier on that route: a metrology step cannot read out
    an etch the wafer has not had, and an inspection cannot see defects from a
    deposition still ahead of it. The relations are declared, not inferred —
    but a declaration that contradicts the route is a template bug, and one
    that would otherwise surface as an impossible timestamp far downstream.
    """
    step_by_id = {step.step_id: step for step in steps}
    flow_step_by_id = {fs.flow_step_id: fs for fs in flow_steps}
    for flow in flows:
        route = [step_by_id[flow_step_by_id[fsid].step_id]
                 for fsid in flow.flow_step_ids]
        position = {step.step_id: index for index, step in enumerate(route)}
        for index, step in enumerate(route):
            related = ([step.measures_step_id]
                       if step.measures_step_id is not None else [])
            related += list(step.covers_step_ids)
            for related_id in related:
                seen = position.get(related_id)
                related_step = step_by_id[related_id]
                if seen is None:
                    raise WorldTemplateError(
                        f"step {step.step_name!r} refers to "
                        f"{related_step.step_name!r}, which flow "
                        f"{flow.flow_name!r} does not run", "process_flows")
                if seen >= index:
                    raise WorldTemplateError(
                        f"step {step.step_name!r} refers to "
                        f"{related_step.step_name!r}, which comes later on "
                        f"flow {flow.flow_name!r}; a wafer cannot be observed "
                        "for something it has not had done to it",
                        "process_flows")


def build_world(raw: Mapping[str, Any]) -> World:
    """Validate a world template and instantiate the world it declares.

    Rejects rather than repairs, in the same spirit as the scenario loader: an
    unsupported header, an unknown field, a wrong type, a value outside a
    closed vocabulary, a dangling reference (a product on a flow that does not
    exist, a flow through a step that does not exist, a metrology step
    measuring a step that does not, an alarm rule watching a signal nobody
    declares), a relation pointing at the wrong kind of step or at a step later
    on the route, a confusion row that does not sum to one, a die that does not
    fit its wafer, a duplicate name, or an operation type no tool is qualified
    for. A world that cannot route one of its own steps is not a world.
    """
    obj = _as_object(raw, "")
    _reject_unknown(obj, _TEMPLATE_KEYS, "")

    header = _as_text(_require(obj, HEADER_KEY, ""), HEADER_KEY)
    if header != HEADER_VALUE:
        raise WorldTemplateError(
            f"unsupported world template version {header!r}; this loader "
            f"implements {CONTRACT} (header {HEADER_KEY}: {HEADER_VALUE!r})",
            HEADER_KEY)

    operations = _build_operation_types(_require(obj, "operation_types", ""))
    layers = _build_layers(_require(obj, "layers", ""))
    steps = _build_steps(_require(obj, "process_steps", ""), operations, layers)
    flows, flow_steps = _build_flows(_require(obj, "process_flows", ""), steps)
    products = _build_products(_require(obj, "products", ""), flows)
    tools, chambers = _build_tools(_require(obj, "tools", ""), operations)
    operators = _build_operators(_require(obj, "operators", ""))
    observation = _build_observation(_require(obj, "observation", ""),
                                     operations, steps)

    recipes_obj = _as_object(obj.get("recipes", {}), "recipes")
    _reject_unknown(recipes_obj, _RECIPES_KEYS, "recipes")
    recipe_version = _as_text(recipes_obj.get("version", "1.0"),
                              "recipes.version")

    # Every operation type on a route must be runnable somewhere, or the
    # timeline would deadlock at a step with no candidates.
    qualified = {op for tool in tools for op in tool.operations}
    step_by_id = {s.step_id: s for s in steps}
    flow_step_by_id = {f.flow_step_id: f for f in flow_steps}
    for flow in flows:
        for flow_step_id in flow.flow_step_ids:
            step = step_by_id[flow_step_by_id[flow_step_id].step_id]
            if step.operation_type not in qualified:
                raise WorldTemplateError(
                    f"no tool is qualified for operation type "
                    f"{step.operation_type!r}, required by step "
                    f"{step.step_name!r} of flow {flow.flow_name!r}", "tools")

    _check_relations_on_routes(flows, flow_steps, steps)

    return World(
        template_name=_as_text(_require(obj, "name", ""), "name",
                               _IDENTIFIER_RE),
        description=_as_text(obj.get("description", ""), "description",
                             allow_empty=True),
        time_origin=_as_datetime(_require(obj, "time_origin", ""),
                                 "time_origin"),
        wafers_per_lot=_as_int(_require(obj, "wafers_per_lot", ""),
                               "wafers_per_lot", minimum=1),
        operation_types=operations,
        layers=layers,
        products=products,
        process_steps=steps,
        process_flows=flows,
        flow_steps=flow_steps,
        tools=tools,
        chambers=chambers,
        recipes=_build_recipes(products, flows, flow_steps, steps,
                               recipe_version),
        operators=operators,
        routing=_build_routing(_require(obj, "routing", ""), products, tools,
                               operations),
        lot_release=_build_lot_release(_require(obj, "lot_release", "")),
        queue=_build_queue(_require(obj, "queue", "")),
        maintenance=_build_maintenance(_require(obj, "maintenance", "")),
        observation=observation,
        alarms=_build_alarms(_require(obj, "alarms", ""), operations, tools,
                             observation),
        die_grid=_build_die_grid(_require(obj, "die_grid", ""), products),
        world_sha256=world_sha256(obj),
    )
