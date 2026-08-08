"""
Contract tests for `fabsim.scenario`.

Two things are under test: the loader rejects what the design says must be
rejected, and identity is a pure function of the configuration's meaning —
not of its formatting, its filename, or the machine it was loaded on.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

import pytest

from fabsim import SCHEMA_VERSION
from fabsim import __version__ as FABSIM_VERSION
from fabsim.scenario import (
    CONTRACT,
    DOCUMENTATION_KEYS,
    MAGNITUDES,
    PROFILE_TYPES,
    RECOVERY_MODES,
    ROUTING_CONDITION_KINDS,
    SEVERITIES,
    ScenarioConfigError,
    derive_dataset_id,
    from_mapping,
    load_scenario,
    load_scenario_text,
)

#: A stand-in for the digest of a resolved world. `fabsim.scenario` never opens
#: the world registry — it is handed the digest — so these tests hand it one
#: rather than importing `fabsim.world` and coupling two contracts that the
#: design keeps apart. `test_world.py` checks the real digest end to end.
WORLD_DIGEST = "5c" * 32
OTHER_WORLD_DIGEST = "7a" * 32

# The Phase 1 demo scenario of SCENARIO_SPECIFICATION.md §2, in JSON.
VALID: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "demo-edge-uniformity",
    "description": "One etch chamber develops edge non-uniformity mid-window.",
    "world": "baseline_fab_v1",
    "horizon_days": 84,
    "lots": 20,
    "default_seed": 42,
    "events": [
        {
            "mechanism": "chamber_edge_uniformity",
            "target": {"tool": "ETCH-02", "chamber": "B"},
            "onset_day": 35,
            "profile": {"type": "ramp", "ramp_days": 7},
            "severity": "moderate",
            "response": {
                "alarm": True,
                "repair_delay_days_mean": 4.0,
                "recovery": "partial",
            },
        }
    ],
    "distractors": [
        {
            "mechanism": "benign_offset",
            "target": {"tool": "CVD-01"},
            "magnitude": "small",
        }
    ],
}

MINIMAL: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "null",
    "world": "baseline_fab_v1",
    "horizon_days": 84,
    "lots": 20,
    "default_seed": 42,
}


def config(**overrides: Any) -> dict[str, Any]:
    """A copy of the demo configuration with top-level overrides applied."""
    raw = json.loads(json.dumps(VALID))
    raw.update(overrides)
    return raw


def event(**overrides: Any) -> dict[str, Any]:
    raw = json.loads(json.dumps(VALID["events"][0]))
    raw.update(overrides)
    return raw


#: Scenario G's confounder: one product preferentially — not exclusively —
#: routed to one tool during a window (`SCENARIO_SPECIFICATION.md` §4 G).
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
    raw = json.loads(json.dumps(CONDITION))
    raw.update(overrides)
    return raw


BOM = chr(0xFEFF)  # some editors add one; it is not a semantic difference


def reorder(value: Any) -> Any:
    """Reverse every object's key order, recursively."""
    if isinstance(value, dict):
        return {k: reorder(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reorder(v) for v in value]
    return value


# --------------------------------------------------------------- happy paths


def test_valid_configuration_loads():
    cfg = load_scenario_text(json.dumps(VALID))

    assert CONTRACT == "fabsim.scenario/v1"
    assert cfg.name == "demo-edge-uniformity"
    assert cfg.world == "baseline_fab_v1"
    assert cfg.horizon_days == 84
    assert cfg.lots == 20
    assert cfg.default_seed == 42
    assert len(cfg.events) == 1
    assert cfg.events[0]["target"] == {"tool": "ETCH-02", "chamber": "B"}
    assert cfg.events[0]["profile"] == {"type": "ramp", "ramp_days": 7.0}
    assert cfg.distractors[0]["mechanism"] == "benign_offset"


def test_optional_fields_are_defaulted():
    """The null scenario: no prose, no events, no distractors."""
    cfg = from_mapping(MINIMAL)
    assert cfg.description == ""
    assert cfg.events == []
    assert cfg.distractors == []


def test_omitted_event_options_are_defaulted():
    cfg = from_mapping(config(events=[{
        "mechanism": "param_drift",
        "target": {"tool": "ETCH-01"},
        "onset_day": 30,
        "severity": "subtle",
    }]))
    only = cfg.events[0]
    assert only["profile"] == {"type": "step"}
    assert only["response"] == {"alarm": False, "repair_delay_days_mean": 0.0,
                                "recovery": "none"}
    assert "chamber" not in only["target"]  # tool-wide, not defaulted


def test_returned_views_do_not_alias_the_configuration():
    cfg = from_mapping(VALID)
    cfg.events[0]["severity"] = "obvious"
    cfg.canonical["lots"] = 999
    assert cfg.events[0]["severity"] == "moderate"
    assert cfg.canonical["lots"] == 20


def test_configuration_loads_from_a_file(tmp_path):
    path = tmp_path / "demo_edge_uniformity.json"
    path.write_text(json.dumps(VALID), encoding="utf-8")
    assert load_scenario(path) == load_scenario_text(json.dumps(VALID))


# ----------------------------------------------------------------- rejection


@pytest.mark.parametrize("header", [
    "scenario/v2", "fabsim.scenario/v1", "scenario/v1.0", "", "v1", 1, None,
])
def test_unsupported_contract_version_is_rejected(header):
    with pytest.raises(ScenarioConfigError):
        from_mapping(config(fabsim=header))


def test_missing_header_is_rejected():
    raw = config()
    del raw["fabsim"]
    with pytest.raises(ScenarioConfigError):
        from_mapping(raw)


@pytest.mark.parametrize("missing", ["fabsim", "name", "world",
                                     "horizon_days", "lots", "default_seed"])
def test_missing_required_field_is_rejected(missing):
    raw = config()
    del raw[missing]
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(raw)
    assert excinfo.value.path == missing


@pytest.mark.parametrize("missing", ["mechanism", "target", "onset_day",
                                     "severity"])
def test_missing_required_event_field_is_rejected(missing):
    raw = event()
    del raw[missing]
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(events=[raw]))
    assert excinfo.value.path == f"events[0].{missing}"


