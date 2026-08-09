"""
acceptance.py — A1–A11 of `PHASE_1_ACCEPTANCE.md`, as machine checks.

One function per criterion, each returning a `Verdict` of `PASS`, `PARTIAL`
or `BLOCKED` with the reason attached. Three statuses rather than two because
the honest answer for several criteria is neither: A2's Jaccard bound is
checkable and its cohort-delta half needs more seeds than a gate should
build; A9's checklist ends in a manual wafer-map review that no function can
perform. A criterion that is *partly* tested is reported as partly tested —
calling it PASS would make the matrix a worse instrument than no matrix.

Where the repository already implements a criterion, this calls it rather
than restating it: A4 runs `fabsim.selftest.check_observable`, the emitter's
own stage-7 invariants, and A10 runs the leakage suite's L9. One definition
per rule, one place to change it.

`fabeval` is the trusted join: these functions see both planes. That is what
lets A6 confirm a mechanism reached the channel it declared, and it is
exactly the privilege `fabops` does not have.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from fabeval.leakage import Finding, l9_code_plane_lint, run_leakage_suite
from fabeval.queries import (
    alarm_counts,
    chamber_edge_cd_deviation,
    chamber_edge_defect_share,
    chamber_yield_split,
    rank,
    wafer_yields,
    zscore,
)
from fabeval.truthschema import TruthValidationError, validate_truth

__all__ = [
    "PASS",
    "PARTIAL",
    "BLOCKED",
    "Verdict",
    "arc_ordering",
    "check_a1", "check_a2", "check_a3", "check_a4", "check_a5",
    "check_a6", "check_a7", "check_a8", "check_a9", "check_a10", "check_a11",
]

PASS = "PASS"
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Verdict:
    """One acceptance criterion's outcome, with its evidence."""

    criterion: str
    status: str
    detail: str
    evidence: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.criterion}  -  {self.status}: {self.detail}"


def _fail(criterion: str, detail: str, evidence: Sequence[str] = ()) -> Verdict:
    return Verdict(criterion, BLOCKED, detail, tuple(evidence))


# ------------------------------------------------------------------- A1


def check_a1(builds: Mapping[str, Sequence[Any]]) -> Verdict:
    """Reproducibility: the same five inputs, the same dataset.

    `builds` maps a label to two independently produced datasets of the same
    configuration. Checks 1–3 of A1 are testable here; check 4, the
    reference-image `fab.db` byte compare, is a CI-environment job by the
    criterion's own wording and is reported as outstanding rather than run.
    """
    evidence: list[str] = []
    for label, pair in builds.items():
        if len(pair) != 2:
            return _fail("A1", f"{label}: need two builds to compare")
        first, second = pair
        checks = {
            "build_fingerprint":
                first.identity.build_fingerprint
                == second.identity.build_fingerprint,
            "content_sha256":
                first.observable.content_sha256()
                == second.observable.content_sha256(),
            "sql dump bytes":
                (first.directory / "fab_database.sql").read_bytes()
                == (second.directory / "fab_database.sql").read_bytes(),
            "truth bytes":
                first.truth_path.read_bytes() == second.truth_path.read_bytes(),
            "manifest": first.manifest == second.manifest,
        }
        broken = sorted(name for name, ok in checks.items() if not ok)
        if broken:
            return Verdict("A1", BLOCKED,
                           f"{label} differs between builds: {broken}",
                           tuple(evidence))
        evidence.append(f"{label}: identical on all five artifacts")
    return Verdict(
        "A1", PARTIAL,
        "checks 1-3 green on every library scenario; check 4 (reference-image "
        "fab.db byte compare) is a CI-environment job and has not been run",
        tuple(evidence))


# ------------------------------------------------------------------- A2


