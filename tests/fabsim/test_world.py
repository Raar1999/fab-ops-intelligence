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
    CHANNEL_KINDS,
    CONTRACT,
    LATENT_FAMILIES,
    MAGNITUDE_LEVELS,
    MECHANISM_DEFAULT_KEYS,
    MINUTES_PER_DAY,
    SEVERITY_LEVELS,
    SHIFTS,
    MinuteDistribution,
    WorldTemplateError,
    available_worlds,
    build_world,
    load_world,
    load_world_template,
    world_sha256,
    world_template_path,
)

BASELINE = "baseline_fab_v1"


def with_step(raw: dict[str, Any], name: str, **overrides: Any
              ) -> dict[str, Any]:
    """Apply overrides to one named step of a template, in place."""
    step = next(s for s in raw["process_steps"] if s["name"] == name)
    for key, value in overrides.items():
        if value is None:
            step.pop(key, None)
        else:
            step[key] = value
    return raw


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


def dedication(**overrides: Any) -> dict[str, Any]:
    """A standing routing preference in the world template's own shape."""
    raw = {"product": "Mobile-28", "tool": "ETCH-01", "operation_type": "ETCH",
           "start_day": 10.0, "end_day": 20.0, "share": 0.7}
    raw.update(overrides)
    return raw


def routing(**overrides: Any) -> dict[str, Any]:
    return {"stickiness": 0.6, "dedications": [dedication(**overrides)]}


def test_rejects_a_dedication_to_an_unqualified_tool(make_template):
    with pytest.raises(WorldTemplateError, match="not qualified"):
        build_world(make_template(routing=routing(tool="CVD-01")))


def test_rejects_a_dedication_for_an_unknown_product(make_template):
    with pytest.raises(WorldTemplateError, match="unknown product"):
        build_world(make_template(routing=routing(product="Nope-99")))


def test_rejects_a_dedication_window_that_ends_before_it_starts(make_template):
    with pytest.raises(WorldTemplateError, match="must be > 30"):
        build_world(make_template(routing=routing(start_day=30.0,
                                                  end_day=30.0)))


@pytest.mark.parametrize("share", [0.0, 1.0, 1.5, -0.2])
def test_rejects_a_dedication_share_outside_the_open_unit_interval(
        make_template, share):
    """ADR-015: 1 is a hard filter and 0 is not a dedication at all."""
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(make_template(routing=routing(share=share)))
    assert excinfo.value.path == "routing.dedications[0].share"


def test_rejects_a_dedication_without_a_share(make_template):
    raw = dedication()
    del raw["share"]
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(make_template(routing={"stickiness": 0.6,
                                           "dedications": [raw]}))
    assert excinfo.value.path == "routing.dedications[0].share"


def test_rejects_a_chamber_scoped_dedication(make_template):
    """The rule that keeps scenario G's confounder from being a pointer."""
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(make_template(routing=routing(chamber="B")))
    assert excinfo.value.path == "routing.dedications[0].chamber"
    assert "tool-level" in str(excinfo.value)


def test_the_baseline_declares_no_standing_dedication(world):
    assert world.routing.dedications == ()


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


# ------------------------------------------------- the measures relation (F1)


def test_the_baseline_declares_what_each_metrology_step_measures(world):
    """Step 3's blocker: what a metrology row indicts must not be a guess."""
    for metrology, measured in (("GATE_CD_METROLOGY", "GATE_ETCH"),
                                ("METAL_CD_METROLOGY", "METAL_ETCH")):
        step = world.step_by_name(metrology)
        assert world.measured_step(step.step_id).step_name == measured


def test_the_measures_relation_is_navigable_from_both_ends(world):
    etch = world.step_by_name("GATE_ETCH")
    readouts = world.metrology_steps_for(etch.step_id)
    assert [s.step_name for s in readouts] == ["GATE_CD_METROLOGY"]
    assert world.metrology_steps_for(
        world.step_by_name("METAL_LITHO").step_id) == ()


def test_process_steps_measure_nothing(world):
    for step in world.process_steps:
        if step.operation_type != "METROLOGY":
            assert world.measured_step(step.step_id) is None


def test_rejects_a_metrology_step_that_measures_nothing(make_template):
    raw = with_step(make_template(), "GATE_CD_METROLOGY", measures=None)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "process_steps[3].measures"


def test_rejects_a_dangling_measures_reference(make_template):
    raw = with_step(make_template(), "GATE_CD_METROLOGY",
                    measures="NOT_A_STEP")
    with pytest.raises(WorldTemplateError, match="unknown process step"):
        build_world(raw)


@pytest.mark.parametrize("target, message", [
    ("POST_GATE_INSPECT", "observation step"),
    ("METAL_CD_METROLOGY", "observation step"),
    ("GATE_CD_METROLOGY", "cannot measure itself"),
    ("SD_IMPLANT", "declares no metric"),
])
def test_rejects_a_measures_reference_of_the_wrong_kind(make_template, target,
                                                        message):
    raw = with_step(make_template(), "GATE_CD_METROLOGY", measures=target)
    with pytest.raises(WorldTemplateError, match=message):
        build_world(raw)


def test_rejects_measures_on_a_step_that_is_not_metrology(make_template):
    raw = with_step(make_template(), "GATE_ETCH", measures="GATE_LITHO")
    with pytest.raises(WorldTemplateError, match="valid only on METROLOGY"):
        build_world(raw)


def test_rejects_measuring_a_step_that_comes_later(make_template):
    raw = with_step(make_template(), "GATE_CD_METROLOGY",
                    measures="METAL_ETCH")
    with pytest.raises(WorldTemplateError, match="comes later"):
        build_world(raw)