@pytest.mark.parametrize("overrides, path", [
    ({"name": 7}, "name"),
    ({"name": "   "}, "name"),
    ({"description": ["prose"]}, "description"),
    ({"world": "Baseline Fab"}, "world"),
    ({"world": None}, "world"),
    ({"horizon_days": "84"}, "horizon_days"),
    ({"horizon_days": 84.0}, "horizon_days"),
    ({"horizon_days": 0}, "horizon_days"),
    ({"lots": True}, "lots"),
    ({"lots": -1}, "lots"),
    ({"default_seed": -1}, "default_seed"),
    ({"default_seed": 4.2}, "default_seed"),
    ({"events": {"mechanism": "param_drift"}}, "events"),
    ({"distractors": "none"}, "distractors"),
])
def test_invalid_top_level_types_are_rejected(overrides, path):
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(**overrides))
    assert excinfo.value.path == path


@pytest.mark.parametrize("overrides, path", [
    ({"mechanism": "ChamberEdgeUniformity"}, "events[0].mechanism"),
    ({"mechanism": 3}, "events[0].mechanism"),
    ({"target": "ETCH-02"}, "events[0].target"),
    ({"target": {"tool": ""}}, "events[0].target.tool"),
    ({"target": {"tool": "ETCH-02", "chamber": 2}}, "events[0].target.chamber"),
    ({"onset_day": "35"}, "events[0].onset_day"),
    ({"onset_day": -1}, "events[0].onset_day"),
    ({"onset_day": 84}, "events[0].onset_day"),       # horizon is 84 days
    ({"onset_day": 200}, "events[0].onset_day"),
    ({"severity": "catastrophic"}, "events[0].severity"),
    ({"profile": {"type": "swoop"}}, "events[0].profile.type"),
    ({"profile": {"type": "ramp"}}, "events[0].profile.ramp_days"),
    ({"profile": {"type": "ramp", "ramp_days": 0}}, "events[0].profile.ramp_days"),
    ({"profile": {"type": "step", "ramp_days": 7}}, "events[0].profile"),
    ({"response": {"alarm": "yes"}}, "events[0].response.alarm"),
    ({"response": {"recovery": "mostly"}}, "events[0].response.recovery"),
    ({"response": {"repair_delay_days_mean": -1}},
     "events[0].response.repair_delay_days_mean"),
])
def test_invalid_event_fields_are_rejected(overrides, path):
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(events=[event(**overrides)]))
    assert excinfo.value.path == path


