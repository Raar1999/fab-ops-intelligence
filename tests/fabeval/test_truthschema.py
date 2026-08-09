"""
Tests for the `fabsim.truth/v1` validator.

Mostly mutation tests, and deliberately so: a validator is only worth what it
*rejects*, and a suite that only fed it valid input would pass just as
happily if every check were `return True`. Each case below takes a real truth
artifact, breaks exactly one thing, and requires the validator to notice —
and to say **where**, because a rejection that does not name the field makes
the next hour someone else's problem.

The artifact under test is built once from the smallest scenario that still
has an event, so the fixture is cheap and the failures are about the schema
rather than about the physics.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from fabeval.truthschema import (
    TRUTH_SCHEMA,
    TruthValidationError,
    validate_truth,
    validate_truth_file,
)

SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"


@pytest.fixture(scope="module")
def truth(world, tmp_path_factory) -> dict[str, Any]:
    """A real faulted truth artifact — the thing the validator must accept."""
    from fabsim.emit import build_dataset
    from fabsim.scenario import from_mapping, load_scenario

    config = load_scenario(SCENARIO_ROOT / "chamber_edge_uniformity.json")
    raw = json.loads(config.canonical_json)
    # The 84-day horizon must stay - the onset is day 35 - so lots are what
    # get reduced. Eight is the smallest count at which the affected chamber
    # actually processes wafers after the onset: below it the fab is so
    # lightly loaded that the cohort comes out empty, and a truth artifact
    # with no affected wafers would not exercise the fields this file is
    # mostly about.
    raw["lots"] = 8
    dataset = build_dataset(from_mapping(raw), world=world,
                            root=tmp_path_factory.mktemp("truth"),
                            created_at="pinned")
    return dataset.truth


@pytest.fixture(scope="module")
def confounded_truth(world, tmp_path_factory) -> dict[str, Any]:
    """G's truth, which carries a *second* distractor kind."""
    from fabsim.emit import build_dataset
    from fabsim.scenario import from_mapping, load_scenario

    config = load_scenario(
        SCENARIO_ROOT / "confounded_chamber_vs_product.json")
    raw = json.loads(config.canonical_json)
    raw["lots"] = 8
    dataset = build_dataset(from_mapping(raw), world=world,
                            root=tmp_path_factory.mktemp("confounded-truth"),
                            created_at="pinned")
    return dataset.truth


@pytest.fixture(scope="module")
def null_truth(world, tmp_path_factory) -> dict[str, Any]:
    from fabsim.emit import build_dataset
    from fabsim.scenario import from_mapping, load_scenario

    config = load_scenario(SCENARIO_ROOT / "null_baseline.json")
    raw = json.loads(config.canonical_json)
    raw["lots"] = 2
    dataset = build_dataset(from_mapping(raw), world=world,
                            root=tmp_path_factory.mktemp("null-truth"),
                            created_at="pinned")
    return dataset.truth


def broken(truth: dict[str, Any], mutate) -> dict[str, Any]:
    copied = copy.deepcopy(truth)
    mutate(copied)
    return copied


# --------------------------------------------------------------- acceptance


def test_a_real_truth_artifact_validates(truth, null_truth):
    validate_truth(truth)
    validate_truth(null_truth)


def test_a_null_carries_an_answer_key_even_with_no_events(null_truth):
    """§3: "the null scenario emits `events: []` with distractors populated —
    an empty answer key is still an answer key"."""
    assert null_truth["events"] == []
    assert null_truth["distractors"]
    assert null_truth["latent_summaries"] == {}
    validate_truth(null_truth)


def test_the_validator_reads_a_file_and_returns_it(tmp_path, truth):
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(truth), encoding="utf-8")
    assert validate_truth_file(path) == truth


def test_malformed_json_is_a_rejection_not_a_crash(tmp_path):
    path = tmp_path / "truth.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(TruthValidationError, match="invalid JSON"):
        validate_truth_file(path)


# ------------------------------------------------------- mutation: top level