def check_a2(seeds: Sequence[Any]) -> Verdict:
    """Diversity: three seeds of one scenario differ where they must.

    The criterion's own wording: affected-wafer sets differ pairwise with
    Jaccard < 0.9, realized cohort yield deltas differ, and truth's scenario
    semantics stay identical.
    """
    if len(seeds) < 3:
        return _fail("A2", f"needs three seeds, given {len(seeds)}")
    cohorts = [{entry["wafer_id"] for event in d.truth["events"]
                for entry in event["affected_wafers"]} for d in seeds]
    if not all(cohorts):
        return _fail("A2", "a seed produced no affected cohort")

    jaccards = []
    for i in range(len(cohorts)):
        for j in range(i + 1, len(cohorts)):
            union = cohorts[i] | cohorts[j]
            jaccards.append(len(cohorts[i] & cohorts[j]) / len(union))
    deltas = [d.truth["events"][0]["expected_impact"]["cohort_yield_delta_pts"]
              for d in seeds]
    semantics = {(e["mechanism"], e["target"]["tool"], e["target"]["chamber"],
                  e["severity"], e["profile"]["type"])
                 for d in seeds for e in d.truth["events"]}

    evidence = [
        f"pairwise Jaccard {[round(v, 3) for v in jaccards]} (limit 0.9)",
        f"cohort yield deltas {[None if v is None else round(v, 3) for v in deltas]}",
        f"{len(semantics)} distinct scenario semantics across seeds",
    ]
    if max(jaccards) >= 0.9:
        return Verdict("A2", BLOCKED,
                       f"affected-wafer sets too similar: max Jaccard "
                       f"{max(jaccards):.3f}", tuple(evidence))
    if len(semantics) != 1:
        return Verdict("A2", BLOCKED,
                       "the seeds disagree about what the scenario is",
                       tuple(evidence))
    present = [v for v in deltas if v is not None]
    if len(set(round(v, 6) for v in present)) != len(present):
        return Verdict("A2", BLOCKED, "two seeds produced the same cohort "
                                      "delta", tuple(evidence))
    return Verdict("A2", PASS,
                   "affected sets differ, deltas differ, semantics identical",
                   tuple(evidence))


# ------------------------------------------------------------------- A3


def check_a3(null: Any, faulted: Sequence[Any]) -> Verdict:
    """No-fault validity: the null is a real fab, not a clean one."""
    counts = null.observable.row_counts()
    yields = [y for _w, _p, y in wafer_yields(null.db_path)]
    unscheduled = sum(1 for row in null.observable.rows("maintenance")
                      if row[3] == "UNSCHEDULED")
    bins = {row[3] for row in null.observable.rows("die_bins")}

    problems: list[str] = []
    if null.truth["events"]:
        problems.append("a null scenario declared an event")
    if counts["alarms"] < 20:
        problems.append(f"only {counts['alarms']} alarms")
    if unscheduled < 10:
        problems.append(f"only {unscheduled} unscheduled repairs")
    if counts["defects"] < 5000:
        problems.append(f"only {counts['defects']} defects")
    if not yields or st.pstdev(yields) < 1.0:
        problems.append("yield does not vary")
    if yields and max(yields) >= 100.0:
        problems.append("a perfect wafer exists")
    if len(bins) < 5:
        problems.append(f"only {len(bins)} bin codes")

    # …and not artificially quiet relative to the fault scenarios, which is
    # the half a "the null looks fine" check would miss.
    for dataset in faulted:
        other = dataset.observable.row_counts()
        for table in ("alarms", "maintenance", "defects", "wafer_yield"):
            ratio = other[table] / max(1, counts[table])
            if not 0.6 < ratio < 1.7:
                problems.append(
                    f"{table}: {dataset.truth['scenario_name']} is "
                    f"{ratio:.2f}x the null")

    evidence = [
        f"{counts['alarms']} alarms, {unscheduled} unscheduled repairs, "
        f"{counts['defects']} defects",
        f"yield {st.mean(yields):.2f} +/- {st.pstdev(yields):.2f}, "
        f"max {max(yields):.2f}",
        f"bin codes: {sorted(bins)}",
    ]
    if problems:
        return Verdict("A3", BLOCKED, "; ".join(problems), tuple(evidence))
    return Verdict("A3", PARTIAL,
                   "the null is populated, varied and not quieter than the "
                   "fault scenarios; the criterion's 'full integrity suite' "
                   "half is A4 and its L7/L10 half is A7, both reported "
                   "separately", tuple(evidence))