@pytest.mark.parametrize("raw, path", [
    ({"mechanism": "benign_offset", "target": {"tool": "CVD-01"}},
     "distractors[0].magnitude"),
    ({"mechanism": "benign_offset", "target": {"tool": "CVD-01"},
      "magnitude": "enormous"}, "distractors[0].magnitude"),
    ({"mechanism": "benign_offset", "magnitude": "small"},
     "distractors[0].target"),
])
def test_invalid_distractors_are_rejected(raw, path):
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(distractors=[raw]))
    assert excinfo.value.path == path


@pytest.mark.parametrize("raw, path", [
    (config(seed=42), ""),
    (config(events=[event(onset_dya=35)]), "events[0]"),
    (config(events=[event(target={"tool": "ETCH-02", "slot": 3})]),
     "events[0].target"),
    (config(events=[event(response={"alarm": True, "escalate": True})]),
     "events[0].response"),
    (config(distractors=[{"mechanism": "benign_offset",
                          "target": {"tool": "CVD-01"},
                          "magnitude": "small", "note": "hi"}]),
     "distractors[0]"),
])
def test_unknown_fields_are_rejected(raw, path):
    """Strict by design: a misspelled field must not be silently ignored."""
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(raw)
    assert excinfo.value.path == path


def test_non_object_configuration_is_rejected():
    with pytest.raises(ScenarioConfigError):
        load_scenario_text("[]")


def test_duplicate_keys_are_rejected():
    """Valid JSON, invalid configuration: two stated intents, one survivor."""
    with pytest.raises(ScenarioConfigError):
        load_scenario_text(json.dumps(VALID)[:-1] + ',"lots":40}')


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_rejected(literal):
    text = json.dumps(config()).replace("84", literal, 1)
    with pytest.raises(ScenarioConfigError):
        load_scenario_text(text)


def test_malformed_json_is_rejected():
    with pytest.raises(ScenarioConfigError):
        load_scenario_text("{'fabsim': 'scenario/v1',}")


@pytest.mark.parametrize("vocabulary, field_path", [
    (SEVERITIES, "severity"),
    (PROFILE_TYPES, "profile"),
    (RECOVERY_MODES, "recovery"),
    (MAGNITUDES, "magnitude"),
])
def test_vocabularies_are_closed_and_populated(vocabulary, field_path):
    assert len(set(vocabulary)) == len(vocabulary) >= 3
    assert all(isinstance(v, str) and v for v in vocabulary)


@pytest.mark.parametrize("severity", SEVERITIES)
def test_every_severity_is_accepted(severity):
    assert from_mapping(config(events=[event(severity=severity)])
                        ).events[0]["severity"] == severity


@pytest.mark.parametrize("profile_type", PROFILE_TYPES)
def test_every_profile_type_is_accepted(profile_type):
    profile = {"type": profile_type}
    if profile_type == "ramp":
        profile["ramp_days"] = 7
    assert from_mapping(config(events=[event(profile=profile)])
                        ).events[0]["profile"]["type"] == profile_type


