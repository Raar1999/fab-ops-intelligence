"""
matrix.py — the five-scenario benchmark matrix, and the acceptance report.

One function builds the library, one scores it, one renders the result. The
matrix records the identity of every dataset it evaluated, so a report can be
traced back to the exact five inputs that produced the datasets it grades —
which is the point of `build_fingerprint` existing at all.

**The report may contain truth-derived scoring**, because `eval` is the
trusted boundary and scoring is what it is for. What it must not do is put
truth *into* a dataset, and it does not: nothing here writes to a dataset
directory, and the report is a returned object the caller decides what to do
with.

Seed strategy follows the criteria rather than convenience. Every scenario is
evaluated at seed 42, which is the library's declared default and the seed
`scenarios/README.md` publishes. A2 additionally needs three seeds of one
scenario — the criterion names `chamber_edge_uniformity` — so that one is
built at 42, 101 and 2024. A1 needs a second build of one configuration to
compare against, which is the cheapest of the three and is done on the null.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabeval.acceptance import (
    BLOCKED,
    PARTIAL,
    PASS,
    Verdict,
    check_a1,
    check_a2,
    check_a3,
    check_a4,
    check_a5,
    check_a6,
    check_a7,
    check_a8,
    check_a9,
    check_a10,
    check_a11,
)
from fabeval.fixtures import expectation_for
from fabeval.leakage import Finding, l8_seed_sensitivity, run_leakage_suite
from fabeval.truthschema import TruthValidationError, validate_truth

__all__ = [
    "A2_SEEDS",
    "DEFAULT_SEED",
    "DEMO_SCENARIO",
    "LIBRARY",
    "BenchmarkReport",
    "MatrixRow",
    "build_library",
    "build_sweep",
    "evaluate",
    "render",
]

#: The Phase 1 library, in the order `SCENARIO_SPECIFICATION.md` §4 lists it.
LIBRARY = ("null_baseline", "chamber_edge_uniformity", "parameter_drift",
           "confounded_chamber_vs_product", "fault_repair_recovery")

#: The library's declared default, and the seed the maintainers' index
#: publishes for each dataset id.
DEFAULT_SEED = 42

#: A2 asks for three seeds of the equipment-fault scenario by name.
A2_SEEDS = (42, 101, 2024)

#: A6's severity sweep: the same scenario at each rung of the ladder. Built
#: by `build_sweep` and passed to `evaluate(..., sweep=...)`, separately from
#: the library, because it is three more full datasets and the criteria that
#: do not need it should not pay for them.
SWEEP_SEVERITIES = ("subtle", "moderate", "obvious")

#: The ADR-010 continuity anchor A9 is assessed on.
DEMO_SCENARIO = "chamber_edge_uniformity"

SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"


@dataclass(frozen=True)
class MatrixRow:
    """One evaluated dataset: what it is, and how it scored.

    Every identity field the reproducibility contract names is carried, so a
    row is a complete provenance statement rather than a label.
    """

    scenario: str
    seed: int
    dataset_id: str
    scenario_id: str
    config_sha256: str
    world_sha256: str
    fabsim_version: str
    schema_version: str
    build_fingerprint: str
    content_sha256: str
    truth_valid: bool
    truth_error: str | None
    row_counts: Mapping[str, int]
    leakage: tuple[Finding, ...] = ()

    @property
    def leakage_summary(self) -> str:
        failed = [f.test for f in self.leakage if not f.passed and not f.skipped]
        skipped = [f.test for f in self.leakage if f.skipped]
        if failed:
            return f"FAIL {failed}"
        return f"pass ({len(skipped)} n/a)" if skipped else "pass"


@dataclass(frozen=True)
class BenchmarkReport:
    """The matrix and the A1–A11 verdicts it supports."""

    rows: tuple[MatrixRow, ...]
    verdicts: tuple[Verdict, ...]

    def verdict(self, criterion: str) -> Verdict:
        return next(v for v in self.verdicts if v.criterion == criterion)

    @property
    def blocked(self) -> tuple[str, ...]:
        return tuple(v.criterion for v in self.verdicts if v.status == BLOCKED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matrix": [
                {k: v for k, v in asdict(row).items() if k != "leakage"}
                | {"leakage": [{"test": f.test, "status": f.status,
                                "detail": f.detail} for f in row.leakage]}
                for row in self.rows],
            "acceptance": [
                {"criterion": v.criterion, "status": v.status,
                 "detail": v.detail, "evidence": list(v.evidence)}
                for v in self.verdicts],
        }


def _row(dataset: Any, scenario: str, seed: int) -> MatrixRow:
    identity = dataset.identity
    error: str | None = None
    try:
        validate_truth(dataset.truth)
        valid = True
    except TruthValidationError as exc:
        valid, error = False, f"{exc.path}: {exc.reason}"
    return MatrixRow(
        scenario=scenario, seed=seed, dataset_id=identity.dataset_id,
        scenario_id=identity.scenario_id,
        config_sha256=identity.config_sha256,
        world_sha256=identity.world_sha256,
        fabsim_version=identity.fabsim_version,
        schema_version=identity.schema_version,
        build_fingerprint=identity.build_fingerprint,
        content_sha256=dataset.observable.content_sha256(),
        truth_valid=valid, truth_error=error,
        row_counts=dataset.observable.row_counts(),
        leakage=tuple(run_leakage_suite(dataset,
                                        expectation_for(scenario))))


def build_sweep(root: Path, *, world: Any = None,
                scenario: str = DEMO_SCENARIO) -> dict[str, Any]:
    """One scenario at each severity, for A6. Keyed by severity name."""
    from fabsim.emit import build_dataset
    from fabsim.scenario import from_mapping, load_scenario
    from fabsim.world import load_world

    resolved = world if world is not None else load_world("baseline_fab_v1")
    base = json.loads(
        load_scenario(SCENARIO_ROOT / f"{scenario}.json").canonical_json)
    built: dict[str, Any] = {}
    for severity in SWEEP_SEVERITIES:
        raw = dict(base)
        raw["events"] = [dict(base["events"][0], severity=severity)]
        built[severity] = build_dataset(
            from_mapping(raw), world=resolved,
            root=root / f"sweep-{severity}", created_at="benchmark")
    return built


def build_library(root: Path, *, world: Any = None,
                  seeds: Mapping[str, Sequence[int]] | None = None,
                  duplicate: str | None = "null_baseline"
                  ) -> dict[tuple[str, int], list[Any]]:
    """Build the datasets the matrix needs, keyed by (scenario, seed).

    A key maps to a *list* because A1 needs two independent builds of one
    configuration to compare; every other key holds one.
    """
    from fabsim.emit import build_dataset
    from fabsim.scenario import load_scenario
    from fabsim.world import load_world

    resolved = world if world is not None else load_world("baseline_fab_v1")
    plan: dict[str, Sequence[int]] = {name: (DEFAULT_SEED,)
                                      for name in LIBRARY}
    plan[DEMO_SCENARIO] = A2_SEEDS
    # The null is built at the same seeds, because A6's natural-variation
    # floor is "the worst chamber on a world with nothing wrong" and one
    # null world is one draw of that. Measured on the baseline: the worst
    # chamber's edge-CD standing is 2.31 sigma at seed 42 but 2.84 at seed
    # 2024, and a floor taken from the lucky seed would let a moderate fault
    # (2.65) look separated when it is not.
    plan["null_baseline"] = A2_SEEDS
    if seeds:
        plan.update(seeds)

    built: dict[tuple[str, int], list[Any]] = {}
    for scenario, scenario_seeds in plan.items():
        config = load_scenario(SCENARIO_ROOT / f"{scenario}.json")
        for seed in scenario_seeds:
            copies = 2 if (scenario == duplicate
                           and seed == DEFAULT_SEED) else 1
            built[(scenario, seed)] = [
                build_dataset(config, seed, world=resolved,
                              root=root / f"{scenario}-{seed}-{index}",
                              created_at="benchmark")
                for index in range(copies)]
    return built


def evaluate(built: Mapping[tuple[str, int], Sequence[Any]],
             sweep: Mapping[str, Any] | None = None) -> BenchmarkReport:
    """Score a built library against A1–A11. Reads both planes.

    `sweep` is A6's severity ladder (see `build_sweep`). Supplied, A6 also
    answers its "difficulty axis" half against a floor measured on the null
    worlds already in `built`; omitted, A6 reports that half as not run.
    """
    primary = {scenario: copies[0]
               for (scenario, seed), copies in built.items()
               if seed == DEFAULT_SEED}
    rows = tuple(
        _row(copies[0], scenario, seed)
        for (scenario, seed), copies in sorted(built.items())
    )

    datasets = [copies[0] for copies in built.values()]
    library = [primary[name] for name in LIBRARY if name in primary]
    faulted = [d for d in library if d.truth["events"]]
    null = next((d for d in library if not d.truth["events"]), None)
    a2_seeds = [built[(DEMO_SCENARIO, seed)][0] for seed in A2_SEEDS
                if (DEMO_SCENARIO, seed) in built]

    duplicates = {f"{scenario}@{seed}": copies
                  for (scenario, seed), copies in built.items()
                  if len(copies) == 2}

    # Every row, not just seed 42. The suite already runs on every dataset the
    # matrix builds, and scoring only the default seed let A7 report "L1-L11
    # green" while the report's own rows carried L7 failures at other seeds —
    # the same one-draw mistake A6's floor was fixed for. Keyed by scenario
    # *and* seed because the scenario alone collides across seeds.
    findings = {f"{row.scenario}@{row.seed}": row.leakage for row in rows}

    verdicts = [
        check_a1(duplicates) if duplicates else Verdict(
            "A1", BLOCKED, "no configuration was built twice"),
        check_a2(a2_seeds),
        check_a3(null, faulted) if null else Verdict(
            "A3", BLOCKED, "no null scenario in the matrix"),
        check_a4(library),
        check_a5(faulted),
        check_a6(library, sweep=sweep,
                 nulls=[copies[0] for (scenario, _seed), copies
                        in built.items() if scenario == "null_baseline"]),
        check_a7(findings, l8_seed_sensitivity(a2_seeds)),
        check_a8(library),
        check_a9(primary[DEMO_SCENARIO]) if DEMO_SCENARIO in primary
        else Verdict("A9", BLOCKED, "the demo scenario was not built"),
        check_a10(library),
        check_a11(),
    ]
    return BenchmarkReport(rows=rows, verdicts=tuple(verdicts))


def render(report: BenchmarkReport) -> str:
    """A plain-text acceptance report. No colour, no width assumptions."""
    lines = ["FabSim Phase 1  -  benchmark matrix", ""]
    header = (f"{'scenario':32s} {'seed':>5s} {'dataset_id':22s} "
              f"{'truth':6s} {'leakage':16s} content_sha256")
    lines += [header, "-" * len(header)]
    for row in report.rows:
        lines.append(
            f"{row.scenario:32s} {row.seed:5d} {row.dataset_id:22s} "
            f"{'ok' if row.truth_valid else 'BAD':6s} "
            f"{row.leakage_summary:16s} {row.content_sha256[:16]}")

    lines += ["", "Acceptance criteria", "-" * 19]
    for verdict in report.verdicts:
        lines.append(f"{verdict.criterion:4s} {verdict.status:8s} "
                     f"{verdict.detail}")
    blocked = report.blocked
    lines += ["", f"{len(blocked)} blocked: {', '.join(blocked) or 'none'}"]
    return "\n".join(lines)