@pytest.mark.parametrize("path, mutate", [
    ("schema", lambda t: t.__setitem__("schema", "fabsim.truth/v2")),
    ("events", lambda t: t.pop("events")),
    ("distractors", lambda t: t.pop("distractors")),
    ("latent_summaries", lambda t: t.pop("latent_summaries")),
    ("hidden_counts", lambda t: t.pop("hidden_counts")),
    ("seed", lambda t: t.__setitem__("seed", "42")),
    ("config_sha256", lambda t: t.__setitem__("config_sha256", "abc")),
    ("world_sha256", lambda t: t.__setitem__("world_sha256", "")),
    ("fabsim_version", lambda t: t.__setitem__("fabsim_version", "one")),
    ("schema_version", lambda t: t.__setitem__("schema_version", "two")),
    ("dataset_id", lambda t: t.__setitem__("dataset_id", "dataset-1")),
    ("scenario_id", lambda t: t.__setitem__("scenario_id", "scn-XYZ")),
    ("extra", lambda t: t.__setitem__("extra", 1)),
])
def test_a_broken_top_level_field_is_rejected(truth, path, mutate):
    with pytest.raises(TruthValidationError) as excinfo:
        validate_truth(broken(truth, mutate))
    assert excinfo.value.path.startswith(path.split(".")[0]) or path == "extra"


def test_an_identity_that_disagrees_with_itself_is_rejected(truth):
    """Scenario identity handling, mutation-checked: `dataset_id` must extend
    `scenario_id` and carry the seed, or the two planes cannot be joined."""
    with pytest.raises(TruthValidationError, match="does not extend"):
        validate_truth(broken(truth, lambda t: t.__setitem__(
            "dataset_id", "scn-000000000000-s042")))
    with pytest.raises(TruthValidationError, match="does not carry seed"):
        validate_truth(broken(truth, lambda t: t.__setitem__("seed", 7)))


# ---------------------------------------------------------- mutation: events


@pytest.mark.parametrize("expected, mutate", [
    ("events[0].mechanism",
     lambda t: t["events"][0].__setitem__("mechanism", "chamber_is_bad")),
    ("events[0].latent",
     lambda t: t["events"][0].__setitem__("latent", "vibes")),
    ("events[0].severity",
     lambda t: t["events"][0].__setitem__("severity", "catastrophic")),
    ("events[0].event_id",
     lambda t: t["events"][0].__setitem__("event_id", "E7")),
    ("events[0].end",
     lambda t: t["events"][0].__setitem__("end", "2020-01-01 00:00:00")),
    ("events[0].onset",
     lambda t: t["events"][0].__setitem__("onset", "yesterday")),
    ("events[0].target.chamber_ids",
     lambda t: t["events"][0]["target"].__setitem__("chamber_ids", [])),
    ("events[0].profile.type",
     lambda t: t["events"][0]["profile"].__setitem__("type", "sawtooth")),
    ("events[0].severity_realized.aggregate_shift_sigma",
     lambda t: t["events"][0]["severity_realized"].__setitem__(
         "aggregate_shift_sigma", "big")),
    ("events[0].causal_chain[0]",
     lambda t: t["events"][0]["causal_chain"].__setitem__(0, "latent.vibes")),
    ("events[0].alarms_emitted",
     lambda t: t["events"][0].__setitem__("alarms_emitted", [9, 3, 5])),
    ("events[0].affected_runs",
     lambda t: t["events"][0].__setitem__("affected_runs", [9, 3])),
    ("events[0].maintenance_response.recovery_fraction",
     lambda t: (t["events"][0]["maintenance_response"] or {}).__setitem__(
         "recovery_fraction", 1.4)),
    ("events[0].expected_impact.cohort_size",
     lambda t: t["events"][0]["expected_impact"].__setitem__(
         "cohort_size", 10 ** 6)),
])
def test_a_broken_event_field_is_rejected(truth, expected, mutate):
    if "maintenance_response" in expected \
            and truth["events"][0]["maintenance_response"] is None:
        pytest.skip("this realization earned no repair")
    with pytest.raises(TruthValidationError) as excinfo:
        validate_truth(broken(truth, mutate))
    assert excinfo.value.path == expected, excinfo.value.path