# ---------------------------------------------------------- routing conditions
#
# ADR-015. The scenario contract carries the *experimental* routing condition;
# the world keeps the standing policy. These tests pin the shape — resolution
# against a world's rosters belongs to `test_routing.py`, and what the share
# actually does to traffic belongs to `test_timeline.py`.


def test_routing_conditions_default_to_an_empty_list():
    """The field is optional, and omitting it is the same as declaring none."""
    assert from_mapping(MINIMAL).routing_conditions == []
    assert from_mapping(config()).routing_conditions == []


def test_a_routing_condition_is_accepted_and_canonicalized():
    cfg = from_mapping(config(routing_conditions=[condition(start_day=28,
                                                            end_day=62)]))
    assert cfg.routing_conditions == [{
        "kind": "product_dedication",
        "product": "Mobile-28",
        "tool": "ETCH-01",
        "operation_type": "ETCH",
        "start_day": 28.0,
        "end_day": 62.0,
        "share": 0.85,
    }]


def test_the_contract_stays_at_v1():
    """No scenario/v2: the field is additive and no config exists to migrate."""
    assert CONTRACT == "fabsim.scenario/v1"
    assert from_mapping(config(routing_conditions=[condition()]))


def test_routing_condition_kinds_are_a_closed_vocabulary():
    assert ROUTING_CONDITION_KINDS == ("product_dedication",)
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(routing_conditions=[condition(kind="tool_swap")]))
    assert excinfo.value.path == "routing_conditions[0].kind"


def test_a_routing_condition_may_not_target_a_chamber():
    """The rule that makes scenario G a confounder rather than a pointer."""
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(routing_conditions=[condition(chamber="B")]))
    assert excinfo.value.path == "routing_conditions[0].chamber"
    assert "tool-level" in str(excinfo.value)


@pytest.mark.parametrize("overrides, path", [
    ({"share": 1.0}, "routing_conditions[0].share"),
    ({"share": 1.2}, "routing_conditions[0].share"),
    ({"share": 0.0}, "routing_conditions[0].share"),
    ({"share": -0.1}, "routing_conditions[0].share"),
    ({"share": "0.85"}, "routing_conditions[0].share"),
    ({"share": True}, "routing_conditions[0].share"),
    ({"start_day": 62.0, "end_day": 28.0}, "routing_conditions[0].end_day"),
    ({"start_day": 30.0, "end_day": 30.0}, "routing_conditions[0].end_day"),
    ({"start_day": -1.0}, "routing_conditions[0].start_day"),
    ({"start_day": 84.0}, "routing_conditions[0].start_day"),  # horizon is 84
    ({"start_day": "28"}, "routing_conditions[0].start_day"),
    ({"product": 7}, "routing_conditions[0].product"),
    ({"tool": ""}, "routing_conditions[0].tool"),
    ({"operation_type": "etch"}, "routing_conditions[0].operation_type"),
])
def test_invalid_routing_conditions_are_rejected(overrides, path):
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(routing_conditions=[condition(**overrides)]))
    assert excinfo.value.path == path


@pytest.mark.parametrize("missing", ["kind", "product", "tool",
                                     "operation_type", "start_day", "end_day",
                                     "share"])
def test_missing_routing_condition_fields_are_rejected(missing):
    raw = condition()
    del raw[missing]
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(routing_conditions=[raw]))
    assert excinfo.value.path == f"routing_conditions[0].{missing}"


def test_unknown_routing_condition_fields_are_rejected():
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(routing_conditions=[condition(priority="high")]))
    assert excinfo.value.path == "routing_conditions[0]"


def test_routing_conditions_must_be_an_array():
    with pytest.raises(ScenarioConfigError) as excinfo:
        from_mapping(config(routing_conditions=CONDITION))
    assert excinfo.value.path == "routing_conditions"


