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

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "CONTRACT",
    "HEADER_KEY",
    "HEADER_VALUE",
    "MINUTES_PER_DAY",
    "SHIFTS",
    "WORLD_TEMPLATE_ROOT",
    "Chamber",
    "Dedication",
    "HourRange",
    "FlowStep",
    "LotReleasePolicy",
    "MaintenancePolicy",
    "MinuteDistribution",
    "Operator",
    "ProcessFlow",
    "ProcessStep",
    "Product",
    "QueuePolicy",
    "Recipe",
    "RoutingPolicy",
    "StepMetric",
    "Tool",
    "World",
    "WorldTemplateError",
    "available_worlds",
    "build_world",
    "load_world",
    "load_world_template",
    "world_template_path",
]

# ------------------------------------------------------------------ contract

#: The world template contract this loader implements. In a file it is spelled
#: as the header field ``"fabsim": "world/v1"``, exactly as the scenario
#: contract spells its own header.
CONTRACT = "fabsim.world/v1"
HEADER_KEY = "fabsim"
HEADER_VALUE = "world/v1"

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

_TEMPLATE_REQUIRED = (HEADER_KEY, "name", "time_origin", "wafers_per_lot",
                      "operation_types", "products", "process_steps",
                      "process_flows", "tools", "operators", "routing",
                      "lot_release", "queue", "maintenance")
_TEMPLATE_OPTIONAL = ("description", "recipes")
_TEMPLATE_KEYS = _TEMPLATE_REQUIRED + _TEMPLATE_OPTIONAL

_PRODUCT_REQUIRED = ("name", "flow", "technology_node_nm", "wafer_size_mm",
                     "die_size_mm2", "target_yield_pct", "mix_weight",
                     "metric_scale")
_STEP_REQUIRED = ("name", "operation_type", "duration_minutes")
_STEP_OPTIONAL = ("is_inspection", "metric", "settings")
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
_DEDICATION_KEYS = ("product", "tool", "operation_type", "start_day", "end_day")
_LOT_RELEASE_KEYS = ("first_release_day", "mean_interval_days", "jitter_days")
_QUEUE_KEYS = ("delay_minutes",)
_MAINTENANCE_KEYS = ("pm_interval_days", "pm_jitter_days", "pm_duration_hours",
                     "qual_duration_hours", "breakdown_mtbf_days",
                     "breakdown_duration_hours", "technicians",
                     "pm_action_codes", "unscheduled_action_codes")
_RECIPES_KEYS = ("version",)

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
               greater_than: float | None = None) -> float:
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
    """A product preferentially routed to one tool during a window.

    Dedication is a *routing policy*, declared by the world template and
    therefore visible in the emitted routing shares — the confounder of
    scenario G is honest data, not hidden state (`SCENARIO_SPECIFICATION.md`
    §4 G). The baseline world declares none.
    """

    product_name: str
    tool_name: str
    operation_type: str
    start_day: float
    end_day: float

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


def _build_steps(raw: Any, operations: Sequence[str]) -> tuple[ProcessStep, ...]:
    array = _as_array(raw, "process_steps", minimum_length=1)
    steps: list[ProcessStep] = []
    for index, item in enumerate(array):
        path = f"process_steps[{index}]"
        obj = _as_object(item, path)
        _reject_unknown(obj, _STEP_REQUIRED + _STEP_OPTIONAL, path)
        steps.append(ProcessStep(
            step_id=index + 1,
            step_name=_as_text(_require(obj, "name", path), _at(path, "name"),
                               _ENTITY_NAME_RE),
            operation_type=_as_choice(_require(obj, "operation_type", path),
                                      _at(path, "operation_type"), operations),
            is_inspection=_as_bool(obj.get("is_inspection", False),
                                   _at(path, "is_inspection")),
            duration=_build_distribution(
                _require(obj, "duration_minutes", path),
                _at(path, "duration_minutes")),
            metric=_build_metric(obj.get("metric"), _at(path, "metric")),
            settings=_build_settings(obj.get("settings", {}),
                                     _at(path, "settings")),
        ))
    _unique([s.step_name for s in steps], "process_steps", "step name")
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
        dedications.append(Dedication(
            product_name=product_name, tool_name=tool_name,
            operation_type=operation, start_day=start_day, end_day=end_day,
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


def build_world(raw: Mapping[str, Any]) -> World:
    """Validate a world template and instantiate the world it declares.

    Rejects rather than repairs, in the same spirit as the scenario loader: an
    unsupported header, an unknown field, a wrong type, a value outside a
    closed vocabulary, a dangling reference (a product on a flow that does not
    exist, a flow through a step that does not exist), a duplicate name, or an
    operation type no tool is qualified for. A world that cannot route one of
    its own steps is not a world.
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
    steps = _build_steps(_require(obj, "process_steps", ""), operations)
    flows, flow_steps = _build_flows(_require(obj, "process_flows", ""), steps)
    products = _build_products(_require(obj, "products", ""), flows)
    tools, chambers = _build_tools(_require(obj, "tools", ""), operations)
    operators = _build_operators(_require(obj, "operators", ""))

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
    )
