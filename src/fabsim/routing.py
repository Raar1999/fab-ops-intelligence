"""
routing.py — where a scenario's routing conditions meet a world's routing
machinery, and what a dedication actually does to a routing decision.

Dedication is **layered** (ADR-015)::

    world template  routing.dedications      standing policy — how this fab
                                             normally allocates its traffic
            +
    scenario        routing_conditions       a time-bounded experimental
                                             condition laid on top of it
            ↓
    fabsim.routing  effective_dedications    one ordered list, scenario first
            ↓
    fabsim.timeline _route                   a share of exposure, per decision

The two layers exist because they answer different questions. "This fab
prefers ETCH-01 for its highest-volume product" is a property of the fab and
belongs with the other routing constants; "during days 28–62 of *this*
experiment, that preference is 0.85" is a property of the experiment and would
be a lie if it were baked into a world that other scenarios share. Putting the
window in the world would also mean scenario G needed a world of its own, and
then the anti-leakage rule that all scenarios share one world (D7) would be
carrying an exception whose only purpose was the confounder.

**A dedication is a share, not a filter.** This is the substantive change of
Step 3.0 and the reason the module exists as a named thing rather than as four
lines inside the scheduler::

    dedication in force
            ↓
    with probability `share`   → this decision is restricted to the tool
    otherwise                  → this decision sees the whole qualified pool
            ↓
    stickiness and earliest-available then choose *within* whatever pool
    survived — qualification, chamber eligibility and availability are
    untouched

The realized share is therefore a little above `share`, because the remaining
traffic can still reach the dedicated tool on availability. That is the
intended shape: a dedication moves exposure probability, and exposure that
could only ever have gone one way is not a probability.

A hard filter — the Step 2 behaviour this replaces — would make product and
chamber exposure the *same* variable inside the window. Scenario G's whole
demand is that "the chamber effect survives within-product comparison, and the
product effect does not survive within-chamber comparison"
(`SCENARIO_SPECIFICATION.md` §4 G); under a filter, neither comparison has any
data, and the benchmark would be scoring an impossibility.

**Dedication is tool-level.** There is no chamber field in either layer.
Aiming traffic at a chamber would aim it at exactly the grain a fault is
attributed at, and the confounder would stop being something to untangle and
become a pointer at the answer.

What this module does *not* do: read `events` or `distractors`, look at latent
state, or know that faults exist. Routing conditions are honest, observable
data — the routing shift shows up in `runs` — and the code path from a fault
to a routing decision does not exist here to be misused.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from fabsim.scenario import (
    ROUTING_CONDITION_KINDS,
    ScenarioConfig,
    ScenarioConfigError,
)
from fabsim.world import Dedication, World

__all__ = [
    "ROUTING_CONDITION_KINDS",
    "dedication_in_force",
    "effective_dedications",
    "resolve_routing_conditions",
]


def _resolve_product_dedication(world: World, condition: Mapping[str, Any],
                                path: str) -> Dedication:
    """Check one condition against the world's rosters and bind it.

    Every reference must resolve, and the tool must actually be qualified for
    the operation type: a dedication to a tool that cannot run the step is not
    a weak preference, it is a statement about a fab that does not exist.
    """
    product_name = condition["product"]
    if product_name not in {p.product_name for p in world.products}:
        raise ScenarioConfigError(
            f"unknown product {product_name!r} in world "
            f"{world.template_name!r}", f"{path}.product")

    tool_name = condition["tool"]
    tools_by_name = {t.tool_name: t for t in world.tools}
    if tool_name not in tools_by_name:
        raise ScenarioConfigError(
            f"unknown tool {tool_name!r} in world {world.template_name!r}",
            f"{path}.tool")

    operation = condition["operation_type"]
    if operation not in world.operation_types:
        raise ScenarioConfigError(
            f"unknown operation type {operation!r} in world "
            f"{world.template_name!r}", f"{path}.operation_type")
    if operation not in tools_by_name[tool_name].operations:
        raise ScenarioConfigError(
            f"tool {tool_name!r} is not qualified for {operation!r}, so it "
            "cannot be dedicated to it", f"{path}.operation_type")

    return Dedication(
        product_name=product_name,
        tool_name=tool_name,
        operation_type=operation,
        start_day=condition["start_day"],
        end_day=condition["end_day"],
        share=condition["share"],
    )


def resolve_routing_conditions(
    world: World,
    conditions: Sequence[Mapping[str, Any]] = (),
) -> tuple[Dedication, ...]:
    """Bind canonical routing conditions to a world, in declaration order.

    Takes the canonical form `fabsim.scenario` produces — so the structural
    validation (closed `kind` vocabulary, share in (0, 1), ordered window, no
    chamber field) has already happened, and what is left is exactly the part
    that needs a world to answer.
    """
    resolved: list[Dedication] = []
    for index, condition in enumerate(conditions):
        path = f"routing_conditions[{index}]"
        kind = condition.get("kind")
        if kind not in ROUTING_CONDITION_KINDS:
            raise ScenarioConfigError(
                f"unknown routing condition kind {kind!r}", f"{path}.kind")
        resolved.append(_resolve_product_dedication(world, condition, path))
    return tuple(resolved)


def effective_dedications(
    world: World,
    config: ScenarioConfig | None = None,
) -> tuple[Dedication, ...]:
    """The world's standing policy with a scenario's conditions layered on.

    Scenario conditions come first because they are the experiment: where a
    condition and the standing policy both cover the same (product, operation,
    day), the condition is what the experiment asked for and the standing
    policy is the background it is being contrasted against.
    """
    conditions = () if config is None else config.routing_conditions
    return (resolve_routing_conditions(world, conditions)
            + world.routing.dedications)


def dedication_in_force(dedications: Sequence[Dedication], product_name: str,
                        operation_type: str, day: float) -> Dedication | None:
    """The first dedication covering this decision, or `None`.

    First rather than combined: two overlapping shares have no well-defined
    sum, and a rule that silently blended them would make the realized share
    depend on declaration order in a way nobody could read off the config.
    Order decides, and the order is documented.
    """
    for dedication in dedications:
        if dedication.covers(product_name, operation_type, day):
            return dedication
    return None