def test_routing_conditions_are_semantic_and_ordered():
    """They change where wafers go, so they change the scenario's identity."""
    plain = from_mapping(config())
    dedicated = from_mapping(config(routing_conditions=[condition()]))
    assert dedicated.config_sha256 != plain.config_sha256

    other = condition(product="Logic-14", start_day=10.0, end_day=20.0)
    assert (from_mapping(config(routing_conditions=[condition(), other])
                         ).config_sha256
            != from_mapping(config(routing_conditions=[other, condition()])
                            ).config_sha256)


@pytest.mark.parametrize("equivalent", [
    pytest.param(lambda: condition(start_day=28, end_day=62), id="int-days"),
    pytest.param(lambda: condition(product="  Mobile-28  "), id="padded"),
])
def test_equivalent_routing_conditions_canonicalize_identically(equivalent):
    assert (from_mapping(config(routing_conditions=[equivalent()])
                         ).config_sha256
            == from_mapping(config(routing_conditions=[condition()])
                            ).config_sha256)


# ------------------------------------------------------------ canonical form


def test_canonical_representation_is_deterministic():
    renders = {load_scenario_text(json.dumps(VALID)).canonical_json
               for _ in range(5)}
    assert len(renders) == 1


@pytest.mark.parametrize("equivalent", [
    pytest.param(lambda: json.dumps(reorder(VALID)), id="key-order"),
    pytest.param(lambda: json.dumps(VALID, indent=4), id="whitespace"),
    pytest.param(lambda: BOM + json.dumps(VALID), id="byte-order-mark"),
    pytest.param(lambda: json.dumps(config(name="  demo-edge-uniformity  ")),
                 id="padded-strings"),
    pytest.param(
        lambda: json.dumps(config(events=[event(onset_day=35.0)])),
        id="integer-vs-float-day"),
    pytest.param(
        lambda: json.dumps(config(events=[event(
            profile={"type": "ramp", "ramp_days": 7.0})])),
        id="integer-vs-float-ramp"),
    pytest.param(
        lambda: json.dumps(config(events=[event(response={
            "alarm": True, "repair_delay_days_mean": 4, "recovery": "partial",
        })])),
        id="integer-vs-float-delay"),
])
def test_equivalent_representations_canonicalize_identically(equivalent):
    baseline = load_scenario_text(json.dumps(VALID))
    variant = load_scenario_text(equivalent())
    assert variant.canonical_json == baseline.canonical_json
    assert variant.config_sha256 == baseline.config_sha256
    assert variant == baseline


def test_omitted_optional_lists_equal_empty_ones():
    explicit = from_mapping(config(events=[], distractors=[],
                                   routing_conditions=[],
                                   name="null", description=""))
    implicit = from_mapping({k: v for k, v in explicit.canonical.items()
                             if k not in ("events", "distractors",
                                          "routing_conditions",
                                          "description")})
    assert implicit.config_sha256 == explicit.config_sha256


def test_documentation_fields_are_excluded_from_identity():
    """Renaming a scenario or editing its prose must not change identity."""
    baseline = from_mapping(VALID)
    renamed = from_mapping(config(name="something-entirely-different",
                                  description="rewritten prose"))
    assert renamed.config_sha256 == baseline.config_sha256
    assert renamed.scenario_id == baseline.scenario_id
    assert renamed.canonical_json != baseline.canonical_json  # prose kept
    identity = json.loads(baseline.identity_json)
    assert all(key not in identity for key in DOCUMENTATION_KEYS)


@pytest.mark.parametrize("overrides", [
    {"world": "baseline_fab_v2"},
    {"horizon_days": 85},
    {"lots": 21},
    {"default_seed": 43},
    {"events": []},
    {"events": [event(severity="obvious")]},
    {"events": [event(onset_day=36)]},
    {"events": [event(mechanism="param_drift")]},
    {"events": [event(target={"tool": "ETCH-02"})]},
    {"events": [event(target={"tool": "ETCH-02", "chamber": "A"})]},
    {"events": [event(profile={"type": "step"})]},
    {"events": [event(profile={"type": "ramp", "ramp_days": 8})]},
    {"events": [event(response={"alarm": False})]},
    {"distractors": []},
    {"distractors": [{"mechanism": "benign_offset",
                      "target": {"tool": "CVD-01"}, "magnitude": "large"}]},
    {"routing_conditions": [CONDITION]},
])
def test_meaningful_differences_change_the_hash(overrides):
    assert (from_mapping(config(**overrides)).config_sha256
            != from_mapping(VALID).config_sha256)


