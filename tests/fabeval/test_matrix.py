"""
The five-scenario benchmark matrix, end to end.

One expensive fixture builds what the criteria need — the five library
scenarios at seed 42, three seeds of the equipment fault for A2, and a second
independent build of the null for A1 — and everything here scores that. It
takes a couple of minutes and about a gigabyte of temporary disk, which is
what a benchmark over a five-dataset library costs; the alternative is a
matrix that grades something smaller than the library it claims to grade.

The mutation tests at the end are the point of the file. A benchmark that
only ever sees a healthy simulator cannot tell you it would notice an
unhealthy one, so each critical check is fed a deliberately broken dataset and
must fail.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from fabeval import BLOCKED, PARTIAL, PASS, build_library, evaluate, render
from fabeval.acceptance import (
    arc_ordering,
    check_a1,
    check_a2,
    check_a3,
    check_a9,
    check_a10,
)
from fabeval.fixtures import EXPECTATIONS, evaluate_expectation, expectation_for
from fabeval.leakage import l7_null_blindness, run_leakage_suite
from fabeval.matrix import A2_SEEDS, DEFAULT_SEED, DEMO_SCENARIO, LIBRARY


@pytest.fixture(scope="module")
def built(world, tmp_path_factory):
    """The benchmark's datasets. The expensive fixture; built once."""
    return build_library(tmp_path_factory.mktemp("benchmark"), world=world)


@pytest.fixture(scope="module")
def report(built):
    return evaluate(built)


def primary(built, scenario):
    return built[(scenario, DEFAULT_SEED)][0]


# ------------------------------------------------------------- the matrix


def test_the_matrix_covers_the_library_and_the_seeds_the_criteria_need(built):
    keys = set(built)
    for scenario in LIBRARY:
        assert (scenario, DEFAULT_SEED) in keys, scenario
    for seed in A2_SEEDS:
        assert (DEMO_SCENARIO, seed) in keys, seed
    assert any(len(copies) == 2 for copies in built.values()), \
        "A1 needs one configuration built twice"


def test_every_row_carries_a_complete_provenance_statement(report):
    """A row a result cannot be traced from is a row that proves nothing."""
    assert len(report.rows) == len(LIBRARY) + len(A2_SEEDS) - 1
    for row in report.rows:
        assert row.dataset_id.startswith(row.scenario_id + "-s")
        assert len(row.config_sha256) == 64 and len(row.world_sha256) == 64
        assert len(row.build_fingerprint) == 64
        assert len(row.content_sha256) == 64
        assert row.fabsim_version and row.schema_version
        assert row.seed in (DEFAULT_SEED, *A2_SEEDS)
        assert set(row.row_counts) and all(v >= 0
                                           for v in row.row_counts.values())


def test_every_truth_file_in_the_matrix_is_valid(report):
    """The A10 carry-forward, applied to the whole library."""
    bad = [(row.scenario, row.seed, row.truth_error)
           for row in report.rows if not row.truth_valid]
    assert bad == []


def test_every_dataset_has_a_distinct_identity_and_content(report):
    assert len({row.dataset_id for row in report.rows}) == len(report.rows)
    assert len({row.content_sha256 for row in report.rows}) == len(report.rows)
    assert len({row.build_fingerprint for row in report.rows}) == len(report.rows)


def test_the_leakage_suite_finds_nothing(report):
    failures = [(row.scenario, row.seed, finding.test, finding.detail)
                for row in report.rows for finding in row.leakage
                if not finding.passed and not finding.skipped]
    assert failures == []


def test_the_report_renders_without_a_console_encoding_problem(report):
    """A plain-text report that cannot be printed where the project runs is
    not a report. Windows consoles are cp1252; the renderer stays ASCII."""
    text = render(report)
    text.encode("ascii")
    assert "benchmark matrix" in text
    for scenario in LIBRARY:
        assert scenario in text
    for criterion in [f"A{n}" for n in range(1, 12)]:
        assert criterion in text


def test_the_report_serializes(report):
    payload = report.as_dict()
    assert json.dumps(payload)
    assert len(payload["matrix"]) == len(report.rows)
    assert len(payload["acceptance"]) == 11


# ------------------------------------------------------- acceptance verdicts


def test_every_criterion_is_reported(report):
    assert [v.criterion for v in report.verdicts] == \
        [f"A{n}" for n in range(1, 12)]
    for verdict in report.verdicts:
        assert verdict.status in (PASS, PARTIAL, BLOCKED)
        assert verdict.detail, verdict.criterion