# ------------------------------------------------------------------- A4


def check_a4(datasets: Sequence[Any]) -> Verdict:
    """Structural integrity: the emitter's own stage-7 invariants, re-run.

    Deliberately the same function the build runs (`fabsim.selftest`), not a
    second implementation: two definitions of one rule is how they drift.
    What this adds is that it runs over *every* library dataset from outside
    the build, so a criterion that was only ever checked by the thing being
    graded is now checked by the grader too.
    """
    from fabsim.selftest import SelfTestError, check_observable

    evidence: list[str] = []
    for dataset in datasets:
        name = dataset.truth["scenario_name"]
        try:
            check_observable(dataset.observable.tables, dataset.response.world)
        except SelfTestError as exc:
            return Verdict("A4", BLOCKED, f"{name}: {exc}", tuple(evidence))
        # Primary-key uniqueness is the database's own claim; assert it holds
        # in the emitted rows too, since the digest depends on it.
        from fabsim.emit.observable import SCHEMA_COLUMNS, SCHEMA_KEYS

        for table, keys in SCHEMA_KEYS.items():
            positions = [SCHEMA_COLUMNS[table].index(k) for k in keys]
            seen = {tuple(row[i] for i in positions)
                    for row in dataset.observable.rows(table)}
            if len(seen) != len(dataset.observable.rows(table)):
                return Verdict("A4", BLOCKED,
                               f"{name}: duplicate primary key in {table}",
                               tuple(evidence))
        evidence.append(f"{name}: sec 4.1-sec 4.4 and PK uniqueness hold")
    return Verdict("A4", PASS,
                   f"all four invariant families hold on {len(datasets)} "
                   "datasets", tuple(evidence))


# ------------------------------------------------------------------- A5


def arc_ordering(dataset: Any) -> tuple[bool, str]:
    """Onset → alarm → repair start → repair end, from observable timestamps.

    The ordering has to be *read out of the dataset*, not inferred from the
    configuration: an evaluation that trusted the config would confirm that
    the config says what the config says.
    """
    if not dataset.truth["events"]:
        return False, "no event"
    event = dataset.truth["events"][0]
    onset = event["onset"]
    chambers = set(event["target"]["chamber_ids"])

    # Which alarms were condition-driven and which window the escalation
    # produced is something only the hidden plane knows, and `fabeval` is
    # allowed to ask it. Their *timestamps* are then read out of the
    # observable tables, so what is asserted is that the dataset an analyst
    # receives really shows the ordering. Taking the first alarm at or after
    # onset from the observable plane alone — an earlier version of this
    # function — can pick a background false alarm and a pre-existing
    # breakdown and call the coincidence an arc.
    condition_ids = set(event["alarms_emitted"])
    if not condition_ids:
        return False, f"onset {onset}: no condition alarm followed"
    stamped = {row[0]: row[3] for row in dataset.observable.rows("alarms")}
    missing = sorted(condition_ids - set(stamped))
    if missing:
        return False, f"truth names alarm(s) {missing} the dataset lacks"
    first_alarm = min(stamped[alarm_id] for alarm_id in condition_ids)

    response = event["maintenance_response"]
    if response is None:
        return False, (f"onset {onset} -> alarm {first_alarm}; no repair "
                       "followed inside the horizon")
    windows = {row[0]: (row[4], row[5], row[2])
               for row in dataset.observable.rows("maintenance")}
    if response["maint_id"] not in windows:
        return False, (f"truth names maintenance {response['maint_id']} the "
                       "dataset lacks")
    start, end, chamber = windows[response["maint_id"]]
    if chamber not in chambers:
        return False, (f"maintenance {response['maint_id']} is on chamber "
                       f"{chamber}, not the affected one")
    ordered = onset <= first_alarm <= start < end
    return ordered, (f"onset {onset} -> condition alarm {first_alarm} -> "
                     f"repair {start}...{end}")


