"""
The five-scenario benchmark matrix, end to end.

One expensive fixture builds what the criteria need — the five library
scenarios at seed 42, three seeds of the equipment fault for A2, three of the
null for A6's natural-variation floor, and a second independent build of the
null for A1 — and everything here scores that. It
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
    # Every scenario at the default seed, plus the extra seeds the criteria
    # ask for: A2's three of the equipment fault, and A6's three of the null
    # (its natural-variation floor is one draw per seed). Counted off the two
    # multi-seed scenarios rather than written as a literal, so the assertion
    # tracks the plan instead of having to be re-derived when it changes.
    extra = 2 * (len(A2_SEEDS) - 1)
    assert len(report.rows) == len(LIBRARY) + extra
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


def test_the_leakage_suite_is_green_on_every_row(report):
    """L1-L11 over every row, with nothing outstanding.

    Until ADR-027 this test named two expected failures — L7 on the null at
    seeds 101 and 2024. Those are gone, and it matters *why*: not because a
    threshold was lowered but because L7's threshold is now derived from the
    exchangeable null at the fab's own 3-sigma convention instead of being a
    per-chamber constant applied to a maximum over seven chambers. ADR-026
    measured the old constant failing 10 of 12 fault-free worlds.

    The mutation tests below are what stop this from being a vacuous green: a
    poisoned null must still fail L7, and it does.
    """
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


@pytest.mark.parametrize("criterion",
                         ["A1", "A3", "A5", "A6", "A7", "A8", "A11"])
def test_the_partly_testable_criteria_are_reported_as_partial(report,
                                                              criterion):
    """Not PASS, and not BLOCKED either. Each of these has a half this gate
    genuinely settles and a half that needs CI or a manual review — and
    saying PASS would make the matrix a worse instrument.

    A6 is here on its measured reading: the sweep runs, the difficulty axis
    exists, subtle stays inside benign variation, and moderate does not clear
    the declared level on any channel A6 names as evidence. A7 returned to
    this list in ADR-027, when L7's threshold stopped being a per-chamber
    constant applied to a maximum over chambers; it is PARTIAL rather than
    PASS because four checks are inapplicable to a null.
    """
    verdict = report.verdict(criterion)
    assert verdict.status == PARTIAL, f"{criterion}: {verdict.detail}"
    assert verdict.detail


def test_a7_reports_the_null_calibration_it_now_measures(report):
    """A7's L7 half is a population reading, and the evidence must show it.

    The per-world guard says no chamber is grossly out; the calibration says
    the population as a whole is correctly sized. Both belong in the evidence,
    because "L7 passed" without a rate is the kind of claim ADR-026 found
    hiding an 83% failure rate.
    """
    verdict = report.verdict("A7")
    assert verdict.status == PARTIAL
    calibration = [line for line in verdict.evidence
                   if "L7 null calibration" in line]
    assert calibration, verdict.evidence
    assert "expected" in calibration[0] and "resolves an inflation" in \
        calibration[0]


def test_a9_is_blocked_on_the_item_that_is_actually_unmet(report):
    """A9 is *statistical equivalence*, never a replay of the legacy numbers
    (ADR-010).

    Until ADR-027 it blocked on the 4-10 point cohort band — a number traced
    to the audited v1's direct label term and measured unreachable through the
    only channel that could carry it. The band is now reported as historical
    reference and the criterion blocks on what is genuinely unmet: the
    affected tool is not the worst etch tool on cohort yield, which is a
    ranking failure the between-tool benign spread explains.
    """
    verdict = report.verdict("A9")
    assert verdict.status == BLOCKED
    assert "worst etch tool" in verdict.detail
    assert "ranking failure" in verdict.detail
    assert any("wafer-map" in line for line in verdict.evidence)
    # The retired band is reported, not deleted.
    assert any("historical reference" in line for line in verdict.evidence)


def test_only_a9_is_blocked_and_it_is_a_measurement(report):
    """A7 left this list in ADR-027 and A9 did not.

    A7 is no longer blocked because its check stopped measuring an order
    statistic; A9 still is, because the yield channel genuinely cannot rank
    the affected tool and no document has yet decided whether demo continuity
    should keep a yield item at all. Pinning the list keeps the matrix from
    drifting green quietly — a criterion may only leave it by being earned.
    """
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


def test_the_null_clears_the_action_limit_at_every_seed(built):
    """The verdict ADR-027's derived limit produces, at all three seeds.

    Under the old constant this failed at seeds 101 and 2024 — and at 10 of
    12 fault-free worlds once enough were built (ADR-026 §2), because 2.5 is a
    per-chamber figure that the maximum of seven exchangeable chambers exceeds
    with probability 0.598. The limit is now the fab's own 3-sigma convention
    carried into the leave-one-out currency, which is 6.46 at seven chambers.

    This is a green that had to be earned twice over: the mutation test below
    shows the same check still fails on a poisoned null, and
    `test_reference.py` pins the derivation of the number.
    """
    for (scenario, seed), copies in sorted(built.items()):
        if scenario != "null_baseline":
            continue
        finding = l7_null_blindness(copies[0])
        assert finding.passed and not finding.skipped, (seed, finding.detail)
        assert "action limit" in finding.detail


def test_the_null_population_is_correctly_sized(built):
    """L7's other half: the rate over every fault-free world.

    A generator defect would put structure on many chambers a little rather
    than on one chamber a lot, and no per-world threshold would see it. This
    is the check that would, and it reports the rate it measured rather than
    only a verdict.
    """
    from fabeval.leakage import l7_null_calibration

    nulls = [copies[0] for (scenario, _seed), copies in built.items()
             if scenario == "null_baseline"]
    finding = l7_null_calibration(nulls)
    assert finding.passed and not finding.skipped, finding.detail
    assert "expected" in finding.detail
    assert "resolves an inflation" in finding.detail


def test_the_calibration_refuses_a_population_too_small_to_be_a_rate(built):
    """One null world is not a rate, and saying so beats reporting one."""
    from fabeval.leakage import l7_null_calibration

    finding = l7_null_calibration([primary(built, "null_baseline")])
    assert finding.skipped, finding.detail


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

    **The detection floor, measured and stated rather than implied** (ADR-027).
    Against the derived action limit this catches a 10% single-chamber shift
    (10.8 sigma) and a 30% one (19.2 sigma), and lets a 5% one through (4.7
    sigma). The old 2.5 constant "caught" a 2% shift — while flagging nine
    healthy worlds in ten, and while naming the *wrong* chamber at 2%. That is
    not sensitivity the correction gave away; it is a check that was firing on
    the benign structure rule F11 requires the null to contain.
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


def test_a9_would_notice_a_demo_whose_yield_story_held(built):
    """The A9 check is not hard-wired to fail.

    It reports BLOCKED today because the affected tool is not the worst etch
    tool on cohort yield, not because the function cannot report anything
    else. Feeding it a yield table where the affected chamber really does lose
    six points makes its tool the worst and moves the verdict — which is what
    makes today's BLOCKED a measurement rather than a stub.

    Never PASS: the manual wafer-map item is unrun and no arithmetic can run
    it. And the retired band is still *reported* in the evidence at both
    verdicts, so retiring it cannot be mistaken for deleting it.
    """
    demo = primary(built, DEMO_SCENARIO)
    verdict = check_a9(demo)
    assert verdict.status == BLOCKED
    assert any("historical reference" in line for line in verdict.evidence)

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
    assert any("historical reference" in line for line in moved.evidence)


def test_a9_still_reports_the_retired_band_and_never_enforces_it(built):
    """The band is retired as binding and preserved as a reference (ADR-027).

    Both halves are pinned: the number is still in the evidence with its
    range, and a deficit outside it no longer decides the verdict. A future
    edit that quietly deletes the number, or one that quietly starts gating on
    it again, breaks this.
    """
    from fabeval.acceptance import LEGACY_COHORT_BAND

    assert LEGACY_COHORT_BAND == (4.0, 10.0)
    verdict = check_a9(primary(built, DEMO_SCENARIO))
    band_lines = [line for line in verdict.evidence if "4.0-10.0" in line]
    assert band_lines, verdict.evidence
    assert "not enforced" in band_lines[0]
    # The measured deficit is far outside the band, and that is not why the
    # criterion blocks.
    assert "4-10" not in verdict.detail
    assert "ranking failure" in verdict.detail
