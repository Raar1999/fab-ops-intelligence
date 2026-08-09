"""
fabeval — the evaluation plane: the one place the two datasets may be joined.

`ADR-013` splits every FabSim dataset into an observable plane and a hidden
one and gives each actor a different privilege::

    fabsim   writes both
    fabops   reads the observable plane, and only ever a path to `fab.db`
    fabeval  reads both, on `dataset_id`, and writes neither

That third row is this package. It exists so the simulator can be *graded* —
"did the generated world satisfy the Phase 1 acceptance criteria?" — by
something that is not the simulator and is not the analytical engine either.
Scoring needs the answer key; the diagnostic plane must never have it; so the
scorer is a third party.

    truthschema   `fabsim.truth/v1`, validated — the A10 carry-forward
    queries       the reference analytical queries, observable plane ONLY
    fixtures      what each scenario is expected to show (L11's table)
    leakage       L1–L11 of `ANTI_LEAKAGE_DESIGN.md` §3
    acceptance    A1–A11 of `PHASE_1_ACCEPTANCE.md`, as machine checks
    matrix        the five-scenario benchmark and its report

**Why `src/fabeval/` and not `eval/`.** The design documents name the
directory `eval/`, and the *role* is exactly what they describe. The location
differs because this repository settled on a src-layout installed package —
`src/fabops`, `src/fabsim` — after the audit's P0 defect was precisely a
non-installable source folder, and a root-level `eval/` would need the
`sys.path` manipulation that fix removed. `eval` is also a builtin name, so a
top-level package spelled that way is a trap. ADR-024 records the decision.

**What this package must never become.** It is not a diagnosis engine. The
reference queries compute engineering quantities and rank chambers by them;
they do not weigh evidence, combine channels or decide anything. A scorer that
grew an opinion could no longer grade the thing that has one — which is the
whole reason it is built before the diagnostic engine rather than after.

Stdlib only, like `fabsim`. Computing a mean and solving four normal equations
does not need a dependency, and staying free of one keeps the grader as
portable as the thing it grades.
"""
from __future__ import annotations

from fabeval.acceptance import BLOCKED, PARTIAL, PASS, Verdict
from fabeval.leakage import Finding, run_leakage_suite
from fabeval.matrix import (
    A2_SEEDS,
    DEFAULT_SEED,
    LIBRARY,
    BenchmarkReport,
    MatrixRow,
    build_library,
    evaluate,
    render,
)
from fabeval.truthschema import (
    TRUTH_SCHEMA,
    TruthValidationError,
    validate_truth,
    validate_truth_file,
)

__all__ = [
    "A2_SEEDS",
    "BLOCKED",
    "DEFAULT_SEED",
    "LIBRARY",
    "PARTIAL",
    "PASS",
    "TRUTH_SCHEMA",
    "BenchmarkReport",
    "Finding",
    "MatrixRow",
    "TruthValidationError",
    "Verdict",
    "build_library",
    "evaluate",
    "render",
    "run_leakage_suite",
    "validate_truth",
    "validate_truth_file",
]

#: Versioned like every other artifact this project produces: a change to what
#: a criterion means is a visible bump, not a silent reinterpretation.
__version__ = "0.1.0"