def test_event_order_is_semantic():
    first = event()
    second = event(mechanism="param_drift", target={"tool": "ETCH-01"},
                   onset_day=50)
    assert (from_mapping(config(events=[first, second])).config_sha256
            != from_mapping(config(events=[second, first])).config_sha256)


# -------------------------------------------------------- dataset identities


def test_dataset_identity_is_deterministic():
    identities = {from_mapping(VALID).dataset_identity(
        42, world_sha256=WORLD_DIGEST) for _ in range(5)}
    assert len(identities) == 1
    identity = identities.pop()
    assert identity.scenario_id == from_mapping(VALID).scenario_id
    assert identity.seed == 42
    assert identity.world_sha256 == WORLD_DIGEST
    assert identity.fabsim_version == FABSIM_VERSION
    assert identity.schema_version == SCHEMA_VERSION


def test_dataset_identity_shape():
    identity = from_mapping(VALID).dataset_identity(
        42, world_sha256=WORLD_DIGEST)
    assert re.fullmatch(r"scn-[0-9a-f]{12}", identity.scenario_id)
    assert identity.dataset_id == f"{identity.scenario_id}-s042"
    assert re.fullmatch(r"scn-[0-9a-f]{12}-s\d{3,}", identity.dataset_id)
    assert re.fullmatch(r"[0-9a-f]{64}", identity.config_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", identity.world_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", identity.build_fingerprint)


def test_seed_defaults_to_the_configurations_default_seed():
    cfg = from_mapping(VALID)
    assert (cfg.dataset_identity(world_sha256=WORLD_DIGEST)
            == cfg.dataset_identity(cfg.default_seed,
                                    world_sha256=WORLD_DIGEST))


@pytest.mark.parametrize("seed", [0, 1, 41, 43, 1000])
def test_different_seeds_give_different_dataset_identities(seed):
    cfg = from_mapping(VALID)
    baseline = cfg.dataset_identity(42, world_sha256=WORLD_DIGEST)
    other = cfg.dataset_identity(seed, world_sha256=WORLD_DIGEST)
    assert other.dataset_id != baseline.dataset_id
    assert other.build_fingerprint != baseline.build_fingerprint
    assert other.scenario_id == baseline.scenario_id  # same scenario


def test_different_scenarios_give_different_dataset_identities():
    left = from_mapping(VALID).dataset_identity(42, world_sha256=WORLD_DIGEST)
    right = from_mapping(config(lots=21)).dataset_identity(
        42, world_sha256=WORLD_DIGEST)
    assert left.scenario_id != right.scenario_id
    assert left.dataset_id != right.dataset_id
    assert left.build_fingerprint != right.build_fingerprint


def test_the_world_participates_in_the_reproducibility_contract():
    """Same config, same seed, a different world — a different dataset.

    The ids cannot say so (`dataset_id` names a scenario and a seed), which is
    precisely why the fingerprint has to.
    """
    cfg = from_mapping(VALID)
    baseline = cfg.dataset_identity(42, world_sha256=WORLD_DIGEST)
    other = cfg.dataset_identity(42, world_sha256=OTHER_WORLD_DIGEST)
    assert other.build_fingerprint != baseline.build_fingerprint
    assert other.dataset_id == baseline.dataset_id


@pytest.mark.parametrize("bad_digest", [
    None, "", "not-a-digest", "5C" * 32, "5c" * 31, "5c" * 33, 42,
])
def test_a_missing_or_malformed_world_digest_is_rejected(bad_digest):
    """No placeholder world: a fingerprint that omitted the world would be
    claiming a reproducibility it cannot deliver."""
    with pytest.raises(ScenarioConfigError):
        from_mapping(VALID).dataset_identity(42, world_sha256=bad_digest)


