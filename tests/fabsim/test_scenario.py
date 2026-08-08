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
    SEVERITIES,
    ScenarioConfigError,
    derive_dataset_id,
    from_mapping,
    load_scenario,
    load_scenario_text,
)

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
                                   name="null", description=""))
    implicit = from_mapping({k: v for k, v in explicit.canonical.items()
                             if k not in ("events", "distractors",
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
    identities = {from_mapping(VALID).dataset_identity(42)
                  for _ in range(5)}
    assert len(identities) == 1
    identity = identities.pop()
    assert identity.scenario_id == from_mapping(VALID).scenario_id
    assert identity.seed == 42
    assert identity.fabsim_version == FABSIM_VERSION
    assert identity.schema_version == SCHEMA_VERSION


def test_dataset_identity_shape():
    identity = from_mapping(VALID).dataset_identity(42)
    assert re.fullmatch(r"scn-[0-9a-f]{12}", identity.scenario_id)
    assert identity.dataset_id == f"{identity.scenario_id}-s042"
    assert re.fullmatch(r"scn-[0-9a-f]{12}-s\d{3,}", identity.dataset_id)
    assert re.fullmatch(r"[0-9a-f]{64}", identity.config_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", identity.build_fingerprint)


def test_seed_defaults_to_the_configurations_default_seed():
    cfg = from_mapping(VALID)
    assert cfg.dataset_identity() == cfg.dataset_identity(cfg.default_seed)


@pytest.mark.parametrize("seed", [0, 1, 41, 43, 1000])
def test_different_seeds_give_different_dataset_identities(seed):
    cfg = from_mapping(VALID)
    baseline = cfg.dataset_identity(42)
    other = cfg.dataset_identity(seed)
    assert other.dataset_id != baseline.dataset_id
    assert other.build_fingerprint != baseline.build_fingerprint
    assert other.scenario_id == baseline.scenario_id  # same scenario


def test_different_scenarios_give_different_dataset_identities():
    left = from_mapping(VALID).dataset_identity(42)
    right = from_mapping(config(lots=21)).dataset_identity(42)
    assert left.scenario_id != right.scenario_id
    assert left.dataset_id != right.dataset_id
    assert left.build_fingerprint != right.build_fingerprint


@pytest.mark.parametrize("versions", [
    {"fabsim_version": "9.9.9"},
    {"schema_version": "3.0"},
])
def test_versions_participate_in_the_reproducibility_contract(versions):
    """Same config, same seed, different generator or schema — a different
    build, and the fingerprint says so even though the ids cannot."""
    cfg = from_mapping(VALID)
    baseline = cfg.dataset_identity(42)
    other = cfg.dataset_identity(42, **versions)
    assert other.build_fingerprint != baseline.build_fingerprint
    assert other.dataset_id == baseline.dataset_id


@pytest.mark.parametrize("bad_seed", [-1, "42", 4.2, True])
def test_invalid_build_seed_is_rejected(bad_seed):
    with pytest.raises((TypeError, ValueError)):
        from_mapping(VALID).dataset_identity(bad_seed)


def test_dataset_id_does_not_disclose_the_scenario():
    """Anti-leakage D5: identifiers are opaque; the slug lives in the config
    and the truth artifact, nowhere else."""
    cfg = from_mapping(config(name="etch-02-chamber-b-is-the-culprit"))
    identity = cfg.dataset_identity(42)
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

    assert (load_scenario(first).dataset_identity(42)
            == load_scenario(second).dataset_identity(42))


_IDENTITY_PROBE = """
import json, sys
from fabsim.scenario import load_scenario_text
cfg = load_scenario_text(sys.stdin.read())
identity = cfg.dataset_identity(7)
print(json.dumps({
    "canonical_json": cfg.canonical_json,
    "config_sha256": cfg.config_sha256,
    "scenario_id": identity.scenario_id,
    "dataset_id": identity.dataset_id,
    "build_fingerprint": identity.build_fingerprint,
}, sort_keys=True))
"""


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