def test_the_relation_is_not_inferred_from_step_order(make_template):
    """Declaration order and route position are both irrelevant: the template
    says what is measured, and moving the declaration changes nothing."""
    raw = make_template()
    steps = raw["process_steps"]
    metrology = steps.pop(next(i for i, s in enumerate(steps)
                               if s["name"] == "GATE_CD_METROLOGY"))
    steps.insert(0, metrology)
    reordered = build_world(raw)
    step = reordered.step_by_name("GATE_CD_METROLOGY")
    assert reordered.measured_step(step.step_id).step_name == "GATE_ETCH"


# --------------------------------------------------- the covers relation (F1)


def test_the_baseline_declares_what_each_inspection_covers(world):
    for inspection, covered, layer in (
        ("POST_GATE_INSPECT",
         ["OXIDE_GROWTH", "GATE_LITHO", "GATE_ETCH"], "GATE"),
        ("POST_METAL_INSPECT",
         ["ILD_DEPOSITION", "ILD_CMP", "METAL_DEPOSITION", "METAL_LITHO",
          "METAL_ETCH"], "METAL"),
    ):
        step = world.step_by_name(inspection)
        assert [s.step_name for s in world.covered_steps(step.step_id)] \
            == covered
        assert step.layer == layer


def test_coverage_is_navigable_from_both_ends(world):
    etch = world.step_by_name("METAL_ETCH")
    assert [s.step_name for s in world.inspection_steps_for(etch.step_id)] \
        == ["POST_METAL_INSPECT"]
    # The implant is covered by nothing: coverage is declared, so a step that
    # no inspection was told about is honestly uncovered.
    assert world.inspection_steps_for(
        world.step_by_name("SD_IMPLANT").step_id) == ()


def test_non_inspection_steps_cover_nothing_and_carry_no_layer(world):
    for step in world.process_steps:
        if not step.is_inspection:
            assert step.covers_step_ids == ()
            assert step.layer is None


@pytest.mark.parametrize("missing", ["covers", "layer"])
def test_rejects_an_inspection_that_declares_no_coverage(make_template,
                                                         missing):
    raw = with_step(make_template(), "POST_GATE_INSPECT", **{missing: None})
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == f"process_steps[4].{missing}"


def test_rejects_an_empty_coverage_list(make_template):
    raw = with_step(make_template(), "POST_GATE_INSPECT", covers=[])
    with pytest.raises(WorldTemplateError, match="at least 1 item"):
        build_world(raw)


def test_rejects_a_dangling_covers_reference(make_template):
    raw = with_step(make_template(), "POST_GATE_INSPECT",
                    covers=["GATE_ETCH", "NOT_A_STEP"])
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "process_steps[4].covers[1]"
    assert "unknown process step" in str(excinfo.value)


@pytest.mark.parametrize("covers, message", [
    (["GATE_CD_METROLOGY"], "observation step"),
    (["POST_METAL_INSPECT"], "observation step"),
    (["GATE_ETCH", "GATE_ETCH"], "duplicate covered step"),
    (["METAL_ETCH"], "comes later"),
])
def test_rejects_a_covers_reference_of_the_wrong_kind(make_template, covers,
                                                      message):
    raw = with_step(make_template(), "POST_GATE_INSPECT", covers=covers)
    with pytest.raises(WorldTemplateError, match=message):
        build_world(raw)


def test_rejects_an_unknown_layer(make_template):
    raw = with_step(make_template(), "POST_GATE_INSPECT", layer="BACKSIDE")
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "process_steps[4].layer"


def test_rejects_covers_on_a_step_that_is_not_an_inspection(make_template):
    raw = with_step(make_template(), "GATE_ETCH", covers=["GATE_LITHO"])
    with pytest.raises(WorldTemplateError,
                       match="valid only on inspection steps"):
        build_world(raw)


def test_rejects_an_inspection_that_is_not_an_inspection_operation(
        make_template):
    raw = with_step(make_template(), "GATE_ETCH", is_inspection=True,
                    covers=["GATE_LITHO"], layer="GATE")
    with pytest.raises(WorldTemplateError, match="must be an INSPECTION"):
        build_world(raw)


def test_rejects_a_layer_vocabulary_that_is_empty(make_template):
    with pytest.raises(WorldTemplateError, match="at least 1 item"):
        build_world(make_template(layers=[]))


# ------------------------------------------------------------ alarm contract


def test_the_baseline_declares_generic_alarm_rules(world):
    policy = world.alarms
    assert policy.codes
    assert policy.severities == ("INFO", "WARNING", "CRITICAL")
    assert 0.0 < policy.background_rate_per_chamber_day < 1.0
    assert 0.0 < policy.detection_probability <= 1.0
    for code in policy.codes:
        assert code.threshold_sigma > 0.0
        assert code.severity in policy.severities
        assert code.operation_types
        assert policy.code_by_name(code.code) is code


def test_every_alarm_rule_watches_a_declared_signal(world):
    channels = {c.name for c in world.observation.channels}
    for code in world.alarms.codes:
        known = channels if code.source == "channel" else set(
            world.observation.latents)
        assert code.signal in known


def test_every_alarm_code_can_fire_on_more_than_one_chamber(world):
    """Rule D2: a code whose only possible source is one entity would be a
    fingerprint of that entity, whatever the background rate did."""
    for code in world.alarms.codes:
        chambers = {chamber.chamber_id
                    for operation in code.operation_types
                    for tool in world.tools_for_operation(operation)
                    for chamber in world.chambers_of(tool.tool_id)}
        assert len(chambers) >= 2, code.code


def test_alarm_codes_are_shared_across_operation_types(world):
    """No code belongs to one kind of tool alone: the codes that matter for
    the Phase 1 mechanisms are raisable wherever the mechanism could act."""
    reach = {code.code: set(code.operation_types) for code in world.alarms.codes}
    assert {"ETCH", "CVD", "PVD"} <= reach["PRESSURE_HI"]
    assert {"ETCH", "CVD", "PVD", "CMP"} <= reach["PARTICLE_HI"]