def check_a5(datasets: Sequence[Any], horizon_days: int = 84) -> Verdict:
    """Temporal validity: onset inside the horizon with baseline before it,
    and the observable arc in causal order where one exists."""
    evidence: list[str] = []
    problems: list[str] = []
    for dataset in datasets:
        name = dataset.truth["scenario_name"]
        for event in dataset.truth["events"]:
            (meta,) = dataset.observable.rows("dataset_meta")
            from datetime import datetime

            origin = datetime.fromisoformat(meta[3])
            onset = datetime.fromisoformat(event["onset"])
            elapsed = (onset - origin).total_seconds() / 86400.0
            if not 0 < elapsed < horizon_days:
                problems.append(f"{name}: onset at day {elapsed:.1f}")
            if elapsed / horizon_days < 0.30:
                problems.append(
                    f"{name}: only {100 * elapsed / horizon_days:.0f}% "
                    "baseline before onset (needs >=30%)")
            evidence.append(f"{name}: onset day {elapsed:.1f} of "
                            f"{horizon_days} ({100 * elapsed / horizon_days:.0f}% baseline)")
            ordered, detail = arc_ordering(dataset)
            evidence.append(f"{name}: {detail}")
            if not ordered and "no alarm" not in detail:
                problems.append(f"{name}: arc out of order  -  {detail}")
    if problems:
        return Verdict("A5", BLOCKED, "; ".join(problems), tuple(evidence))
    return Verdict(
        "A5", PARTIAL,
        "onset placement and the alarm->repair ordering hold on every faulted "
        "scenario; the metrology->defect->yield *series* ordering across a "
        "cohort is not asserted here  -  the effects are simultaneous in this "
        "model rather than sequenced, and the criterion's wording assumes a "
        "lag the physics does not have", tuple(evidence))


# ------------------------------------------------------------------- A6