def test_a_wafer_exposure_out_of_range_names_the_wafer(truth):
    """The failure path §1 of the gate asks for, exactly."""
    wafers = truth["events"][0]["affected_wafers"]
    assert wafers, "the fixture must have an affected cohort to break"
    last = len(wafers) - 1

    with pytest.raises(TruthValidationError) as excinfo:
        validate_truth(broken(truth, lambda t: t["events"][0][
            "affected_wafers"][last].__setitem__("exposure", 1.5)))
    assert excinfo.value.path == f"events[0].affected_wafers[{last}].exposure"

    with pytest.raises(TruthValidationError) as excinfo:
        validate_truth(broken(truth, lambda t: t["events"][0][
            "affected_wafers"][last].__setitem__("exposure", 0.0)))
    assert excinfo.value.path == f"events[0].affected_wafers[{last}].exposure"
    assert "never exposed" in excinfo.value.reason


def test_a_causal_chain_that_starts_elsewhere_is_rejected(truth):
    """A chain must begin at the latent it drives, or it is a narrative."""
    with pytest.raises(TruthValidationError, match="must begin at the latent"):
        validate_truth(broken(truth, lambda t: t["events"][0][
            "causal_chain"].__setitem__(0, "wafer_yield")))


# ----------------------------------------------------- mutation: the rest


def test_an_empty_distractor_list_is_rejected(truth):
    """Every world carries standing benign structure; an empty list would
    make false attribution unscoreable."""
    with pytest.raises(TruthValidationError) as excinfo:
        validate_truth(broken(truth, lambda t: t.__setitem__("distractors", [])))
    assert excinfo.value.path == "distractors"


def test_dropping_the_standing_offsets_is_rejected(confounded_truth):
    """Scored on G, whose distractor list has a second entry — so removing
    the F11 baseline leaves a *non-empty* list and the specific rule fires
    rather than the empty-list one."""
    validate_truth(confounded_truth)
    assert {d["kind"] for d in confounded_truth["distractors"]} == {
        "benign_offset_baseline", "routing_condition"}
    with pytest.raises(TruthValidationError, match="rule F11"):
        validate_truth(broken(confounded_truth, lambda t: t.__setitem__(
            "distractors", [d for d in t["distractors"]
                            if d["kind"] != "benign_offset_baseline"])))


def test_a_routing_condition_distractor_validates(confounded_truth):
    """G declares the confounder as observable data (ADR-015); truth records
    it as a distractor so false attribution to the product is scoreable."""
    condition = next(d for d in confounded_truth["distractors"]
                     if d["kind"] == "routing_condition")
    assert condition["declared"] is True
    assert {"product", "tool", "start_day", "end_day", "share"} <= set(
        condition["condition"])


def test_an_affected_chamber_without_a_trajectory_is_rejected(truth):
    """Onset error cannot be scored without one, so its absence is a fault."""
    with pytest.raises(TruthValidationError, match="onset error"):
        validate_truth(broken(truth,
                              lambda t: t.__setitem__("latent_summaries", {})))


def test_a_summary_key_that_does_not_name_a_chamber_is_rejected(truth):
    def mutate(t):
        t["latent_summaries"]["something_else"] = [0.0, 1.0]
    with pytest.raises(TruthValidationError) as excinfo:
        validate_truth(broken(truth, mutate))
    assert excinfo.value.path == "latent_summaries.something_else"


def test_zero_benign_offsets_is_rejected(truth):
    with pytest.raises(TruthValidationError, match="rule F11"):
        validate_truth(broken(truth, lambda t: t["hidden_counts"].__setitem__(
            "benign_offsets", 0)))


def test_the_validator_never_repairs_what_it_reads(truth):
    """It validates; it does not normalize, default or coerce."""
    before = json.dumps(truth, sort_keys=True)
    validate_truth(truth)
    assert json.dumps(truth, sort_keys=True) == before


def test_the_two_deliberately_absent_fields_stay_absent(truth):
    """ADR-023 §5: `expected_mechanism_share` is a qualitative bucket nothing
    realizes, and requiring it here would require the emitter to guess."""
    entry = truth["events"][0]["affected_wafers"][0]
    assert set(entry) == {"wafer_id", "exposure"}
    validate_truth(truth)      # and its absence is accepted


def test_the_schema_constant_matches_what_the_emitter_writes(truth):
    from fabsim.emit.truth import TRUTH_SCHEMA as EMITTED

    assert truth["schema"] == TRUTH_SCHEMA == EMITTED