def test_no_criterion_is_reported_pass_without_evidence(report):
    """A verdict is a claim; a claim needs its working shown."""
    for verdict in report.verdicts:
        if verdict.status == PASS:
            assert verdict.evidence, verdict.criterion


@pytest.mark.parametrize("criterion", ["A2", "A4", "A10"])
def test_the_criteria_the_library_settles_are_green(report, criterion):
    assert report.verdict(criterion).status == PASS, \
        report.verdict(criterion).detail


@pytest.mark.parametrize("criterion", ["A1", "A3", "A5", "A6", "A7", "A8",
                                       "A11"])
def test_the_partly_testable_criteria_are_reported_as_partial(report,
                                                              criterion):
    """Not PASS, and not BLOCKED either. Each of these has a half this gate
    genuinely settles and a half that needs CI, a severity sweep or a manual
    review — and saying PASS would make the matrix a worse instrument."""
    verdict = report.verdict(criterion)
    assert verdict.status == PARTIAL, f"{criterion}: {verdict.detail}"
    assert verdict.detail


def test_a9_is_blocked_for_the_reason_the_design_records(report):
    """A9 is *statistical equivalence*, never a replay of the legacy numbers
    (ADR-010) — and it is blocked on the cohort yield deficit, which ADR-021
    already explains and which no constant here may be moved to fix."""
    verdict = report.verdict("A9")
    assert verdict.status == BLOCKED
    assert "yield deficit" in verdict.detail
    assert "4-10" in verdict.detail
    assert any("wafer-map" in line for line in verdict.evidence)


def test_the_only_blocked_criterion_is_a9(report):
    assert report.blocked == ("A9",)


# ------------------------------------------------------- scenario behaviour


def test_scenario_g_remains_genuinely_confounded(built):
    """The hard requirement (§7 of the gate), checked on observable rows."""
    dataset = primary(built, "confounded_chamber_vs_product")
    ok, detail = evaluate_expectation(
        dataset, expectation_for("confounded_chamber_vs_product"))
    assert ok, detail
    assert "outside ->" in detail and "inside" in detail
    assert "went elsewhere" in detail and "came here" in detail


def test_scenario_i_shows_the_whole_arc_in_order(built):
    dataset = primary(built, "fault_repair_recovery")
    ordered, detail = arc_ordering(dataset)
    assert ordered, detail
    assert "condition alarm" in detail


def test_the_null_stays_below_the_natural_variation_floor(built):
    finding = l7_null_blindness(primary(built, "null_baseline"))
    assert finding.passed and not finding.skipped, finding.detail


# ------------------------------------------------------------- mutations
#
# Each of these breaks one thing on purpose. A benchmark that has never been
# shown to fail is a benchmark nobody should trust.


def test_a_false_positive_in_the_null_is_caught(built, tmp_path):
    """Plant a signal in the *null* database and L7 must object.

    A real mutation, at the level L7 actually reads: the dataset directory is
    copied, one chamber's edge-CD readings are pushed out in the copy's own
    `fab.db`, and the same check that passes on the untouched null must fail
    on the poisoned one. Anything less would be testing the helper rather than
    the check.
    """
    import shutil
    import sqlite3

    dataset = primary(built, "null_baseline")
    assert l7_null_blindness(dataset).passed, "the real null must be quiet"

    poisoned_dir = tmp_path / "poisoned"
    shutil.copytree(dataset.directory, poisoned_dir)
    connection = sqlite3.connect(str(poisoned_dir / "fab.db"))
    try:
        # One chamber, one channel, pushed 30% out. Nothing else changes.
        connection.execute("""
            UPDATE metrology SET value = value * 1.3
            WHERE param_name = 'cd_nm_edge' AND wafer_id IN (
                SELECT r.wafer_id FROM runs r
                JOIN chambers c ON c.chamber_id = r.chamber_id
                JOIN tools t ON t.tool_id = c.tool_id
                WHERE t.tool_name = 'ETCH-02' AND c.chamber_name = 'B')""")
        connection.commit()
    finally:
        connection.close()

    finding = l7_null_blindness(replace(dataset, directory=poisoned_dir))
    assert not finding.passed and not finding.skipped, finding.detail
    assert "ETCH-02/B" in finding.detail