def check_a6(datasets: Sequence[Any],
             sweep: Mapping[str, Any] | None = None,
             nulls: Sequence[Any] = (),
             label: str = "ETCH-02/B") -> Verdict:
    """Causal plausibility: does each declared mechanism reach its channel?

    Truth says which channels the mechanism *should* reach — `causal_chain`
    is derived from the world's declared sensitivities — and the reference
    queries say what the observable plane shows. This compares the two, which
    is the join `fabeval` exists for.

    `sweep` maps a severity to a dataset of one scenario at that severity, and
    `nulls` are fault-free worlds the same queries are run over. Supplied, they
    answer A6's second half — "at subtle severity the same queries sit near
    the natural-variation floor (difficulty axis exists)" — which cannot be
    answered from one dataset. Omitted, that half is reported as not run.
    """
    evidence: list[str] = []
    problems: list[str] = []
    from fabeval.fixtures import evaluate_expectation, expectation_for

    for dataset in datasets:
        name = dataset.truth["scenario_name"]
        expectation = expectation_for(name)
        if expectation is None:
            problems.append(f"{name}: no declared expectation")
            continue
        ok, detail = evaluate_expectation(dataset, expectation)
        evidence.append(f"{name}: {detail}")
        if not ok:
            problems.append(f"{name}: expectation not met")
    if problems:
        return Verdict("A6", BLOCKED, "; ".join(problems), tuple(evidence))

    if not sweep or not nulls:
        return Verdict("A6", PARTIAL,
                       "every scenario's declared evidence is recoverable by "
                       "the reference queries at its configured severity; the "
                       "severity sweep the criterion also asks for was not "
                       "supplied to this call", tuple(evidence))

    from fabeval.sweep import (
        MINIMUM_FLOOR_SEEDS,
        natural_variation_floor,
        severity_sweep,
        summarize,
    )

    if len(nulls) < MINIMUM_FLOOR_SEEDS:
        return Verdict("A6", PARTIAL,
                       f"the severity sweep ran but the natural-variation "
                       f"floor was read from {len(nulls)} null realization(s); "
                       f"at least {MINIMUM_FLOOR_SEEDS} are needed before "
                       "'above the floor' means anything", tuple(evidence))
    readings = severity_sweep(sweep, label)
    floor = natural_variation_floor(nulls)
    outcome = summarize(readings, floor)
    for reading in readings:
        evidence.append(
            f"sweep {reading.severity:8s} realized "
            f"{reading.realized_sigma:5.2f} sigma  " + "  ".join(
                f"{c}={s:+.2f}(rank {r}/{n})"
                for c, (s, r, n) in sorted(reading.standing.items())))
    evidence.append(f"natural-variation floor (worst chamber on a null): "
                    f"{outcome['floor']}")

    if not outcome["realized_rises_with_severity"]:
        return Verdict("A6", BLOCKED,
                       f"realized severity does not rise with the configured "
                       f"ladder: {outcome['realized']}", tuple(evidence))

    if not outcome["separated_at_moderate"]:
        # Measured, not assumed. A6 asks that the evidence be *recovered* at
        # moderate; ranking first is not recovery, because on a null world
        # some chamber always ranks first and does so at a comparable sigma.
        return Verdict(
            "A6", PARTIAL,
            "the difficulty axis exists - realized severity rises "
            f"{outcome['realized']} and channel(s) "
            f"{outcome['monotone_channels'] or 'none'} rise with it - but at "
            "moderate severity the planted chamber does not exceed the "
            "natural-variation floor on any single reference channel, so the "
            "criterion's 'recovers the intended evidence' half is not met by "
            "a single query. Whether it is recoverable by combining channels "
            "and controlling for each chamber's own baseline is the diagnosis "
            "engine's question, not a reference query's", tuple(evidence))

    return Verdict("A6", PASS,
                   f"evidence recovered at moderate on "
                   f"{outcome['separated_at_moderate']}, above the null "
                   f"floor; subtle sits at or below it: "
                   f"{outcome['subtle_at_or_below_floor']}", tuple(evidence))


# ------------------------------------------------------------------- A7


def check_a7(findings: Mapping[str, Sequence[Finding]],
             seed_finding: Finding | None = None) -> Verdict:
    """Leakage resistance: L1–L11 across the library."""
    evidence: list[str] = []
    failures: list[str] = []
    skipped: list[str] = []
    for name, results in findings.items():
        for finding in results:
            if finding.skipped:
                skipped.append(f"{name}/{finding.test}")
            elif not finding.passed:
                failures.append(f"{name}/{finding.test}: {finding.detail}")
            evidence.append(f"{name} {finding.test} [{finding.status}] "
                            f"{finding.detail}")
    if seed_finding is not None:
        evidence.append(f"L8 [{seed_finding.status}] {seed_finding.detail}")
        if not seed_finding.passed and not seed_finding.skipped:
            failures.append(f"L8: {seed_finding.detail}")
    if failures:
        return Verdict("A7", BLOCKED, "; ".join(failures), tuple(evidence))
    return Verdict("A7", PARTIAL,
                   f"L1-L11 green where applicable; {len(skipped)} check(s) "
                   "not applicable to their dataset (a null has no cohort, so "
                   "L3/L4/L6/L10 have nothing to separate)", tuple(evidence))


# ------------------------------------------------------------------- A8


