"""
Contract and integrity tests for `fabsim.world`.

Two things are under test. The template loader rejects what a world must never
silently accept — a dangling reference, a minted vocabulary value, a step no
tool can run. And the instantiated world has the *shape* the design requires:
several products, several tools per operation, several chambers per tool, and
one shared candidate pool at the two etch steps. That shape is not decoration;
a world that collapses to one product on one chamber would make every later
attribution question trivially answerable, which is exactly what the audit
found and what this slice exists to prevent.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from fabsim.world import (
    CONTRACT,
    MINUTES_PER_DAY,
    SHIFTS,
    MinuteDistribution,
    WorldTemplateError,
    available_worlds,
    build_world,
    load_world,
    load_world_template,
    world_template_path,
)

BASELINE = "baseline_fab_v1"


# ------------------------------------------------------------- the registry


def test_baseline_template_resolves_by_name():
    world = load_world(BASELINE)
    assert world.template_name == BASELINE


def test_registry_lists_the_baseline_world():
    assert BASELINE in available_worlds()


def test_template_path_is_the_name_plus_json():
    assert world_template_path(BASELINE).name == f"{BASELINE}.json"


def test_unknown_template_names_what_the_registry_holds():
    with pytest.raises(WorldTemplateError) as excinfo:
        load_world("no_such_world")
    message = str(excinfo.value)
    assert "no_such_world" in message
    assert BASELINE in message


def test_template_file_name_must_match_declared_name(tmp_path: Path,
                                                     template: dict[str, Any]):
    import json

    (tmp_path / "other_name.json").write_text(json.dumps(template),
                                              encoding="utf-8")
    with pytest.raises(WorldTemplateError, match="declares name"):
        load_world("other_name", tmp_path)


def test_a_byte_order_mark_is_not_a_difference(tmp_path: Path,
                                               template: dict[str, Any]):
    import json

    path = tmp_path / f"{BASELINE}.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(template).encode("utf-8"))
    assert load_world(BASELINE, tmp_path) == load_world(BASELINE)


# --------------------------------------------------------- loader strictness


def test_rejects_an_unsupported_header(make_template):
    with pytest.raises(WorldTemplateError, match=CONTRACT):
        build_world(make_template(fabsim="world/v2"))


def test_rejects_an_unknown_top_level_field(make_template):
    with pytest.raises(WorldTemplateError, match="unknown field"):
        build_world(make_template(yield_model={}))


def test_rejects_a_missing_required_field(make_template):
    raw = make_template()
    del raw["routing"]
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "routing"


def test_rejects_duplicate_json_keys(tmp_path: Path):
    path = tmp_path / f"{BASELINE}.json"
    path.write_text('{"fabsim": "world/v1", "name": "a", "name": "b"}',
                    encoding="utf-8")
    with pytest.raises(WorldTemplateError, match="duplicate key"):
        load_world_template(BASELINE, tmp_path)


def test_rejects_non_finite_numbers(tmp_path: Path):
    path = tmp_path / f"{BASELINE}.json"
    path.write_text('{"fabsim": "world/v1", "wafers_per_lot": NaN}',
                    encoding="utf-8")
    with pytest.raises(WorldTemplateError, match="NaN"):
        load_world_template(BASELINE, tmp_path)


def test_rejects_a_step_naming_an_undeclared_operation_type(make_template):
    raw = make_template()
    raw["process_steps"][0]["operation_type"] = "PLASMA_MAGIC"
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "process_steps[0].operation_type"


def test_rejects_a_flow_through_an_unknown_step(make_template):
    raw = make_template()
    raw["process_flows"][0]["steps"][2] = "NOT_A_STEP"
    with pytest.raises(WorldTemplateError, match="unknown process step"):
        build_world(raw)


def test_rejects_a_product_on_an_unknown_flow(make_template):
    raw = make_template()
    raw["products"][0]["flow"] = "no_such_flow"
    with pytest.raises(WorldTemplateError, match="unknown process flow"):
        build_world(raw)


def test_rejects_duplicate_tool_names(make_template):
    raw = make_template()
    raw["tools"][1]["name"] = raw["tools"][0]["name"]
    with pytest.raises(WorldTemplateError, match="duplicate tool name"):
        build_world(raw)


def test_rejects_duplicate_chamber_names_within_a_tool(make_template):
    raw = make_template()
    etch = next(t for t in raw["tools"] if t["name"] == "ETCH-02")
    etch["chambers"][1]["name"] = etch["chambers"][0]["name"]
    with pytest.raises(WorldTemplateError, match="duplicate chamber name"):
        build_world(raw)


def test_the_same_chamber_name_on_two_tools_is_fine(world):
    """`A` on ETCH-01 and `A` on ETCH-02 are different chambers."""
    first = world.chamber_by_name("ETCH-01", "A")
    second = world.chamber_by_name("ETCH-02", "A")
    assert first.chamber_id != second.chamber_id


def test_rejects_a_tool_with_no_chambers(make_template):
    raw = make_template()
    raw["tools"][0]["chambers"] = []
    with pytest.raises(WorldTemplateError, match="at least 1 item"):
        build_world(raw)


def test_rejects_a_world_that_cannot_route_one_of_its_own_steps(make_template):
    raw = make_template()
    raw["tools"] = [t for t in raw["tools"] if t["tool_type"] != "ETCH"]
    with pytest.raises(WorldTemplateError, match="no tool is qualified"):
        build_world(raw)


def test_rejects_stickiness_outside_zero_to_one(make_template):
    with pytest.raises(WorldTemplateError, match="must be <= 1"):
        build_world(make_template(routing={"stickiness": 1.4}))


def test_rejects_a_dedication_to_an_unqualified_tool(make_template):
    dedication = {"product": "Mobile-28", "tool": "CVD-01",
                  "operation_type": "ETCH", "start_day": 10.0,
                  "end_day": 20.0}
    with pytest.raises(WorldTemplateError, match="not qualified"):
        build_world(make_template(routing={"stickiness": 0.6,
                                           "dedications": [dedication]}))


def test_rejects_a_dedication_for_an_unknown_product(make_template):
    dedication = {"product": "Nope-99", "tool": "ETCH-01",
                  "operation_type": "ETCH", "start_day": 10.0,
                  "end_day": 20.0}
    with pytest.raises(WorldTemplateError, match="unknown product"):
        build_world(make_template(routing={"stickiness": 0.6,
                                           "dedications": [dedication]}))


def test_rejects_a_dedication_window_that_ends_before_it_starts(make_template):
    dedication = {"product": "Mobile-28", "tool": "ETCH-01",
                  "operation_type": "ETCH", "start_day": 30.0,
                  "end_day": 30.0}
    with pytest.raises(WorldTemplateError, match="must be > 30"):
        build_world(make_template(routing={"stickiness": 0.6,
                                           "dedications": [dedication]}))


def test_rejects_release_jitter_that_could_reorder_lots(make_template):
    with pytest.raises(WorldTemplateError, match="must stay below"):
        build_world(make_template(lot_release={"first_release_day": 0.0,
                                               "mean_interval_days": 4.0,
                                               "jitter_days": 4.0}))


def test_rejects_an_unstaffed_shift(make_template):
    raw = make_template()
    raw["operators"] = [o for o in raw["operators"] if o["shift"] != "C"]
    with pytest.raises(WorldTemplateError, match="shift must be staffed"):
        build_world(raw)


def test_rejects_a_duration_whose_floor_exceeds_its_mean(make_template):
    raw = make_template()
    raw["process_steps"][0]["duration_minutes"] = {"mean": 10.0, "sd": 1.0,
                                                   "min": 40.0}
    with pytest.raises(WorldTemplateError, match="exceeds mean"):
        build_world(raw)


def test_rejects_a_time_origin_with_a_time_zone(make_template):
    with pytest.raises(WorldTemplateError, match="naive"):
        build_world(make_template(time_origin="2026-03-02T00:00:00+02:00"))


def test_rejects_a_wrong_type_naming_the_field(make_template):
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(make_template(wafers_per_lot=25.0))
    assert excinfo.value.path == "wafers_per_lot"
    assert "integer" in str(excinfo.value)


# ------------------------------------------------------------ the entity set


def test_baseline_roster_matches_the_specified_world(world):
    """`SCENARIO_SPECIFICATION.md` §4: 6 products, one 14-step flow, 15 tools."""
    assert len(world.products) == 6
    assert len(world.process_steps) == 14
    assert len(world.process_flows) == 1
    assert len(world.flow_steps) == 14
    assert len(world.tools) == 15
    assert len(world.chambers) == 24
    assert len(world.operators) == 9
    assert world.wafers_per_lot == 25


def test_ids_are_assigned_in_template_order_from_one(world, template):
    for entities, key, names in (
        (world.products, "product_name", [p["name"] for p in template["products"]]),
        (world.process_steps, "step_name",
         [s["name"] for s in template["process_steps"]]),
        (world.tools, "tool_name", [t["name"] for t in template["tools"]]),
        (world.operators, "operator_name",
         [o["name"] for o in template["operators"]]),
    ):
        assert [getattr(e, key) for e in entities] == names
    for index, product in enumerate(world.products):
        assert product.product_id == index + 1
    for index, chamber in enumerate(world.chambers):
        assert chamber.chamber_id == index + 1


def test_every_product_references_a_flow_that_exists(world):
    for product in world.products:
        assert world.flow(product.flow_id).flow_id == product.flow_id


def test_the_route_is_a_contiguous_sequence_of_real_steps(world):
    for flow in world.process_flows:
        route = world.flow_steps_of(flow.flow_id)
        assert [fs.step_sequence for fs in route] == list(
            range(1, len(route) + 1))
        for flow_step in route:
            assert world.step(flow_step.step_id).step_id == flow_step.step_id
            assert flow_step.flow_id == flow.flow_id


def test_every_chamber_belongs_to_the_tool_that_declares_it(world):
    for tool in world.tools:
        for chamber in world.chambers_of(tool.tool_id):
            assert chamber.tool_id == tool.tool_id
    assert sorted(c.chamber_id for c in world.chambers) == sorted(
        cid for tool in world.tools for cid in tool.chamber_ids)


def test_every_flow_step_has_candidates_and_they_are_qualified(world):
    for flow_step in world.flow_steps:
        operation = world.step(flow_step.step_id).operation_type
        candidates = world.eligible_chambers(flow_step.flow_step_id)
        assert candidates, flow_step
        for chamber in candidates:
            assert operation in world.tool(chamber.tool_id).operations


def test_candidates_are_returned_in_chamber_id_order(world):
    for flow_step in world.flow_steps:
        ids = [c.chamber_id
               for c in world.eligible_chambers(flow_step.flow_step_id)]
        assert ids == sorted(ids)


def test_the_world_does_not_collapse_to_one_tool_one_chamber(world):
    """The shape that would make later attribution trivial must be absent."""
    assert len({p.product_name for p in world.products}) > 1
    multi_chamber = [t for t in world.tools if len(t.chamber_ids) > 1]
    assert len(multi_chamber) >= 5
    assert max(len(t.chamber_ids) for t in world.tools) >= 3
    etch_tools = world.tools_for_operation("ETCH")
    assert len(etch_tools) >= 3
    assert sum(len(t.chamber_ids) for t in etch_tools) >= 6


def test_gate_and_metal_etch_share_one_candidate_pool(world):
    """Structural precondition for independent assignment at the two steps."""
    gate = world.step_by_name("GATE_ETCH")
    metal = world.step_by_name("METAL_ETCH")
    assert gate.step_id != metal.step_id
    gate_step = next(fs for fs in world.flow_steps if fs.step_id == gate.step_id)
    metal_step = next(fs for fs in world.flow_steps
                      if fs.step_id == metal.step_id)
    gate_pool = world.eligible_chambers(gate_step.flow_step_id)
    metal_pool = world.eligible_chambers(metal_step.flow_step_id)
    assert gate_pool == metal_pool
    assert len(gate_pool) >= 6


def test_a_tool_may_serve_several_steps_and_a_step_several_tools(world):
    litho_steps = [fs for fs in world.flow_steps
                   if world.step(fs.step_id).operation_type == "LITHO"]
    assert len(litho_steps) >= 2
    assert len(world.tools_for_operation("LITHO")) >= 2


def test_inspection_steps_are_flagged_and_use_inspection_tools(world):
    inspection = [s for s in world.process_steps if s.is_inspection]
    assert len(inspection) >= 2
    for step in inspection:
        assert step.operation_type == "INSPECTION"
    # Metrology is a separate operation from defect inspection: the audited
    # "CD-SEM doing defect scans" anomaly is not representable here.
    assert not any(s.is_inspection for s in world.process_steps
                   if s.operation_type == "METROLOGY")


# ---------------------------------------------------------------- recipes


def test_one_recipe_per_product_and_step_on_its_route(world):
    for product in world.products:
        route = world.flow_steps_of(product.flow_id)
        step_ids = {fs.step_id for fs in route}
        for step_id in step_ids:
            recipe = world.recipe_for(step_id, product.product_id)
            assert recipe.step_id == step_id
            assert recipe.product_id == product.product_id
    assert len(world.recipes) == len(world.products) * len(world.process_steps)


def test_recipe_specs_are_product_specific_and_ordered(world):
    step_id = world.step_by_name("GATE_ETCH").step_id
    targets = {}
    for product in world.products:
        recipe = world.recipe_for(step_id, product.product_id)
        assert recipe.metric_name == "cd_nm"
        assert recipe.metric_lsl < recipe.metric_target < recipe.metric_usl
        targets[product.product_name] = recipe.metric_target
    assert len(set(targets.values())) == len(targets)
    # Smaller node, smaller feature: the spec follows the product, not a tool.
    assert targets["Logic-14"] < targets["Mobile-28"] < targets["Sensor-90"]


def test_steps_without_a_metric_produce_recipes_without_one(world):
    step_id = world.step_by_name("GATE_CD_METROLOGY").step_id
    recipe = world.recipe_for(step_id, world.products[0].product_id)
    assert recipe.metric_name is None
    assert recipe.metric_target is None and recipe.metric_usl is None


def test_recipes_carry_the_step_setpoints(world):
    step = world.step_by_name("GATE_ETCH")
    recipe = world.recipe_for(step.step_id, world.products[0].product_id)
    settings = dict(recipe.settings)
    assert settings["chamber_pressure_mtorr"] == 12.0
    assert settings["rf_power_w"] == 850.0
    assert list(settings) == sorted(settings)


# ------------------------------------------------------------ clock helpers


def test_minute_offsets_map_onto_the_declared_time_origin(world):
    assert world.at(0) == world.time_origin
    assert (world.at(MINUTES_PER_DAY) - world.time_origin).days == 1


def test_shifts_partition_the_day(world):
    assert world.shift_at(0) == "A"
    assert world.shift_at(8 * 60) == "B"
    assert world.shift_at(16 * 60) == "C"
    assert world.shift_at(MINUTES_PER_DAY) == "A"
    for shift in SHIFTS:
        assert world.operators_on_shift(shift)


def test_durations_respect_their_floor():
    import random

    distribution = MinuteDistribution(mean=10.0, sd=50.0, minimum=6.0)
    rng = random.Random(0)
    assert all(distribution.draw(rng) >= 6 for _ in range(200))


# ---------------------------------------------------------------- neutrality


def test_building_a_world_is_a_pure_function(template):
    assert build_world(template) == build_world(template)


def _template_values(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string *value* in a template, with its path. Prose excluded."""
    if isinstance(node, dict):
        return [pair
                for key, value in node.items() if key != "description"
                for pair in _template_values(value, f"{path}.{key}")]
    if isinstance(node, list):
        return [pair for index, item in enumerate(node)
                for pair in _template_values(item, f"{path}[{index}]")]
    return [(path, node)] if isinstance(node, str) else []


