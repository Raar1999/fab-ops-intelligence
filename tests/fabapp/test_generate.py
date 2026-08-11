"""
Creating a dataset: the five stages, and every way the user can be told no.

`create` is where the product holds both privileges at once — it asks the
simulator for a world and then hands the analysis a path to it — so two
properties matter beyond "it works": what comes back carries no route to the
hidden plane, and building the same thing twice produces the same thing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fabapp import generate, registry

REPO = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPO / "scenarios"


def test_a_created_dataset_is_immediately_usable(faulted):
    assert faulted.record.usable
    assert faulted.validated and not faulted.reused
    assert faulted.scenario == "chamber_edge_uniformity"
    assert faulted.seed == 42
    assert faulted.record.db_path.is_file()
    assert faulted.record.db_path.name == "fab.db"


def test_the_five_stages_are_announced_in_order(product_root):
    seen: list[str] = []
    generate.create("null_baseline", 42, root=product_root,
                    scenario_root=SCENARIO_ROOT, on_stage=seen.append)
    assert seen[0] == "scenario selection"
    assert seen[-1] == "registration"


def test_building_the_same_thing_twice_reuses_it_rather_than_rebuilding(
        product_root, faulted):
    again = generate.create("chamber_edge_uniformity", 42, root=product_root,
                            scenario_root=SCENARIO_ROOT)
    assert again.reused and not again.validated
    assert again.record.dataset_id == faulted.record.dataset_id
    assert again.record.content_sha256 == faulted.record.content_sha256


def test_a_rebuild_reproduces_the_dataset_byte_for_byte(product_root, faulted):
    """Determinism, exercised through the product rather than asserted of the
    emitter. A dataset is fully determined by its configuration, its world, its
    seed and the two version numbers, and the product must not be a sixth
    input."""
    rebuilt = generate.create("chamber_edge_uniformity", 42, root=product_root,
                              scenario_root=SCENARIO_ROOT, rebuild=True)
    assert not rebuilt.reused and rebuilt.validated
    assert rebuilt.record.dataset_id == faulted.record.dataset_id
    assert rebuilt.record.content_sha256 == faulted.record.content_sha256
    assert rebuilt.record.build_fingerprint == faulted.record.build_fingerprint


def test_a_different_seed_is_a_different_dataset(product_root, tmp_path, null):
    """The second seed is built into its **own** root, deliberately.

    `product_root` is the shared discovery fixture and two later files count
    what is in it exactly. A third dataset landing there is a test reaching
    into another test's world, which is the kind of coupling that produces a
    failure three files away from its cause.
    """
    first = generate.create("null_baseline", 42, root=product_root,
                            scenario_root=SCENARIO_ROOT)
    assert first.reused, "the shared fixture's dataset was rebuilt"

    second = generate.create("null_baseline", 101, root=tmp_path,
                             scenario_root=SCENARIO_ROOT)
    assert first.record.dataset_id != second.record.dataset_id
    assert first.record.content_sha256 != second.record.content_sha256
    assert second.record.seed == 101


def test_the_destination_is_knowable_before_the_wait(product_root, faulted):
    """`would_produce` is what lets the screen say "this already exists"
    before somebody waits twenty seconds to find out."""
    predicted = generate.would_produce("chamber_edge_uniformity", 42,
                                       root=product_root,
                                       scenario_root=SCENARIO_ROOT)
    assert predicted.usable
    assert predicted.dataset_id == faulted.record.dataset_id

    unbuilt = generate.would_produce("null_baseline", 777, root=product_root,
                                     scenario_root=SCENARIO_ROOT)
    assert unbuilt.status == registry.MISSING
    assert not (product_root / "should-not-exist").exists()


def test_what_comes_back_carries_no_route_to_the_hidden_plane(faulted):
    payload = faulted.to_dict()
    assert "truth" not in str(payload).lower()
    assert "directory" not in payload["dataset"]


def test_an_unknown_scenario_fails_at_selection_and_lists_the_real_ones(
        product_root):
    with pytest.raises(generate.GenerationError) as raised:
        generate.create("not_a_scenario", 42, root=product_root,
                        scenario_root=SCENARIO_ROOT)
    assert raised.value.stage == "scenario selection"
    assert "null_baseline" in str(raised.value)


def test_an_invalid_configuration_fails_at_selection(product_root, tmp_path):
    (tmp_path / "malformed.json").write_text(
        '{"fabsim": "scenario/v1", "name": "malformed"}', encoding="utf-8")
    with pytest.raises(generate.GenerationError) as raised:
        generate.create("malformed", 42, root=product_root,
                        scenario_root=tmp_path)
    assert raised.value.stage == "scenario selection"
    assert "not valid" in str(raised.value)


def test_a_scenario_naming_a_world_that_does_not_exist_fails_at_selection(
        product_root, tmp_path):
    source = (SCENARIO_ROOT / "null_baseline.json").read_text(encoding="utf-8")
    (tmp_path / "no_world.json").write_text(
        source.replace('"baseline_fab_v1"', '"no_such_world"'),
        encoding="utf-8")
    with pytest.raises(generate.GenerationError) as raised:
        generate.create("no_world", 42, root=product_root,
                        scenario_root=tmp_path)
    assert raised.value.stage == "scenario selection"
    assert "no_such_world" in str(raised.value)


def test_a_seed_outside_the_generators_range_is_refused(product_root):
    with pytest.raises(Exception) as raised:
        generate.create("null_baseline", -1, root=product_root,
                        scenario_root=SCENARIO_ROOT)
    assert "seed" in str(raised.value).lower()


def test_a_failed_build_reports_the_stage_it_failed_at(product_root,
                                                       monkeypatch):
    """The generator's own self-test fails a build rather than shipping a
    dataset that violates its invariants; the product has to say which of the
    five stages that was."""
    from fabsim.selftest import SelfTestError

    def refuse(*args, **kwargs):
        raise SelfTestError("§4.3 reconciliation", "planted failure")

    monkeypatch.setattr(generate, "build_observable", refuse)
    with pytest.raises(generate.GenerationError) as raised:
        generate.create("null_baseline", 2024, root=product_root,
                        scenario_root=SCENARIO_ROOT)
    assert raised.value.stage == "validation"
    assert "planted failure" in str(raised.value)


def test_a_build_that_cannot_be_written_reports_generation(product_root,
                                                           monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(generate, "build_observable", refuse)
    with pytest.raises(generate.GenerationError) as raised:
        generate.create("null_baseline", 2024, root=product_root,
                        scenario_root=SCENARIO_ROOT)
    assert raised.value.stage == "generation"
    assert "no space left" in str(raised.value)
