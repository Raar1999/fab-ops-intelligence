"""
Contract tests for `fabsim.routing` — where a scenario's routing conditions
meet a world's rosters.

`fabsim.scenario` checks that a condition is *shaped* right; this module checks
that it is *true of a world*: the product exists, the tool exists, the
operation type exists, and the tool is actually qualified to run it. A
dedication to a tool that cannot run the step is not a weak preference, it is a
statement about a fab that does not exist, and a build that accepted it would
produce a window in which nothing happened and no error said why.

What a resolved dedication then *does* to traffic is `test_timeline.py`.
"""
from __future__ import annotations

from typing import Any

import pytest

from fabsim.routing import (
    ROUTING_CONDITION_KINDS,
    dedication_in_force,
    effective_dedications,
    resolve_routing_conditions,
)
from fabsim.scenario import ScenarioConfigError, from_mapping
from fabsim.world import Dedication

SCENARIO: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "confounded",
    "world": "baseline_fab_v1",
    "horizon_days": 84,
    "lots": 20,
    "default_seed": 42,
}

CONDITION: dict[str, Any] = {
    "kind": "product_dedication",
    "product": "Mobile-28",
    "tool": "ETCH-01",
    "operation_type": "ETCH",
    "start_day": 28.0,
    "end_day": 62.0,
    "share": 0.85,
}


def condition(**overrides: Any) -> dict[str, Any]:
    raw = dict(CONDITION)
    raw.update(overrides)
    return raw


def config(*conditions: dict[str, Any]):
    return from_mapping({**SCENARIO, "routing_conditions": list(conditions)})


# ------------------------------------------------------------- resolution


def test_a_valid_condition_binds_to_the_world(world):
    resolved = resolve_routing_conditions(world, [condition()])
    assert resolved == (Dedication(product_name="Mobile-28",
                                   tool_name="ETCH-01",
                                   operation_type="ETCH", start_day=28.0,
                                   end_day=62.0, share=0.85),)


def test_no_conditions_resolve_to_nothing(world):
    assert resolve_routing_conditions(world) == ()
    assert resolve_routing_conditions(world, []) == ()


def test_conditions_keep_their_declared_order(world):
    first = condition()
    second = condition(product="Logic-14", tool="ETCH-02")
    resolved = resolve_routing_conditions(world, [first, second])
    assert [d.tool_name for d in resolved] == ["ETCH-01", "ETCH-02"]


@pytest.mark.parametrize("overrides, path, message", [
    ({"product": "Nope-99"}, "routing_conditions[0].product",
     "unknown product"),
    ({"tool": "ETCH-99"}, "routing_conditions[0].tool", "unknown tool"),
    ({"operation_type": "PLASMA_MAGIC"},
     "routing_conditions[0].operation_type", "unknown operation type"),
    ({"tool": "CVD-01"}, "routing_conditions[0].operation_type",
     "not qualified"),
])
def test_a_condition_the_world_cannot_satisfy_is_rejected(world, overrides,
                                                          path, message):
    with pytest.raises(ScenarioConfigError) as excinfo:
        resolve_routing_conditions(world, [condition(**overrides)])
    assert excinfo.value.path == path
    assert message in str(excinfo.value)


def test_the_rejection_names_the_offending_condition(world):
    with pytest.raises(ScenarioConfigError) as excinfo:
        resolve_routing_conditions(world, [condition(),
                                           condition(tool="ETCH-99")])
    assert excinfo.value.path == "routing_conditions[1].tool"


def test_an_unknown_kind_is_rejected(world):
    assert ROUTING_CONDITION_KINDS == ("product_dedication",)
    with pytest.raises(ScenarioConfigError) as excinfo:
        resolve_routing_conditions(world, [condition(kind="tool_swap")])
    assert excinfo.value.path == "routing_conditions[0].kind"


# ------------------------------------------------------------- the layering


def test_a_scenario_layers_over_the_worlds_standing_policy(world,
                                                           make_world):
    """The world keeps the machinery; the scenario states the experiment."""
    standing = {"product": "Logic-14", "tool": "ETCH-03",
                "operation_type": "ETCH", "start_day": 0.0, "end_day": 84.0,
                "share": 0.5}
    with_policy = make_world(routing={"stickiness": 0.6,
                                      "dedications": [standing]})

    assert effective_dedications(world) == ()
    assert effective_dedications(world, config(condition())) == (
        resolve_routing_conditions(world, [condition()]))

    layered = effective_dedications(with_policy, config(condition()))
    assert [d.tool_name for d in layered] == ["ETCH-01", "ETCH-03"]
    assert effective_dedications(with_policy) == with_policy.routing.dedications


def test_the_baseline_world_declares_no_standing_dedication(world):
    """Every library scenario shares one world (rule D7); a standing
    confounder in it would be a confounder in all five."""
    assert world.routing.dedications == ()


# --------------------------------------------------------- what is in force


def test_a_dedication_covers_only_its_product_operation_and_window(world):
    (dedication,) = resolve_routing_conditions(world, [condition()])
    assert dedication_in_force([dedication], "Mobile-28", "ETCH", 30.0)
    assert dedication_in_force([dedication], "Mobile-28", "ETCH", 28.0)
    assert dedication_in_force([dedication], "Mobile-28", "ETCH",
                               61.999) is not None
    for absent in (
        ("Logic-14", "ETCH", 30.0),      # another product
        ("Mobile-28", "CVD", 30.0),      # another operation type
        ("Mobile-28", "ETCH", 27.99),    # before the window
        ("Mobile-28", "ETCH", 62.0),     # the window is half-open
    ):
        assert dedication_in_force([dedication], *absent) is None


def test_the_first_covering_condition_wins(world):
    """Overlapping shares have no well-defined sum; order decides, and the
    order is the one the config declared."""
    resolved = resolve_routing_conditions(
        world, [condition(share=0.8), condition(tool="ETCH-02", share=0.4)])
    assert dedication_in_force(resolved, "Mobile-28", "ETCH",
                               30.0).tool_name == "ETCH-01"


# -------------------------------------------------------------- neutrality


def test_routing_never_reads_events_or_distractors(world):
    """A routing condition is observable policy; a fault is not, and the two
    do not meet in this module."""
    faulted = from_mapping({
        **SCENARIO,
        "routing_conditions": [condition()],
        "events": [{"mechanism": "chamber_edge_uniformity",
                    "target": {"tool": "ETCH-02", "chamber": "B"},
                    "onset_day": 35, "severity": "obvious"}],
        "distractors": [{"mechanism": "benign_offset",
                         "target": {"tool": "CVD-01"},
                         "magnitude": "large"}],
    })
    assert (effective_dedications(world, faulted)
            == effective_dedications(world, config(condition())))