def check_a8(datasets: Sequence[Any]) -> Verdict:
    """Entity realism: the world is used the way a fab is used."""
    evidence: list[str] = []
    problems: list[str] = []
    for dataset in datasets:
        name = dataset.truth["scenario_name"]
        world = dataset.response.world
        used = {row[4] for row in dataset.observable.rows("runs")}
        for tool in world.tools:
            chambers = {c.chamber_id for c in world.chambers_of(tool.tool_id)}
            if len(chambers) > 1 and len(chambers & used) < 2:
                problems.append(f"{name}: {tool.tool_name} used "
                                f"{len(chambers & used)} of its chambers")
        # Gate-etch and metal-etch assignments independent: a wafer's two etch
        # chambers must not be the same chamber every time, which was the
        # audited 100% collinearity.
        pairs: dict[int, list[int]] = {}
        step_of = {f.flow_step_id: f.step_id for f in world.flow_steps}
        etch = {s.step_id for s in world.process_steps
                if s.operation_type == "ETCH"}
        for row in dataset.observable.rows("runs"):
            if step_of[row[2]] in etch:
                pairs.setdefault(row[1], []).append(row[4])
        both = [v for v in pairs.values() if len(v) == 2]
        same = sum(1 for v in both if v[0] == v[1])
        share = same / len(both) if both else 0.0
        if share > 0.5:
            problems.append(f"{name}: {100 * share:.0f}% of wafers used one "
                            "chamber for both etch steps")
        evidence.append(f"{name}: {len(used)} chambers carried traffic; "
                        f"{100 * share:.0f}% same-chamber etch pairs")
    if problems:
        return Verdict("A8", BLOCKED, "; ".join(problems), tuple(evidence))
    return Verdict("A8", PARTIAL,
                   "multi-chamber tools use their chambers, etch assignments "
                   "are independent, and the routing-condition half is "
                   "checked by A6's confound expectations; the recipe and "
                   "benign-offset items are A4's and 3C's and are not "
                   "re-derived here", tuple(evidence))


# ------------------------------------------------------------------- A9


def check_a9(demo: Any) -> Verdict:
    """Demo continuity — the qualitative checklist, scored honestly.

    A9 is *statistical equivalence*, never a replay of the legacy numbers
    (ADR-010). The checklist has five items; four are machine-checkable and
    the fifth is a manual wafer-map review. A criterion with an unrun manual
    item is not PASS, whatever the other four say.
    """
    truth = demo.truth
    if not truth["events"]:
        return _fail("A9", "the demo dataset carries no event")
    event = truth["events"][0]
    label = f"{event['target']['tool']}/{event['target']['chamber']}"
    tool = event["target"]["tool"]

    # 1. the affected chamber's tool is worst of the three etch tools on
    #    cohort yield, and the deficit is 4–10 pts.
    per_chamber = chamber_yield_split(demo.db_path)
    by_tool: dict[str, list[float]] = {}
    for chamber_label, score in per_chamber.items():
        by_tool.setdefault(chamber_label.split("/")[0], []).append(score.value)
    tool_deficit = {name: st.mean(values) for name, values in by_tool.items()}
    worst_tool = min(tool_deficit, key=tool_deficit.get)
    deficit = -per_chamber[label].value if label in per_chamber else 0.0

    # 2. elevated edge-ring share on the affected chamber's wafers.
    shares = {k: v.value for k, v in
              chamber_edge_defect_share(demo.db_path).items()}
    edge_rank = rank(shares, label) if label in shares else None

    # 3. unscheduled maintenance on the affected tool inside the window.
    repaired = event["maintenance_response"] is not None

    evidence = [
        f"worst etch tool on cohort yield: {worst_tool} "
        f"({tool_deficit[worst_tool]:+.2f} pts); affected tool is {tool}",
        f"affected chamber deficit {deficit:+.2f} pts (checklist wants 4-10)",
        f"edge-ring share rank of {label}: {edge_rank}/{len(shares)}",
        f"unscheduled maintenance in the window: {repaired}",
        "wafer-map review: not run (manual item)",
    ]
    met = [worst_tool == tool, 4.0 <= deficit <= 10.0, edge_rank == 1,
           repaired]
    if not (4.0 <= deficit <= 10.0):
        return Verdict(
            "A9", BLOCKED,
            f"the cohort yield deficit is {deficit:+.2f} pts against the "
            "checklist's 4-10; ADR-021 records the two identified causes and "
            "the constant that would change it is one this criterion forbids "
            "moving. The manual wafer-map item is also unrun.", tuple(evidence))
    return Verdict("A9", PARTIAL,
                   f"{sum(met)}/4 machine-checkable items met; the wafer-map "
                   "review is manual and has not been run", tuple(evidence))


