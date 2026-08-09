"""
The evaluator's side of the boundary: the schema validator and the one adapter
that joins a report to the answer key — and the arrow between them, which must
point one way only.
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

import fabeval
from fabeval.diagnosisscore import score_dataset, score_population
from fabeval.investigationschema import (InvestigationValidationError,
                                         validate_investigation)
from fabops.diagnosis import diagnose

from tests.fabops.conftest import truth_of


def test_every_library_report_validates(library):
    for scenario, record in library.items():
        report = diagnose(record["db_path"]).to_dict()
        validate_investigation(report)


def test_the_validator_never_repairs_and_names_the_field(library):
    report = diagnose(library["null_baseline"]["db_path"]).to_dict()
    report["abstention"]["alpha"] = 5.0
    with pytest.raises(InvestigationValidationError) as caught:
        validate_investigation(report)
    assert caught.value.path == "$.abstention.alpha"


@pytest.mark.parametrize("break_it,path", [
    (lambda r: r.__setitem__("schema", "something/v9"), "$.schema"),
    (lambda r: r["candidates"][0].__setitem__("status", "maybe"),
     "$.candidates[0].status"),
    (lambda r: r.__setitem__("considered", []), "$.considered"),
    (lambda r: r.__setitem__("insufficient_evidence",
                             not r["insufficient_evidence"]),
     "$.insufficient_evidence"),
])
def test_the_validator_fires_on_each_contract_violation(library, break_it,
                                                        path):
    report = diagnose(library["chamber_edge_uniformity"]["db_path"]).to_dict()
    break_it(report)
    with pytest.raises(InvestigationValidationError) as caught:
        validate_investigation(report)
    assert caught.value.path == path


def test_a_not_assessable_candidate_without_a_reason_is_refused(library):
    report = diagnose(library["null_baseline"]["db_path"]).to_dict()
    victim = next(i for i, c in enumerate(report["candidates"])
                  if c["status"] == "not_assessable")
    report["candidates"][victim]["reason"] = ""
    with pytest.raises(InvestigationValidationError) as caught:
        validate_investigation(report)
    assert caught.value.path == f"$.candidates[{victim}].reason"


# ------------------------------------------------------------- the adapter


def test_the_adapter_joins_on_dataset_id_and_refuses_a_mismatch(library):
    record = library["chamber_edge_uniformity"]
    report = diagnose(record["db_path"]).to_dict()
    truth = truth_of(record)
    outcome = score_dataset(report, truth, "chamber_edge_uniformity")
    assert outcome.faulted and outcome.planted
    assert outcome.of > 0

    other = truth_of(library["null_baseline"])
    with pytest.raises(ValueError, match="cannot be scored against"):
        score_dataset(report, other)


def test_a_fault_free_world_scores_as_a_correct_abstention(library):
    record = library["null_baseline"]
    outcome = score_dataset(diagnose(record["db_path"]).to_dict(),
                            truth_of(record), "null_baseline")
    assert not outcome.faulted
    assert outcome.correct_abstention
    assert not outcome.false_alarm


def test_a_population_score_reports_what_it_was_measured_on(library):
    pairs = [(diagnose(r["db_path"]).to_dict(), truth_of(r), name)
             for name, r in library.items()]
    score = score_population(pairs, population="the five-scenario library",
                             notes=("not a capability claim: the library is "
                                    "below the population Phase 6 requires",))
    rendered = score.render()
    assert "the five-scenario library" in rendered
    assert "not a capability claim" in rendered
    assert score.false_alarm_rate is not None
    assert score.detection_rate is not None


def test_the_score_never_reads_a_mechanism(library):
    """Attribution and onset are scored; the hidden mechanism is not.

    The observable plane carries no channel that identifies which mechanism
    acted, so scoring an engine on naming one would score it on something it
    cannot see.
    """
    source = Path(inspect.getfile(score_dataset)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "mechanism" not in node.value or "not" in node.value.lower()
    # and structurally: the outcome record has no field for one
    from fabeval.diagnosisscore import DiagnosisOutcome
    import dataclasses
    fields = {f.name for f in dataclasses.fields(DiagnosisOutcome)}
    assert "mechanism" not in fields


# ------------------------------------------------------------- the arrow


def test_the_engine_does_not_import_the_evaluator():
    """One-way. `fabeval` may call the engine; the engine may not see it."""
    package = Path(inspect.getfile(diagnose)).parent
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [(node.module or "").split(".")[0]]
            for root in roots:
                assert root not in {"fabeval", "fabsim"}, \
                    f"{path.name} imports {root}"


def test_the_evaluator_still_writes_nothing():
    """`fabeval` gained two modules; the no-write property must survive."""
    package = Path(inspect.getfile(fabeval)).parent
    forbidden = {"write_text", "write_bytes", "mkdir", "unlink", "commit",
                 "rmtree"}
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                assert "build_library" in path.name or path.name == "matrix.py", \
                    f"{path.name} calls .{node.attr}()"