def test_a_broken_temporal_ordering_is_caught(built):
    """Move the repair before the alarm and the arc check must object."""
    dataset = primary(built, "fault_repair_recovery")
    ordered, _detail = arc_ordering(dataset)
    assert ordered

    broken_truth = copy.deepcopy(dataset.truth)
    # Point the causal maintenance reference at a window that does not exist.
    broken_truth["events"][0]["maintenance_response"]["maint_id"] = 10 ** 6
    mutated = replace(dataset, truth=broken_truth)
    ordered, detail = arc_ordering(mutated)
    assert not ordered and "the dataset lacks" in detail

    # …and an alarm reference the dataset does not contain.
    broken_truth = copy.deepcopy(dataset.truth)
    broken_truth["events"][0]["alarms_emitted"] = [10 ** 6]
    mutated = replace(dataset, truth=broken_truth)
    ordered, detail = arc_ordering(mutated)
    assert not ordered and "the dataset lacks" in detail


def test_a_scenario_g_that_lost_its_confound_is_caught(built):
    """Flatten the routing window and the confound expectation must fail."""
    dataset = primary(built, "confounded_chamber_vs_product")
    flattened = copy.deepcopy(dataset.truth)
    for distractor in flattened["distractors"]:
        if distractor["kind"] == "routing_condition":
            # A window of zero width: inside and outside become the same
            # population, so no imbalance can be measured.
            distractor["condition"]["end_day"] = \
                distractor["condition"]["start_day"]
    mutated = replace(dataset, truth=flattened)
    ok, detail = evaluate_expectation(
        mutated, expectation_for("confounded_chamber_vs_product"))
    assert not ok, detail


def test_a_wrong_acceptance_threshold_changes_the_verdict(built):
    """A3's floors are load-bearing: raise one absurdly and it must block."""
    null = primary(built, "null_baseline")
    faulted = [primary(built, name) for name in LIBRARY
               if name != "null_baseline"]
    assert check_a3(null, faulted).status == PARTIAL

    starved = replace(null, observable=replace(
        null.observable,
        tables=dict(null.observable.tables) | {"alarms": ()}))
    verdict = check_a3(starved, faulted)
    assert verdict.status == BLOCKED
    assert "alarms" in verdict.detail


def test_a1_notices_two_builds_that_disagree(built):
    """The reproducibility check must fail when reproducibility fails."""
    pair = next(copies for copies in built.values() if len(copies) == 2)
    assert check_a1({"pair": pair}).status == PARTIAL

    diverged = replace(pair[1], manifest=dict(pair[1].manifest)
                       | {"content_sha256": "0" * 64})
    verdict = check_a1({"pair": [pair[0], diverged]})
    assert verdict.status == BLOCKED and "manifest" in verdict.detail


def test_a2_notices_seeds_that_are_the_same_realization(built):
    """If three seeds gave one cohort, the library would be seed-degenerate."""
    seeds = [built[(DEMO_SCENARIO, seed)][0] for seed in A2_SEEDS]
    assert check_a2(seeds).status == PASS

    clones = [seeds[0], seeds[0], seeds[0]]
    verdict = check_a2(clones)
    assert verdict.status == BLOCKED and "Jaccard" in verdict.detail


def test_a10_notices_an_invalid_truth_file(built):
    """The boundary check must depend on the validator actually running."""
    library = [primary(built, name) for name in LIBRARY]
    assert check_a10(library).status == PASS

    broken_truth = copy.deepcopy(library[0].truth)
    broken_truth.pop("distractors")
    mutated = [replace(library[0], truth=broken_truth)] + library[1:]
    verdict = check_a10(mutated)
    assert verdict.status == BLOCKED and "invalid truth" in verdict.detail


def test_a9_would_notice_a_deficit_inside_the_checklist_band(built):
    """The A9 check is not hard-wired to fail.

    It reports BLOCKED today because the measured deficit is +0.47 pts, not
    because the function cannot report anything else. Feeding it a yield table
    where the affected chamber really does lose 4-10 points moves the verdict
    — which is what makes today's BLOCKED a measurement rather than a stub.
    """
    demo = primary(built, DEMO_SCENARIO)
    verdict = check_a9(demo)
    assert verdict.status == BLOCKED

    # A deficit inside the band is reported as PARTIAL (the manual wafer-map
    # item is still unrun), never as PASS.
    from fabeval import acceptance
    from fabeval.queries import ChamberScore

    real = acceptance.chamber_yield_split
    label = "ETCH-02/B"

    def shifted(db_path, operation="ETCH"):
        scores = real(db_path, operation)
        return {k: (ChamberScore(k, -6.0, v.support) if k == label else v)
                for k, v in scores.items()}

    acceptance.chamber_yield_split = shifted
    try:
        moved = check_a9(demo)
    finally:
        acceptance.chamber_yield_split = real
    assert moved.status == PARTIAL
    assert "wafer-map" in " ".join(moved.evidence)