def test_the_template_vocabulary_names_no_conclusion(template):
    """L1 in miniature: no value a dataset could carry names an answer.

    Descriptions are prose for maintainers and are excluded — they never reach
    an observable. Everything else (entity names, states, action codes,
    technicians, parameter names) is vocabulary the emitted data will use.
    """
    forbidden = ("fault", "truth", "scenario", "suspect", "marginal",
                 "inject", "ground", "bad")
    hits = [(path, value) for path, value in _template_values(template)
            for token in forbidden if token in value.lower()]
    assert hits == []


def _code_strings(module: Path) -> list[str]:
    """String literals in a module, excluding docstrings (and comments)."""
    import ast

    tree = ast.parse(module.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str) and id(node) not in docstrings]


def test_no_code_constant_is_keyed_to_a_named_tool_or_chamber():
    """Rule D6: behaviour may never be keyed by a specific entity name.

    Prose may cite ETCH-02 as an example; *code* may not, because a tool name
    in a constant is how a generator quietly learns its own answer. Docstrings
    and comments are excluded; string literals in code are not.
    """
    source = Path(__file__).resolve().parents[2] / "src" / "fabsim"
    pattern = re.compile(r"(ETCH|CVD|LITHO|CMP|PVD|FURN|IMP|MET|INSP|TEST)-\d")
    for module in sorted(source.glob("*.py")):
        hits = [text for text in _code_strings(module) if pattern.search(text)]
        assert hits == [], module


def test_fabsim_never_imports_fabops():
    """ADR-013: fabsim writes datasets, fabops reads them; neither imports."""
    import ast

    source = Path(__file__).resolve().parents[2] / "src" / "fabsim"
    for module in sorted(source.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [name for name in imported
                    if name == "fabops" or name.startswith("fabops.")], module
