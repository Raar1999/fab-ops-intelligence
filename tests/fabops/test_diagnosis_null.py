"""
The null-validity criterion — the check the *simulator* can fail.

`DIAGNOSIS_CONTRACT.md` §5.1: the engine's abstention is calibrated inside the
dataset it is judging, by permuting the candidate label within its own
exchangeable stratum. That is exact only where the candidates really are
exchangeable, which rules F10/F11 make true for this world by construction and
which nothing makes true in general. So the assumption ships with a
measurement rather than with a promise:

    on at least thirty fault-free worlds, the realized rate of
    `p_familywise <= alpha` must not exceed `alpha` by more than binomial
    chance, at every declared level.

**What a suite-sized population can and cannot certify.** The engine's
realized level is measured at **0.065 on 230 fault-free worlds** against a
nominal 0.05 - about +1.0 binomial standard errors, i.e. calibrated to within
roughly 1.3x and *not* provably exact. No test that builds its own population
in a couple of minutes can sharpen that: separating 0.05 from 0.075 at 80%
power needs on the order of five hundred worlds. So this module does what the
evaluator already does for L7 - it asserts a **declared tolerance** rather
than exactness, with the exact binomial so a small sample cannot fail merely
for being small, and the sharp measurement lives in ADR-029 where it can be
quoted without being rebuilt on every commit.

The tolerance is a real guard rather than a formality: the first
implementation of this engine ran at four times nominal, and so did the
version whose anchor was read out of the fab's own aggregate. Both fail here.
"""
from __future__ import annotations

import math

import pytest

from fabops.diagnosis import diagnose
from fabops.diagnosis.decide import ALPHA

#: The levels the contract declares.
LEVELS = (0.01, 0.05, 0.10, 0.20)

#: How far above nominal the realized rate may sit before it is a finding.
#: Declared, and justified by what it has to catch: the two mis-calibrated
#: designs this engine went through both ran at four times nominal.
TOLERANCE = 2.0

#: How improbable the observed count must be, under the tolerated rate, before
#: the sample is treated as evidence rather than as a draw.
SIGNIFICANCE = 0.05


@pytest.fixture(scope="module")
def null_reports(null_population):
    return tuple(diagnose(record["db_path"]) for record in null_population)


def _upper_tail(count: int, n: int, p: float) -> float:
    """P(X >= count) for X ~ Binomial(n, p). Exact, stdlib only."""
    return sum(math.comb(n, k) * p ** k * (1.0 - p) ** (n - k)
               for k in range(count, n + 1))


def test_the_population_is_large_enough_to_be_a_rate(null_population):
    """A rate estimated from a handful of worlds is not a rate."""
    assert len(null_population) >= 30, len(null_population)


@pytest.mark.parametrize("alpha", LEVELS)
def test_the_permutation_null_stays_inside_the_declared_tolerance(
        null_reports, alpha):
    """The criterion, at the resolution the sample actually supports."""
    values = [r.abstention["p_familywise"] for r in null_reports]
    n = len(values)
    fired = sum(1 for p in values if p <= alpha)
    probability = _upper_tail(fired, n, min(TOLERANCE * alpha, 1.0))
    assert probability >= SIGNIFICANCE, (
        f"at alpha={alpha} the engine fired on {fired}/{n} fault-free worlds; "
        f"under the tolerated rate of {TOLERANCE * alpha:.3f} that has "
        f"probability {probability:.4f}, so this is evidence of a "
        f"mis-calibrated null rather than a draw")


def test_the_criterion_says_what_it_can_and_cannot_resolve(null_reports):
    """A calibration check that cannot state its own blindness is read as
    though it were sharp."""
    n = len(null_reports)
    tolerated = TOLERANCE * ALPHA
    smallest_caught = next(
        k for k in range(n + 1) if _upper_tail(k, n, tolerated) < SIGNIFICANCE)
    assert smallest_caught / n <= 4.0 * ALPHA, (
        f"{n} worlds only catch a realized rate of {smallest_caught / n:.3f} "
        f"at alpha={ALPHA}; that is too blind to be worth asserting")


def test_most_fault_free_worlds_abstain(null_reports):
    """The behavioural half. A correctly sized null is not the same claim as
    'the engine abstains', and the report is what a user reads."""
    abstained = sum(1 for r in null_reports if r.insufficient_evidence)
    assert abstained >= 0.85 * len(null_reports), (
        f"only {abstained}/{len(null_reports)} fault-free worlds abstained")