def test_the_world_digest_has_no_default():
    with pytest.raises(TypeError):
        from_mapping(VALID).dataset_identity(42)


@pytest.mark.parametrize("versions", [
    {"fabsim_version": "9.9.9"},
    {"schema_version": "3.0"},
])
def test_versions_participate_in_the_reproducibility_contract(versions):
    """Same config, same seed, different generator or schema — a different
    build, and the fingerprint says so even though the ids cannot."""
    cfg = from_mapping(VALID)
    baseline = cfg.dataset_identity(42, world_sha256=WORLD_DIGEST)
    other = cfg.dataset_identity(42, world_sha256=WORLD_DIGEST, **versions)
    assert other.build_fingerprint != baseline.build_fingerprint
    assert other.dataset_id == baseline.dataset_id


@pytest.mark.parametrize("bad_seed", [-1, "42", 4.2, True])
def test_invalid_build_seed_is_rejected(bad_seed):
    with pytest.raises((TypeError, ValueError)):
        from_mapping(VALID).dataset_identity(bad_seed,
                                             world_sha256=WORLD_DIGEST)


def test_dataset_id_does_not_disclose_the_scenario():
    """Anti-leakage D5: identifiers are opaque; the slug lives in the config
    and the truth artifact, nowhere else."""
    cfg = from_mapping(config(name="etch-02-chamber-b-is-the-culprit"))
    identity = cfg.dataset_identity(42, world_sha256=WORLD_DIGEST)
    haystack = f"{identity.scenario_id} {identity.dataset_id}".lower()
    for token in ("etch", "chamber", "culprit", "uniformity", "demo",
                  "moderate", "benign"):
        assert token not in haystack
    assert derive_dataset_id(cfg.scenario_id, 42) == identity.dataset_id


# --------------------------------------------------- environment independence


def test_identity_ignores_the_file_path(tmp_path):
    text = json.dumps(VALID)
    first = tmp_path / "demo_edge_uniformity.json"
    second = tmp_path / "nested"
    second.mkdir()
    second /= "z.json"
    first.write_text(text, encoding="utf-8")
    second.write_text(text, encoding="utf-8")

    assert (load_scenario(first).dataset_identity(42,
                                                  world_sha256=WORLD_DIGEST)
            == load_scenario(second).dataset_identity(
                42, world_sha256=WORLD_DIGEST))


_IDENTITY_PROBE = """
import json, sys
from fabsim.scenario import load_scenario_text
cfg = load_scenario_text(sys.stdin.read())
identity = cfg.dataset_identity(7, world_sha256="%s")
print(json.dumps({
    "canonical_json": cfg.canonical_json,
    "config_sha256": cfg.config_sha256,
    "scenario_id": identity.scenario_id,
    "dataset_id": identity.dataset_id,
    "build_fingerprint": identity.build_fingerprint,
}, sort_keys=True))
""" % WORLD_DIGEST


def _probe(cwd, hash_seed: str, extra_env: dict[str, str]) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    env.update(extra_env)
    result = subprocess.run([sys.executable, "-c", _IDENTITY_PROBE],
                            cwd=str(cwd), env=env, input=json.dumps(VALID),
                            capture_output=True, text=True, check=True)
    return result.stdout


def test_identity_ignores_process_and_environment(tmp_path):
    """Different interpreter runs, hash salts, working directories, locale
    and user-shaped variables — the identity may not move."""
    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()

    first = _probe(first_dir, "0", {"LANG": "C", "TZ": "UTC"})
    second = _probe(second_dir, "999", {"LANG": "de_DE.UTF-8",
                                        "TZ": "Asia/Tokyo",
                                        "FABSIM_UNRELATED": "value"})

    assert first == second
    assert json.loads(first)["scenario_id"] == from_mapping(VALID).scenario_id