def test_no_alarm_rule_can_name_an_entity_or_an_event(world):
    """The contract has no field for one — this pins that it stays that way."""
    fields = set(vars(world.alarms.codes[0]))
    assert fields == {"code", "source", "signal", "operation_types",
                      "threshold_sigma", "severity", "message"}


@pytest.mark.parametrize("overrides, path", [
    ({"threshold_sigma": 0.0}, "alarms.codes[0].threshold_sigma"),
    ({"threshold_sigma": -1.0}, "alarms.codes[0].threshold_sigma"),
    ({"threshold_sigma": "3"}, "alarms.codes[0].threshold_sigma"),
    ({"signal": "not_a_channel"}, "alarms.codes[0].signal"),
    ({"source": "vibes"}, "alarms.codes[0].source"),
    ({"severity": "APOCALYPTIC"}, "alarms.codes[0].severity"),
    ({"operation_types": ["ETCH", "ETCH"]},
     "alarms.codes[0].operation_types[1]"),
    ({"operation_types": ["PLASMA_MAGIC"]},
     "alarms.codes[0].operation_types[0]"),
    ({"code": "pressure_hi"}, "alarms.codes[0].code"),
    ({"escalation": "page"}, "alarms.codes[0]"),
])
def test_rejects_an_invalid_alarm_rule(make_template, overrides, path):
    raw = make_template()
    raw["alarms"]["codes"][0].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_a_latent_alarm_watching_something_that_is_not_a_latent(
        make_template):
    raw = make_template()
    raw["alarms"]["codes"][0].update({"source": "latent",
                                      "signal": "chamber_pressure_mtorr"})
    with pytest.raises(WorldTemplateError, match="unknown latent"):
        build_world(raw)