def _poison(database, chamber, families):
    """Shift one chamber's post-anchor readings on the named families."""
    import sqlite3

    connection = sqlite3.connect(str(database))
    changed = 0
    try:
        origin, horizon = connection.execute(
            "SELECT time_origin, horizon_days FROM dataset_meta").fetchone()
        after = (origin, int(horizon // 2), chamber)
        if "metrology" in families:
            # A metrology row indicts the chamber that ran the *measured*
            # step, so the mutation has to follow that attribution; selecting
            # every wafer that ever touched the chamber would smear the shift
            # over its peers and the peer-differencing would cancel it.
            changed += connection.execute(
                """UPDATE metrology SET value = value * 1.30
                   WHERE param_name LIKE 'cd_nm_%'
                     AND meas_time >= datetime(?, '+' || ? || ' days')
                     AND EXISTS (SELECT 1 FROM runs r
                                 WHERE r.wafer_id = metrology.wafer_id
                                   AND r.flow_step_id = metrology.flow_step_id
                                   AND r.chamber_id = ?)""", after).rowcount
        if "fdc" in families:
            changed += connection.execute(
                """UPDATE run_measurements SET value = value * 1.30
                   WHERE run_id IN (SELECT run_id FROM runs
                                    WHERE chamber_id = ?
                                      AND start_time >= datetime(?, '+' || ?
                                          || ' days'))""",
                (chamber, origin, int(horizon // 2))).rowcount
        if "alarms" in families:
            tool = connection.execute(
                "SELECT tool_id FROM chambers WHERE chamber_id = ?",
                (chamber,)).fetchone()[0]
            code, severity, message = connection.execute(
                "SELECT alarm_code, severity, message FROM alarms "
                "LIMIT 1").fetchone()
            start_id = connection.execute(
                "SELECT MAX(alarm_id) FROM alarms").fetchone()[0] + 1
            rows = [(start_id + i, tool, chamber,
                     f"{origin[:10]} 12:00:00", code, severity, message)
                    for i in range(0, 0)]
            # place the extra alarms across the post-anchor half
            rows = []
            for i in range(40):
                day = int(horizon // 2) + i
                stamp = connection.execute(
                    "SELECT datetime(?, '+' || ? || ' days')",
                    (origin, day)).fetchone()[0]
                rows.append((start_id + i, tool, chamber, stamp, code,
                             severity, message))
            connection.executemany(
                "INSERT INTO alarms (alarm_id, tool_id, chamber_id, "
                "alarm_time, alarm_code, severity, message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            changed += len(rows)
        connection.commit()
    finally:
        connection.close()
    return changed


def _first_etch_chamber(database):
    import sqlite3

    connection = sqlite3.connect(str(database))
    try:
        chamber_id, tool, chamber = connection.execute(
            "SELECT c.chamber_id, t.tool_name, c.chamber_name FROM chambers c "
            "JOIN tools t ON t.tool_id = c.tool_id WHERE t.tool_type = 'ETCH' "
            "ORDER BY c.chamber_id").fetchone()
        return chamber_id, f"{tool}/{chamber}"
    finally:
        connection.close()


def test_a_single_family_shift_moves_the_candidate_but_need_not_convict(
        null_population, tmp_path):
    """A shift confined to one family must be *visible* and need not convict.

    That is the architecture rather than a shortfall: a single channel was
    measured three times over not to be decisive in this world, and the engine
    is built on agreement across near-independent families.
    """
    import shutil

    record = null_population[0]
    database = tmp_path / "one-family.db"
    shutil.copy2(record["db_path"], database)
    chamber_id, label = _first_etch_chamber(database)

    def standing(report):
        match = next(c for c in report.candidates
                     if c.entity.kind == "chamber" and c.entity.name == label)
        return match, [e for e in match.evidence if e.family == "metrology"]

    before, _ = standing(diagnose(database))
    assert _poison(database, chamber_id, {"metrology"}) > 50
    after, after_metrology = standing(diagnose(database))

    assert after_metrology, "the poisoned family vanished from the evidence"
    assert min(e.rank for e in after_metrology) == 1, (
        "a chamber whose readings were shifted by 30% did not even lead the "
        "family it was shifted on; the evidence plane is not reading the data")
    assert after.p_value < before.p_value, (
        f"the candidate's own evidence did not strengthen: "
        f"{before.p_value} -> {after.p_value}")


def test_a_multi_family_shift_convicts_the_candidate_it_was_applied_to(
        null_population, tmp_path):
    """The non-inertness test, posed where the engine's evidence lives.

    Three of the five evidence families are shifted by 30% on one chamber. What
    must move is that chamber's **own** evidence: its permutation p-value has
    to collapse and it has to become the leading candidate. Measured across
    three fault-free worlds the p-value goes 0.394 -> 0.019, 0.922 -> 0.060 and
    0.939 -> 0.043.

    What is deliberately *not* asserted is that the fab-wide abstention flips.
    That bar is a maximum over roughly a hundred candidates in five strata, and
    the calibrated statistic this engine ships was chosen on null validity in
    full knowledge that it costs power - held-out detection is 1 in 25, which
    ADR-029 records rather than hides. A test that demanded the bar flip would
    be asserting a power this engine does not claim, and the first thing to
    give would be the calibration.
    """
    import shutil

    worlds = null_population[:3]
    collapsed = 0
    led = 0
    for index, record in enumerate(worlds):
        database = tmp_path / f"multi-{index}.db"
        shutil.copy2(record["db_path"], database)
        chamber_id, label = _first_etch_chamber(database)

        before = next(c for c in diagnose(database).candidates
                      if c.entity.kind == "chamber" and c.entity.name == label)
        assert _poison(database, chamber_id,
                       {"metrology", "fdc", "alarms"}) > 100

        report = diagnose(database)
        after = next(c for c in report.candidates
                     if c.entity.kind == "chamber" and c.entity.name == label)
        if after.p_value <= 0.10 and after.p_value < before.p_value / 5.0:
            collapsed += 1
        if report.top is not None and report.top.entity.name == label:
            led += 1

    assert collapsed == len(worlds), (
        f"the poisoned chamber's own evidence collapsed on only {collapsed} of "
        f"{len(worlds)} worlds; the engine is inert")
    assert led >= 2, (
        f"the poisoned chamber led on only {led} of {len(worlds)} worlds")