# ------------------------------------------------------------------- A10


def check_a10(datasets: Sequence[Any]) -> Verdict:
    """Benchmark separation: the boundary, proved mechanically."""
    evidence: list[str] = []
    problems: list[str] = []

    lint = l9_code_plane_lint()
    evidence.append(f"L9 code-plane lint [{lint.status}] {lint.detail}")
    if not lint.passed:
        problems.append(f"L9: {lint.detail}")

    for dataset in datasets:
        name = dataset.truth["scenario_name"]
        try:
            validate_truth(dataset.truth)
            evidence.append(f"{name}: truth valid against fabsim.truth/v1")
        except TruthValidationError as exc:
            problems.append(f"{name}: invalid truth at {exc.path}: {exc.reason}")

        emitted = sorted(p.relative_to(dataset.directory).as_posix()
                         for p in dataset.directory.rglob("*") if p.is_file())
        if emitted != ["fab.db", "fab_database.sql", "manifest.json",
                       "truth/truth.json"]:
            problems.append(f"{name}: unexpected artifacts {emitted}")
        manifest = str(dataset.manifest).lower()
        if name.lower() in manifest:
            problems.append(f"{name}: the manifest names the scenario")

    if problems:
        return Verdict("A10", BLOCKED, "; ".join(problems), tuple(evidence))
    return Verdict("A10", PASS,
                   "code-plane lint clean in both directions, every truth "
                   "file valid, dataset directories hold exactly the four "
                   "artifacts, no manifest names its scenario", tuple(evidence))


# ------------------------------------------------------------------- A11


def check_a11() -> Verdict:
    """Legacy untouched: the v1 demonstration is where it was.

    Structural rather than behavioural — running the 27 legacy tests is the
    test suite's job, not the evaluator's, and duplicating them here would
    give two answers to one question. What this checks is that the artifacts
    they run against still exist and still speak schema v1.
    """
    import sqlite3

    repository = Path(__file__).resolve().parents[2]
    problems: list[str] = []
    evidence: list[str] = []

    legacy_db = repository / "data" / "fab.db"
    if not legacy_db.exists():
        problems.append("data/fab.db is missing")
    else:
        connection = sqlite3.connect(str(legacy_db))
        try:
            tables = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        if not {"run_history", "yield_data"} <= tables:
            problems.append("data/fab.db is no longer schema v1")
        if {"runs", "die_bins"} & tables:
            problems.append("schema v2 tables appeared in the legacy database")
        evidence.append(f"legacy database: {len(tables)} v1 tables intact")

    for relative in ("data/generate_fab_db.py", "data/fab_database.sql",
                     "sql", "app", "notebooks"):
        if not (repository / relative).exists():
            problems.append(f"{relative} is missing")
    evidence.append("legacy generator, SQL, dashboard and notebooks present")

    # No emitted dataset may sit inside the legacy data directory.
    scenarios = repository / "data" / "scenarios"
    if scenarios.exists() and any(scenarios.iterdir()):
        evidence.append("data/scenarios/ is populated (expected once a build "
                        "has been run outside the test suite)")

    if problems:
        return Verdict("A11", BLOCKED, "; ".join(problems), tuple(evidence))
    return Verdict("A11", PARTIAL,
                   "the legacy artifacts are present and still schema v1; the "
                   "27-test behavioural half is asserted by the test suite "
                   "rather than by this evaluator", tuple(evidence))
