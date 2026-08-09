"""
What the engine must actually do: be deterministic, be blind to the hidden
plane without being inert, abstain on a fault-free world, and never let a
hypothesis disappear.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from fabops.diagnosis import ENGINE, ENGINE_VERSION, diagnose
from fabops.diagnosis.model import ASSESSED, NOT_ASSESSABLE

from tests.fabops.conftest import planted_of, truth_of


# ------------------------------------------------------------ determinism


def test_the_same_database_gives_the_same_report(null_dataset):
    first = diagnose(null_dataset["db_path"]).to_dict()
    second = diagnose(null_dataset["db_path"]).to_dict()
    assert json.dumps(first, sort_keys=True) == json.dumps(second,
                                                           sort_keys=True)


def test_the_report_carries_the_engine_that_produced_it(null_dataset):
    report = diagnose(null_dataset["db_path"])
    assert report.generated_by == ENGINE
    assert ENGINE_VERSION in report.generated_by
    assert report.dataset_id == null_dataset["dataset_id"]


def test_the_report_is_json_serializable(demo_dataset):
    payload = json.dumps(diagnose(demo_dataset["db_path"]).to_dict())
    assert json.loads(payload)["schema"] == "fabops.investigation/v1"


# ------------------------------------------------- truth invariance, and its
#                                                    mirror


def _rewrite_hidden_plane(path: Path) -> None:
    """Replace every value in the answer key with something else."""
    payload = json.loads(path.read_text(encoding="utf-8"))

    def scramble(node):
        if isinstance(node, dict):
            return {k: scramble(v) for k, v in node.items()}
        if isinstance(node, list):
            return [scramble(v) for v in node]
        if isinstance(node, str):
            return node[::-1] + "-rewritten"
        if isinstance(node, bool):
            return not node
        if isinstance(node, (int, float)):
            return node + 1
        return node

    payload["events"] = scramble(payload.get("events", []))
    payload["distractors"] = scramble(payload.get("distractors", []))
    payload["scenario_name"] = "something-else-entirely"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def test_rewriting_the_hidden_plane_does_not_move_the_report(demo_dataset,
                                                             tmp_path):
    """§6.6. The engine reads the observable plane and only that."""
    source = Path(demo_dataset["db_path"])
    truth_source = Path(demo_dataset["truth_path"])
    workspace = tmp_path / "invariance"
    (workspace / "truth").mkdir(parents=True)
    database = workspace / source.name
    shutil.copy2(source, database)
    answer_key = workspace / "truth" / truth_source.name
    shutil.copy2(truth_source, answer_key)

    before = diagnose(database).to_dict()
    digest_before = database.read_bytes()
    _rewrite_hidden_plane(answer_key)
    after = diagnose(database).to_dict()

    assert database.read_bytes() == digest_before, \
        "the observable plane must not have moved"
    assert json.dumps(after, sort_keys=True) == json.dumps(before,
                                                           sort_keys=True)


def test_moving_an_observable_value_does_move_the_report(demo_dataset,
                                                          tmp_path):
    """The mirror. Invariance that passes because the engine is inert is not
    invariance."""
    database = tmp_path / "mirror.db"
    shutil.copy2(demo_dataset["db_path"], database)
    before = diagnose(database).to_dict()

    connection = sqlite3.connect(str(database))
    try:
        connection.execute(
            "UPDATE metrology SET value = value * 1.02 "
            "WHERE param_name = 'cd_nm_edge'")
        connection.commit()
    finally:
        connection.close()

    after = diagnose(database).to_dict()
    assert json.dumps(after, sort_keys=True) != json.dumps(before,
                                                           sort_keys=True)


# ----------------------------------------------------------------- output


def test_a_fault_free_world_abstains(null_dataset):
    report = diagnose(null_dataset["db_path"])
    assert report.insufficient_evidence, report.abstention
    assert report.abstention["p_familywise"] > report.abstention["alpha"]


def test_considered_is_non_empty_whenever_a_candidate_is_offered(library):
    for scenario, record in library.items():
        report = diagnose(record["db_path"])
        if any(c.status == ASSESSED for c in report.candidates):
            assert report.considered, scenario


def test_a_hypothesis_the_data_cannot_score_is_reported_not_dropped(library):
    """`not_assessable` is a state. A hypothesis that disappears has been
    rejected without being asked."""
    report = diagnose(library["confounded_chamber_vs_product"]["db_path"])
    unassessable = [c for c in report.candidates
                    if c.status == NOT_ASSESSABLE]
    assert unassessable, "every kind was scorable, which this world is not"
    assert all(c.reason for c in unassessable)
    kinds = {c.entity.kind for c in unassessable}
    assert "product" in kinds, (
        "the product hypothesis is the rival scenario G exists to pose; it "
        "must be present even when it cannot be scored")
    considered_kinds = {c.entity.kind for c in report.considered}
    assert "product" in considered_kinds


def test_every_offered_candidate_carries_falsifiable_evidence(demo_dataset):
    report = diagnose(demo_dataset["db_path"])
    offered = [c for c in report.candidates if c.status == ASSESSED]
    assert offered
    for candidate in offered:
        assert candidate.evidence
        for item in candidate.evidence:
            assert item.channel and item.statistic and item.comparison
            assert 1 <= item.rank <= item.of


def test_the_anchors_are_shared_by_every_candidate(demo_dataset):
    """No candidate may be scored at a change point of its own."""
    report = diagnose(demo_dataset["db_path"])
    declared = {a.day for a in report.anchors}
    assert declared
    for candidate in report.candidates:
        for item in candidate.evidence:
            assert any(f"{day:.0f}" in item.comparison for day in declared), \
                item.comparison
        for onset in candidate.onsets:
            assert onset.day in declared


def test_an_onset_is_an_interval_that_brackets_its_own_day(library):
    for record in library.values():
        for candidate in diagnose(record["db_path"]).candidates:
            for onset in candidate.onsets:
                low, high = onset.interval
                assert low <= onset.day <= high


# ------------------------------------------------------------- the knobs


def test_an_unknown_statistic_is_refused(null_dataset):
    with pytest.raises(ValueError, match="unknown statistic"):
        diagnose(null_dataset["db_path"], statistic="whatever_looks_best")


def test_the_registry_has_more_than_one_member():
    """A pluggable component with one member is a constant in a costume."""
    from fabops.diagnosis.statistics import DEFAULT_STATISTIC, STATISTICS

    assert len(STATISTICS) >= 2
    assert DEFAULT_STATISTIC in STATISTICS


def test_the_default_statistic_is_the_calibrated_one():
    """ADR-029 §8: the default is chosen on fault-free calibration, and the
    powerful-but-hot alternative stays registered rather than shipped."""
    from fabops.diagnosis.statistics import DEFAULT_STATISTIC

    assert DEFAULT_STATISTIC == "own_scale_step"


# ------------------------------------------------------------- the surface


def test_the_registries_cannot_be_mutated_at_runtime():
    """A module-level dict is a global somebody can edit from anywhere.

    Both registries are read-only mappings, so a caller cannot swap a
    statistic or re-point an evidence family and have every later
    investigation quietly change underneath it.
    """
    from fabops.diagnosis.channels import _PREFIX_FAMILY
    from fabops.diagnosis.statistics import STATISTICS

    with pytest.raises(TypeError):
        STATISTICS["own_scale_step"] = None
    with pytest.raises(TypeError):
        _PREFIX_FAMILY["met"] = "something else"


def test_the_command_line_prints_the_artifact_and_nothing_else(
        null_dataset, capsys):
    """`fabops-diagnose` is a wrapper: one argument in, one artifact out."""
    from fabops.diagnosis.cli import main

    assert main([str(null_dataset["db_path"]), "--indent", "0"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == "fabops.investigation/v1"
    assert payload["dataset_id"] == null_dataset["dataset_id"]
    # the human line goes to stderr so stdout stays a clean artifact
    assert "insufficient evidence" in captured.err


def test_the_command_line_refuses_to_be_given_anything_but_a_database(
        null_dataset):
    """No flag may carry information about the answer."""
    import inspect

    from fabops.diagnosis import cli

    source = inspect.getsource(cli)
    for forbidden in ("truth", "scenario", "expect", "planted", "mechanism"):
        assert f'"--{forbidden}' not in source
        assert f"'--{forbidden}" not in source