@pytest.mark.parametrize("overrides, path", [
    ({"background_rate_per_chamber_day": 0.0},
     "alarms.background_rate_per_chamber_day"),
    ({"detection_probability": 0.0}, "alarms.detection_probability"),
    ({"detection_probability": 1.5}, "alarms.detection_probability"),
    ({"codes": []}, "alarms.codes"),
    ({"severities": []}, "alarms.severities"),
])
def test_rejects_an_invalid_alarm_policy(make_template, overrides, path):
    raw = make_template()
    raw["alarms"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_duplicate_alarm_codes(make_template):
    raw = make_template()
    raw["alarms"]["codes"][1]["code"] = raw["alarms"]["codes"][0]["code"]
    with pytest.raises(WorldTemplateError, match="duplicate alarm code"):
        build_world(raw)


# --------------------------------------------------------- die-grid contract


def test_the_baseline_declares_a_die_geometry(world):
    grid = world.die_grid
    assert grid.edge_exclusion_mm > 0.0
    assert grid.street_width_mm >= 0.0
    assert grid.die_aspect_ratio > 0.0
    assert grid.origin == "wafer_center"
    assert grid.index_order == "row_major"
    assert grid.partial_die_policy == "exclude"


def test_the_die_geometry_needs_only_the_product_to_be_resolved(world):
    """The later kill model must reach a die's coordinates from geometry, and
    geometry is wafer size, die area and this policy — nothing else."""
    for product in world.products:
        usable = product.wafer_size_mm - 2.0 * world.die_grid.edge_exclusion_mm
        side = (product.die_size_mm2 * world.die_grid.die_aspect_ratio) ** 0.5
        assert 0.0 < side + world.die_grid.street_width_mm <= usable


@pytest.mark.parametrize("overrides, path", [
    ({"edge_exclusion_mm": -1.0}, "die_grid.edge_exclusion_mm"),
    ({"edge_exclusion_mm": 200.0}, "die_grid.edge_exclusion_mm"),
    ({"street_width_mm": -0.1}, "die_grid.street_width_mm"),
    ({"die_aspect_ratio": 0.0}, "die_grid.die_aspect_ratio"),
    ({"die_aspect_ratio": "1"}, "die_grid.die_aspect_ratio"),
    ({"origin": "wafer_notch"}, "die_grid.origin"),
    ({"index_order": "spiral"}, "die_grid.index_order"),
    ({"partial_die_policy": "count_them"}, "die_grid.partial_die_policy"),
    ({"reticle_shot_mm": 26.0}, "die_grid"),
])
def test_rejects_an_invalid_die_geometry(make_template, overrides, path):
    raw = make_template()
    raw["die_grid"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_a_die_that_does_not_fit_its_wafer(make_template):
    raw = make_template()
    raw["die_grid"]["die_aspect_ratio"] = 900.0
    with pytest.raises(WorldTemplateError, match="does not fit"):
        build_world(raw)


def test_the_die_geometry_is_deterministic(template):
    assert build_world(template).die_grid == build_world(template).die_grid


# ------------------------------------------------ observation configuration


def test_the_baseline_declares_the_observation_stack(world):
    observation = world.observation
    assert observation.latents == ("edge_uniformity", "param_bias",
                                   "particle_load")
    assert "edge" in observation.wafer_zones
    assert observation.channels
    assert observation.variation.run_noise > 0.0
    assert 0.0 <= observation.variation.lot_ar1_phi < 1.0


def test_every_channel_is_grounded_in_something_the_world_declares(world):
    for channel in world.observation.channels:
        assert channel.kind in CHANNEL_KINDS
        assert channel.scale > 0.0
        steps = [s for s in world.process_steps
                 if s.operation_type in channel.operation_types]
        if channel.kind == "fdc":
            assert any(channel.name in dict(s.settings) for s in steps)
        else:
            assert any(s.metric is not None and s.metric.name == channel.name
                       for s in steps)


def test_sensitivities_are_keyed_by_latent_only(world):
    for channel in world.observation.channels:
        for latent, coefficient in channel.sensitivities:
            assert latent in world.observation.latents
            assert isinstance(coefficient, float)


def test_channels_are_reachable_by_name_and_by_operation(world):
    assert world.channel("cd_nm").kind == "metrology"
    etch = {c.name for c in world.channels_for_operation("ETCH")}
    assert "cd_nm" in etch and "gas_flow_sccm" in etch
    assert "down_force_psi" not in etch


def test_severity_calibration_is_the_difficulty_axis(world):
    calibration = dict(world.observation.severity_calibration)
    assert tuple(calibration) == SEVERITY_LEVELS
    assert (calibration["subtle"] < calibration["moderate"]
            < calibration["obvious"])
    for level in SEVERITY_LEVELS:
        assert world.observation.sigma_for(level) == calibration[level]


def test_severity_vocabularies_agree():
    """Two contracts, one vocabulary; this keeps the copies from drifting."""
    from fabsim.scenario import SEVERITIES

    assert SEVERITY_LEVELS == SEVERITIES


def test_the_confusion_matrix_is_a_distribution_over_declared_classes(world):
    classifier = world.observation.classifier
    assert set(dict(classifier.confusion)) == set(classifier.origins)
    for origin, row in classifier.confusion:
        assert classifier.row(origin) == row
        assert abs(sum(p for _label, p in row) - 1.0) < 1e-9
        assert all(label in classifier.classes for label, _p in row)
        # Overlap, not a partition: no origin maps to one class with
        # certainty, or spatial "confirmation" would be circular again (D3).
        assert max(p for _label, p in row) < 1.0


@pytest.mark.parametrize("overrides, path", [
    ({"latents": []}, "observation.latents"),
    ({"wafer_zones": ["edge"]}, "observation.wafer_zones"),
    ({"channels": []}, "observation.channels"),
    ({"detector_gain": 1.0}, "observation"),
])
def test_rejects_an_invalid_observation_block(make_template, overrides, path):
    raw = make_template()
    raw["observation"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


@pytest.mark.parametrize("overrides, path", [
    ({"name": "not_a_setpoint"}, "observation.channels[0].name"),
    ({"kind": "telemetry"}, "observation.channels[0].kind"),
    ({"scale": 0.0}, "observation.channels[0].scale"),
    ({"scale": -1.0}, "observation.channels[0].scale"),
    ({"operation_types": []}, "observation.channels[0].operation_types"),
    ({"operation_types": ["PLASMA_MAGIC"]},
     "observation.channels[0].operation_types[0]"),
    ({"sensitivities": {"gremlins": 1.0}},
     "observation.channels[0].sensitivities.gremlins"),
    ({"drift_rate": 1.0}, "observation.channels[0]"),
])
def test_rejects_an_invalid_channel(make_template, overrides, path):
    raw = make_template()
    raw["observation"]["channels"][0].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_a_channel_keyed_to_an_entity(make_template):
    """Rule D6, made unrepresentable: sensitivities take latent names, and a
    tool or chamber name is not one."""
    raw = make_template()
    raw["observation"]["channels"][0]["sensitivities"] = {"ETCH-02": 1.0}
    with pytest.raises(WorldTemplateError, match="not a declared latent"):
        build_world(raw)


def test_rejects_a_metrology_channel_with_no_metric_behind_it(make_template):
    raw = make_template()
    raw["observation"]["channels"].append({
        "name": "sheet_resistance_ohm", "kind": "metrology",
        "operation_types": ["ETCH"], "scale": 1.0, "sensitivities": {}})
    with pytest.raises(WorldTemplateError, match="a step metric"):
        build_world(raw)


@pytest.mark.parametrize("overrides, path", [
    ({"run_noise": 0.0}, "observation.variation_stack.run_noise"),
    ({"lot_ar1_phi": 1.0}, "observation.variation_stack.lot_ar1_phi"),
    ({"lot_ar1_phi": -0.1}, "observation.variation_stack.lot_ar1_phi"),
    ({"chamber_offset": -1.0}, "observation.variation_stack.chamber_offset"),
    ({"wafer_noise": 1.0}, "observation.variation_stack"),
])
def test_rejects_an_invalid_variation_stack(make_template, overrides, path):
    raw = make_template()
    raw["observation"]["variation_stack"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_a_severity_calibration_that_is_not_an_axis(make_template):
    raw = make_template()
    raw["observation"]["severity_calibration"] = {"subtle": 6.0,
                                                  "moderate": 3.0,
                                                  "obvious": 1.5}
    with pytest.raises(WorldTemplateError, match="increase strictly"):
        build_world(raw)


@pytest.mark.parametrize("severities, path", [
    ({"subtle": 0.0}, "observation.severity_calibration.subtle"),
    ({"catastrophic": 9.0}, "observation.severity_calibration"),
])
def test_rejects_an_invalid_severity_calibration(make_template, severities,
                                                 path):
    raw = make_template()
    raw["observation"]["severity_calibration"].update(severities)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_a_confusion_row_that_is_not_a_distribution(make_template):
    raw = make_template()
    raw["observation"]["classifier"]["confusion"]["uniform"]["PARTICLE"] = 0.9
    with pytest.raises(WorldTemplateError, match="must sum to 1"):
        build_world(raw)


def test_rejects_a_confusion_row_for_an_undeclared_origin(make_template):
    raw = make_template()
    raw["observation"]["classifier"]["confusion"]["chamber_b"] = {
        "PARTICLE": 1.0}
    with pytest.raises(WorldTemplateError,
                       match="not a declared defect origin"):
        build_world(raw)


def test_rejects_a_missing_confusion_row(make_template):
    raw = make_template()
    del raw["observation"]["classifier"]["confusion"]["scratch"]
    with pytest.raises(WorldTemplateError, match="no confusion row"):
        build_world(raw)


def test_rejects_a_confusion_row_naming_an_undeclared_class(make_template):
    raw = make_template()
    raw["observation"]["classifier"]["confusion"]["scratch"] = {"KILLER": 1.0}
    with pytest.raises(WorldTemplateError, match="not a declared class"):
        build_world(raw)


# ------------------------------------------------------- latent dynamics (3A)


def test_the_baseline_declares_dynamics_for_every_latent(world):
    assert {d.name for d in world.latent_dynamics} == set(
        world.observation.latents)
    for dynamics in world.latent_dynamics:
        assert dynamics.family in LATENT_FAMILIES
        assert dynamics.severity_reference > 0.0
        assert dynamics.benign_tool_sd > 0.0
        assert dynamics.benign_chamber_sd > 0.0


def test_the_baseline_dynamics_match_the_design_table(world):
    """`CAUSAL_MECHANISM_MODEL.md` §1 and §6, as constants."""
    edge = world.latent("edge_uniformity")
    assert edge.family == "ar1"
    assert not edge.pm_resets()          # hardware, not cleaning

    param = world.latent("param_bias")
    assert param.family == "ar1"
    assert param.phi == pytest.approx(0.98)
    assert (param.pm_recovery_mean, param.pm_recovery_sd) == (0.7, 0.1)

    particle = world.latent("particle_load")
    assert particle.family == "accumulation"
    assert particle.growth_per_day > 0.0
    assert particle.pm_recovery_mean == 1.0


def test_rejects_dynamics_that_do_not_cover_the_latent_vocabulary(
        make_template):
    raw = make_template()
    del raw["latents"]["param_bias"]
    with pytest.raises(WorldTemplateError, match="no dynamics for declared"):
        build_world(raw)


def test_rejects_dynamics_for_a_latent_nothing_can_observe(make_template):
    raw = make_template()
    raw["latents"]["thermal_bow"] = dict(raw["latents"]["param_bias"])
    with pytest.raises(WorldTemplateError, match="undeclared latent"):
        build_world(raw)


@pytest.mark.parametrize("overrides, path", [
    ({"phi": 1.0}, "latents.param_bias.phi"),
    ({"phi": -0.1}, "latents.param_bias.phi"),
    ({"phi": "0.98"}, "latents.param_bias.phi"),
    ({"sigma": 0.0}, "latents.param_bias.sigma"),
    ({"severity_reference": 0.0}, "latents.param_bias.severity_reference"),
    ({"benign_tool_sd": 0.0}, "latents.param_bias.benign_tool_sd"),
    ({"family": "brownian"}, "latents.param_bias.family"),
    ({"growth_per_day": 1.0}, "latents.param_bias"),
    ({"half_life_days": 3.0}, "latents.param_bias"),
])
def test_rejects_invalid_wander_dynamics(make_template, overrides, path):
    raw = make_template()
    raw["latents"]["param_bias"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


@pytest.mark.parametrize("overrides, path", [
    ({"growth_per_day": 0.0}, "latents.particle_load.growth_per_day"),
    ({"sigma_per_day": -1.0}, "latents.particle_load.sigma_per_day"),
    ({"phi": 0.9}, "latents.particle_load"),
])
def test_rejects_invalid_accumulation_dynamics(make_template, overrides, path):
    raw = make_template()
    raw["latents"]["particle_load"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


@pytest.mark.parametrize("recovery, path", [
    ({"mean": 1.5}, "latents.param_bias.pm_recovery.mean"),
    ({"mean": -0.1}, "latents.param_bias.pm_recovery.mean"),
    ({"sd": -0.1}, "latents.param_bias.pm_recovery.sd"),
    ({"beta": 2.0}, "latents.param_bias.pm_recovery"),
])
def test_rejects_an_invalid_pm_recovery(make_template, recovery, path):
    raw = make_template()
    raw["latents"]["param_bias"]["pm_recovery"].update(recovery)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


# --------------------------------------------------- mechanism constants (3A)


def test_the_baseline_answers_for_every_mechanism(world):
    policy = world.mechanism_policy
    assert policy.severity_jitter_sd > 0.0
    assert 0.0 < policy.intermittent_duty < 1.0
    assert policy.intermittent_period_days > 0.0
    for name in MECHANISM_DEFAULT_KEYS:
        assert isinstance(policy.defaults_for(name), dict)
    assert set(policy.defaults_for("particle_excursion")) == {
        "step_fraction", "escalation_days"}
    assert set(policy.defaults_for("benign_offset")["magnitudes"]) == set(
        MAGNITUDE_LEVELS)


def test_mechanism_defaults_are_handed_out_as_copies(world):
    first = world.mechanism_policy.defaults_for("particle_excursion")
    first["step_fraction"] = 99.0
    assert (world.mechanism_policy.defaults_for("particle_excursion")
            ["step_fraction"] != 99.0)


def test_magnitude_vocabularies_agree():
    from fabsim.scenario import MAGNITUDES

    assert MAGNITUDE_LEVELS == MAGNITUDES


def test_rejects_a_world_that_ignores_a_registered_mechanism(make_template):
    raw = make_template()
    del raw["mechanisms"]["param_drift"]
    with pytest.raises(WorldTemplateError, match="no constants for mechanism"):
        build_world(raw)


def test_rejects_constants_for_a_mechanism_that_does_not_exist(make_template):
    raw = make_template()
    raw["mechanisms"]["chamber_meltdown"] = {}
    with pytest.raises(WorldTemplateError, match="unknown field"):
        build_world(raw)


@pytest.mark.parametrize("overrides, path", [
    ({"step_fraction": 1.0}, "mechanisms.particle_excursion.step_fraction"),
    ({"step_fraction": -0.1}, "mechanisms.particle_excursion.step_fraction"),
    ({"escalation_days": 0.0},
     "mechanisms.particle_excursion.escalation_days"),
    ({"decay_days": 2.0}, "mechanisms.particle_excursion"),
])
def test_rejects_invalid_particle_constants(make_template, overrides, path):
    raw = make_template()
    raw["mechanisms"]["particle_excursion"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_distractor_magnitudes_that_are_not_an_axis(make_template):
    raw = make_template()
    raw["mechanisms"]["benign_offset"]["magnitudes"] = {
        "small": 1.4, "moderate": 0.9, "large": 0.4}
    with pytest.raises(WorldTemplateError, match="increase strictly"):
        build_world(raw)


@pytest.mark.parametrize("overrides, path", [
    ({"severity_jitter_sd": -0.1}, "mechanisms.severity_jitter_sd"),
    ({"profiles": {"intermittent_period_days": 3.0,
                   "intermittent_duty": 1.0}},
     "mechanisms.profiles.intermittent_duty"),
    ({"profiles": {"intermittent_period_days": 0.0,
                   "intermittent_duty": 0.45}},
     "mechanisms.profiles.intermittent_period_days"),
    ({"profiles": {"intermittent_duty": 0.45}},
     "mechanisms.profiles.intermittent_period_days"),
])
def test_rejects_invalid_mechanism_globals(make_template, overrides, path):
    raw = make_template()
    raw["mechanisms"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


# --------------------------------------------------- response contract (3B)


def test_the_baseline_declares_one_fab_wide_response_policy(world):
    """ADR-017: no per-scenario, per-mechanism or per-chamber variant."""
    policy = world.response
    assert 0.0 < policy.baseline_alpha < 1.0
    assert policy.baseline_warmup_days > 0.0
    assert policy.escalation_count > 1
    assert policy.escalation_window_days > 0.0
    assert policy.repair_delay_days_mean > 0.0
    assert policy.repair_duration.maximum >= policy.repair_duration.minimum


def test_recovery_follows_the_designs_distribution(world):
    """`CAUSAL_MECHANISM_MODEL.md` §6: Beta(8, 2), 10% no-fix."""
    recovery = world.response.recovery
    assert (recovery.quality_alpha, recovery.quality_beta) == (8.0, 2.0)
    assert recovery.no_fix_probability == pytest.approx(0.1)


def test_every_latent_declares_how_much_of_a_repair_reaches_it(world):
    for dynamics in world.latent_dynamics:
        assert 0.0 <= dynamics.repair_efficacy <= 1.0
    # A repair reaches the hardware a PM cannot touch — which is the whole
    # reason a breakdown and a fault repair can share one machine.
    assert world.latent("edge_uniformity").repair_efficacy > 0.0
    assert not world.latent("edge_uniformity").pm_resets()


@pytest.mark.parametrize("overrides, path", [
    ({"baseline_alpha": 0.0}, "response.baseline_alpha"),
    ({"baseline_alpha": 1.0}, "response.baseline_alpha"),
    ({"baseline_warmup_days": 0.0}, "response.baseline_warmup_days"),
    ({"escalation_count": 0}, "response.escalation_count"),
    ({"escalation_count": 2.5}, "response.escalation_count"),
    ({"escalation_window_days": 0.0}, "response.escalation_window_days"),
    ({"repair_delay_days_mean": 0.0}, "response.repair_delay_days_mean"),
    ({"repair_cooldown_days": -1.0}, "response.repair_cooldown_days"),
    ({"escalation_policy": "aggressive"}, "response"),
])
def test_rejects_an_invalid_response_policy(make_template, overrides, path):
    raw = make_template()
    raw["response"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


@pytest.mark.parametrize("overrides, path", [
    ({"quality_alpha": 0.0}, "response.recovery.quality_alpha"),
    ({"quality_beta": -1.0}, "response.recovery.quality_beta"),
    ({"no_fix_probability": 1.0}, "response.recovery.no_fix_probability"),
    ({"no_fix_probability": -0.1}, "response.recovery.no_fix_probability"),
    ({"always_works": True}, "response.recovery"),
])
def test_rejects_an_invalid_recovery_policy(make_template, overrides, path):
    raw = make_template()
    raw["response"]["recovery"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_every_latent_declares_how_radial_its_effect_is(world):
    """The within-wafer shape of a latent, declared rather than inferred.

    It is what lets the observation model give a uniformity fault an edge
    signature and a delivery bias a uniform one, without the model ever
    learning which latent is which (`CAUSAL_MECHANISM_MODEL.md` §2).
    """
    for dynamics in world.latent_dynamics:
        assert 0.0 <= dynamics.radial_weight <= 1.0
    assert world.latent("edge_uniformity").radial_weight == 1.0
    assert world.latent("param_bias").radial_weight == 0.0


@pytest.mark.parametrize("weight", [-0.1, 1.5, "1.0", None])
def test_rejects_an_invalid_radial_weight(make_template, weight):
    raw = make_template()
    raw["latents"]["edge_uniformity"]["radial_weight"] = weight
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "latents.edge_uniformity.radial_weight"


@pytest.mark.parametrize("efficacy", [-0.1, 1.5, "0.9"])
def test_rejects_an_invalid_repair_efficacy(make_template, efficacy):
    raw = make_template()
    raw["latents"]["param_bias"]["repair_efficacy"] = efficacy
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "latents.param_bias.repair_efficacy"


def test_the_response_policy_has_no_per_entity_knob(world):
    """The absence *is* the design: an engine with nothing to treat a faulted
    chamber differently with cannot treat it differently."""
    from fabsim.world import ResponsePolicy

    fields = set(ResponsePolicy.__dataclass_fields__)
    forbidden = {"tool", "tool_name", "chamber", "chamber_name", "mechanism",
                 "severity", "scenario", "event", "target"}
    assert not (fields & forbidden)


# ---------------------------------------------------- defect contract (3D)


def test_the_baseline_declares_an_intensity_for_every_origin(world):
    """The mixture's components are exactly the classifier's origins.

    An origin the fab can produce but the classifier cannot label would emit a
    defect with nothing to call it; one the classifier knows but nothing can
    produce would be a class with no support — which is how a vocabulary value
    becomes a fingerprint (rule D2).
    """
    classifier = world.observation.classifier
    assert {o.origin for o in world.defects.origins} == set(classifier.origins)
    for policy in world.defects.origins:
        assert policy.base_rate > 0.0          # a healthy fab makes all of it
        for latent, sensitivity in policy.sensitivities:
            assert latent in world.observation.latents
            assert sensitivity >= 0.0


def test_defect_geometry_fits_inside_the_wafer(world):
    policy = world.defects
    assert policy.edge_inner_fraction + policy.edge_width_fraction <= 1.0
    assert 0.0 < policy.center_sigma_fraction < 1.0
    assert policy.cluster_radius_mm > 0.0
    assert policy.cluster_mean_defects >= 1.0
    assert policy.size_median_um > 0.0


def test_baseline_defectivity_is_product_dependent(world):
    """`CAUSAL_MECHANISM_MODEL.md` §4.1's standing distractor."""
    scales = {p.product_name: p.defect_scale for p in world.products}
    assert len(set(scales.values())) > 1
    assert all(value > 0.0 for value in scales.values())


def test_rejects_an_origin_the_classifier_does_not_know(make_template):
    raw = make_template()
    raw["defects"]["origins"]["burn_mark"] = {"base_rate": 0.001,
                                              "sensitivities": {}}
    with pytest.raises(WorldTemplateError, match="unknown field"):
        build_world(raw)


def test_rejects_an_origin_with_no_intensity(make_template):
    raw = make_template()
    del raw["defects"]["origins"]["scratch"]
    with pytest.raises(WorldTemplateError, match="no intensity"):
        build_world(raw)


def test_rejects_a_defect_sensitivity_keyed_to_an_entity(make_template):
    raw = make_template()
    raw["defects"]["origins"]["edge_ring"]["sensitivities"] = {"ETCH-02": 1.0}
    with pytest.raises(WorldTemplateError, match="not a declared latent"):
        build_world(raw)


@pytest.mark.parametrize("overrides, path", [
    ({"base_rate": 0.0}, "defects.origins.edge_ring.base_rate"),
    ({"base_rate": -1.0}, "defects.origins.edge_ring.base_rate"),
    ({"decay": 0.5}, "defects.origins.edge_ring"),
])
def test_rejects_an_invalid_origin_intensity(make_template, overrides, path):
    raw = make_template()
    raw["defects"]["origins"]["edge_ring"].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


@pytest.mark.parametrize("block, overrides, path", [
    ("size", {"median_um": 0.0}, "defects.size.median_um"),
    ("size", {"log_sigma": 0.0}, "defects.size.log_sigma"),
    ("edge_ring", {"inner_fraction": 1.0}, "defects.edge_ring.inner_fraction"),
    ("edge_ring", {"width_fraction": 0.0},
     "defects.edge_ring.width_fraction"),
    ("edge_ring", {"jitter_fraction": -0.1},
     "defects.edge_ring.jitter_fraction"),
    ("center", {"sigma_fraction": 1.0}, "defects.center.sigma_fraction"),
    ("cluster", {"radius_mm": 0.0}, "defects.cluster.radius_mm"),
    ("cluster", {"mean_defects": 0.5}, "defects.cluster.mean_defects"),
    ("scratch", {"jitter_mm": -1.0}, "defects.scratch.jitter_mm"),
    ("size", {"tail": 2.0}, "defects.size"),
])
def test_rejects_invalid_defect_geometry(make_template, block, overrides,
                                         path):
    raw = make_template()
    raw["defects"][block].update(overrides)
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == path


def test_rejects_a_ring_that_reaches_past_the_wafer_edge(make_template):
    raw = make_template()
    raw["defects"]["edge_ring"].update({"inner_fraction": 0.9,
                                        "width_fraction": 0.3})
    with pytest.raises(WorldTemplateError, match="cannot land outside"):
        build_world(raw)


@pytest.mark.parametrize("scale", [0.0, -1.0, "1.0"])
def test_rejects_an_invalid_product_defect_scale(make_template, scale):
    raw = make_template()
    raw["products"][0]["defect_scale"] = scale
    with pytest.raises(WorldTemplateError) as excinfo:
        build_world(raw)
    assert excinfo.value.path == "products[0].defect_scale"


# --------------------------------------------------------- world identity


def test_the_same_world_hashes_the_same_way(template):
    assert world_sha256(template) == world_sha256(template)
    assert build_world(template).world_sha256 == world_sha256(template)
    assert re.fullmatch(r"[0-9a-f]{64}", build_world(template).world_sha256)


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda raw: raw.update(wafers_per_lot=24), id="lot-size"),
    pytest.param(lambda raw: raw["routing"].update(stickiness=0.61),
                 id="stickiness"),
    pytest.param(lambda raw: raw["products"][0].update(mix_weight=9),
                 id="product-mix"),
    pytest.param(lambda raw: raw["die_grid"].update(edge_exclusion_mm=2.0),
                 id="die-geometry"),
    pytest.param(lambda raw: raw["alarms"]["codes"][0].update(
        threshold_sigma=2.0), id="alarm-threshold"),
    pytest.param(lambda raw: raw["observation"]["channels"][0].update(
        scale=0.4), id="channel-scale"),
    pytest.param(lambda raw: raw["observation"]["variation_stack"].update(
        run_noise=1.1), id="variation-stack"),
    pytest.param(lambda raw: raw["latents"]["param_bias"].update(phi=0.97),
                 id="latent-dynamics"),
    pytest.param(lambda raw: raw["mechanisms"]["particle_excursion"].update(
        escalation_days=5.0), id="mechanism-constants"),
    pytest.param(lambda raw: raw["response"].update(escalation_count=2),
                 id="response-policy"),
    pytest.param(lambda raw: raw["response"]["recovery"].update(
        no_fix_probability=0.2), id="recovery-policy"),
    pytest.param(lambda raw: raw["latents"]["edge_uniformity"].update(
        radial_weight=0.5), id="radial-weight"),
    pytest.param(lambda raw: raw["defects"]["origins"]["edge_ring"].update(
        base_rate=0.0003), id="defect-intensity"),
    pytest.param(lambda raw: raw["products"][0].update(defect_scale=1.4),
                 id="product-defectivity"),
])
def test_a_semantic_change_changes_the_world_hash(template, mutate):
    """Anything that can move emitted data must move the identity with it."""
    import copy

    changed = copy.deepcopy(template)
    mutate(changed)
    assert world_sha256(changed) != world_sha256(template)


def test_prose_is_not_part_of_the_world_identity(template):
    """Documentation never reaches an observable, so editing it must not
    claim that a different dataset was produced."""
    import copy

    reworded = copy.deepcopy(template)
    reworded["description"] = "an entirely different description"
    assert world_sha256(reworded) == world_sha256(template)


def test_formatting_is_not_part_of_the_world_identity(tmp_path: Path,
                                                      template: dict[str, Any]):
    """Key order, indentation and a byte-order mark are editor artefacts."""
    import json

    def reorder(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: reorder(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reorder(v) for v in value]
        return value

    for name, payload in (("indented", json.dumps(template, indent=4)),
                          ("reordered", json.dumps(reorder(template))),
                          ("bom", "﻿" + json.dumps(template))):
        path = tmp_path / name
        path.mkdir()
        (path / f"{BASELINE}.json").write_text(payload, encoding="utf-8")
        assert load_world(BASELINE, path).world_sha256 == \
            load_world(BASELINE).world_sha256


_WORLD_IDENTITY_PROBE = """
import sys
from fabsim.world import load_world
print(load_world("baseline_fab_v1").world_sha256)
"""


def test_the_world_identity_ignores_process_and_environment(tmp_path: Path):
    """No wall clock, no cwd, no locale, no hash salt — the same bytes give
    the same identity everywhere (`SCENARIO_SPECIFICATION.md` §5)."""
    import os
    import subprocess
    import sys

    digests = []
    for name, hash_seed, extra in (("a", "0", {"LANG": "C", "TZ": "UTC"}),
                                   ("b", "999", {"LANG": "de_DE.UTF-8",
                                                 "TZ": "Asia/Tokyo",
                                                 "FABSIM_UNRELATED": "x"})):
        directory = tmp_path / name
        directory.mkdir()
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env.update(extra)
        digests.append(subprocess.run(
            [sys.executable, "-c", _WORLD_IDENTITY_PROBE], cwd=str(directory),
            env=env, capture_output=True, text=True, check=True).stdout)
    assert digests[0] == digests[1]
    assert digests[0].strip() == load_world(BASELINE).world_sha256


def test_a_world_digest_reaches_the_build_fingerprint():
    """The reproducibility contract's fifth input, wired end to end."""
    from fabsim.scenario import from_mapping

    config = from_mapping({"fabsim": "scenario/v1", "name": "null",
                           "world": BASELINE, "horizon_days": 84, "lots": 20,
                           "default_seed": 42})
    world = load_world(BASELINE)
    identity = config.dataset_identity(42, world_sha256=world.world_sha256)
    assert identity.world_sha256 == world.world_sha256
    assert identity.build_fingerprint != config.dataset_identity(
        42, world_sha256="0" * 64).build_fingerprint


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


def _fabsim_modules() -> list[Path]:
    """Every module of the package, subpackages included.

    `rglob`, not `glob`: `mechanisms/` is where entity-specific logic would be
    most tempting and least visible, so it is exactly what these two rules
    must reach.
    """
    source = Path(__file__).resolve().parents[2] / "src" / "fabsim"
    return sorted(p for p in source.rglob("*.py")
                  if "__pycache__" not in p.parts)


def test_no_code_constant_is_keyed_to_a_named_tool_or_chamber():
    """Rule D6: behaviour may never be keyed by a specific entity name.

    Prose may cite ETCH-02 as an example; *code* may not, because a tool name
    in a constant is how a generator quietly learns its own answer. Docstrings
    and comments are excluded; string literals in code are not.
    """
    pattern = re.compile(r"(ETCH|CVD|LITHO|CMP|PVD|FURN|IMP|MET|INSP|TEST)-\d")
    modules = _fabsim_modules()
    assert any(m.parent.name == "mechanisms" for m in modules)
    for module in modules:
        hits = [text for text in _code_strings(module) if pattern.search(text)]
        assert hits == [], module


def test_fabsim_never_imports_fabops():
    """ADR-013: fabsim writes datasets, fabops reads them; neither imports."""
    import ast

    for module in _fabsim_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [name for name in imported
                    if name == "fabops" or name.startswith("fabops.")], module


def test_fabops_never_imports_fabsim():
    """The other direction of ADR-013, and leakage test L9: the analytical
    plane may not reach the generator, its scenarios or its truth."""
    import ast

    repository = Path(__file__).resolve().parents[2]
    forbidden_text = ("scenarios/", "truth.json", "truth/")
    for root in ("src/fabops", "app"):
        for module in sorted((repository / root).rglob("*.py")):
            if "__pycache__" in module.parts:
                continue
            source = module.read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported += [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            assert not [n for n in imported
                        if n == "fabsim" or n.startswith("fabsim.")], module
            for token in forbidden_text:
                assert token not in source, (module, token)
