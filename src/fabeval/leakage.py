"""
leakage.py — the L1–L11 suite of `ANTI_LEAKAGE_DESIGN.md` §3.

The suite is "the boundary's auditor" and reads **both planes deliberately**.
That is the one privilege `fabeval` has and `fabops` does not, and it is what
makes these checks possible at all: L5 compares an observable class against a
hidden origin, L3 needs the affected cohort, L8 needs the realized onset. A
version of this suite that stayed observable-only could not test the thing it
exists to test.

Each check returns a `Finding` rather than raising, because a benchmark that
stopped at the first failure would report one problem per run. The matrix
collects them and the report shows all of them.

Where the repository already has a check, this defers to it rather than
minting a competing definition: L9's code-plane lint and L2's manifest scan
are the ones the emission gate wrote, invoked here so there is one definition
of each rule and one place to change it.
"""
from __future__ import annotations

import ast
import json
import math
import re
import sqlite3
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabeval.queries import (
    EDGE_RADIUS_FRACTION,
    chamber_edge_defect_share,
    chamber_yield_split,
    wafer_yields,
    zscore,
)

__all__ = ["Finding", "LEAKAGE_TESTS", "run_leakage_suite"]

#: Tokens L1 forbids anywhere in the observable schema or its vocabularies.
_FORBIDDEN_TOKENS = ("fault", "truth", "scenario", "bad", "marginal",
                     "suspect", "inject", "ground")

#: Column names that contain a forbidden token but mean something else. Two
#: unrelated things are spelled `origin` in this project and only one is
#: hidden; `time_origin` is the clock's, which §2.1 requires. Listing the
#: exception keeps the scan able to catch what it exists for.
_TOKEN_EXCEPTIONS = {("dataset_meta", "time_origin")}


@dataclass(frozen=True)
class Finding:
    """One leakage check's verdict."""

    test: str
    passed: bool
    detail: str
    #: `True` when the check could not run here — no affected cohort on a
    #: null, no `eval/` expectation yet. Not a pass and not a failure.
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"


def _rows(db_path: Path, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(sql, tuple(params)).fetchall()
    finally:
        connection.close()


def _affected_wafers(truth: Mapping[str, Any]) -> set[int]:
    return {entry["wafer_id"] for event in truth["events"]
            for entry in event["affected_wafers"]}


# ---------------------------------------------------------------- the checks


def l1_schema_token_lint(dataset: Any) -> Finding:
    """No forbidden token in a table name, a column name or a vocabulary."""
    hits: list[str] = []
    tables = [r[0] for r in _rows(dataset.db_path,
                                  "SELECT name FROM sqlite_master "
                                  "WHERE type='table'")]
    for table in tables:
        for token in _FORBIDDEN_TOKENS:
            if token in table.lower():
                hits.append(f"table {table}")
        for column in [r[1] for r in _rows(dataset.db_path,
                                           f"PRAGMA table_info({table})")]:
            if (table, column) in _TOKEN_EXCEPTIONS:
                continue
            for token in _FORBIDDEN_TOKENS:
                if token in column.lower():
                    hits.append(f"{table}.{column}")
    # …and the categorical vocabularies, which are values rather than names.
    for table, column in (("alarms", "alarm_code"), ("alarms", "severity"),
                          ("maintenance", "maint_type"),
                          ("maintenance", "action_code"),
                          ("defects", "classified_type"),
                          ("die_bins", "bin_code"),
                          ("tool_states", "state")):
        for (value,) in _rows(dataset.db_path,
                              f"SELECT DISTINCT {column} FROM {table}"):
            for token in _FORBIDDEN_TOKENS:
                if token in str(value).lower():
                    hits.append(f"{table}.{column} = {value!r}")
    return Finding("L1 schema token lint", not hits,
                   "clean" if not hits else f"hits: {sorted(set(hits))}")


def l2_plane_separation(dataset: Any) -> Finding:
    """No truth in the observable artifacts, and no extra tables in `fab.db`."""
    from fabsim.emit.observable import SCHEMA_TABLES

    problems: list[str] = []
    tables = {r[0] for r in _rows(dataset.db_path,
                                  "SELECT name FROM sqlite_master "
                                  "WHERE type='table'")}
    extra = sorted(tables - set(SCHEMA_TABLES))
    if extra:
        problems.append(f"extra tables {extra}")

    manifest = json.dumps({k: v for k, v in dataset.manifest.items()
                           if k != "row_counts"}).lower()
    for token in (dataset.truth["scenario_name"].lower(),
                  *(e["mechanism"] for e in dataset.truth["events"])):
        if token and token in manifest:
            problems.append(f"manifest names {token!r}")

    blob = dataset.db_path.read_bytes()
    for token in (b"truth.json", dataset.truth["scenario_name"].encode()):
        if token and token in blob:
            problems.append(f"fab.db contains {token!r}")
    return Finding("L2 plane separation", not problems,
                   "clean" if not problems else "; ".join(problems))


def l3_mediation(dataset: Any) -> Finding:
    """The T1 killer: is the cohort's yield gap explained by observables?

    Fit `yield ~ observables` on the **unaffected** wafers only, predict the
    affected cohort, and call the unexplained mean residual the direct-effect
    estimate. The audited v1 had an 8-point direct effect written straight
    into the yield formula; the criterion is ≤ 2 points and ≤ 40% of the raw
    gap.

    Ordinary least squares by normal equations, solved with Gaussian
    elimination — four predictors and a few hundred rows do not need a linear
    algebra dependency, and `fabeval` staying stdlib keeps it as portable as
    the simulator it grades.
    """
    affected = _affected_wafers(dataset.truth)
    if not affected:
        return Finding("L3 mediation", True,
                       "no affected cohort in a null scenario", skipped=True)

    features = _wafer_features(dataset.db_path)
    rows = [(wafer, product, value) for wafer, product, value
            in wafer_yields(dataset.db_path) if wafer in features]
    if len(rows) < 50:
        return Finding("L3 mediation", True, "too few wafers", skipped=True)

    products = sorted({p for _w, p, _y in rows})
    def design(wafer: int, product: str) -> list[float]:
        defects, edge_share, cd_dev = features[wafer]
        # Product enters as one-hot minus a reference level, so a product's
        # own yield target cannot be mistaken for a cohort effect.
        return ([1.0, defects, edge_share, cd_dev]
                + [1.0 if product == name else 0.0 for name in products[1:]])

    train = [(design(w, p), y) for w, p, y in rows if w not in affected]
    test = [(design(w, p), y) for w, p, y in rows if w in affected]
    if len(train) < 30 or len(test) < 5:
        return Finding("L3 mediation", True, "cohort too small", skipped=True)

    beta = _least_squares(train)
    if beta is None:
        return Finding("L3 mediation", True, "singular design", skipped=True)

    residual = st.mean(y - sum(b * x for b, x in zip(beta, row))
                       for row, y in test)
    raw_gap = (st.mean(y for _r, y in test)
               - st.mean(y for _r, y in train))
    limit = 2.0
    share = abs(residual) / abs(raw_gap) if raw_gap else 0.0
    ok = abs(residual) <= limit
    return Finding(
        "L3 mediation", ok,
        f"unexplained residual {residual:+.3f} pts (limit +/-{limit}), "
        f"raw cohort gap {raw_gap:+.3f} pts, share {share:.2f}")


def _wafer_features(db_path: Path) -> dict[int, tuple[float, float, float]]:
    """Per-wafer observables L3 regresses yield on."""
    counts: dict[int, int] = defaultdict(int)
    edge: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for wafer, x_mm, y_mm, size in _rows(db_path, """
            SELECT d.wafer_id, d.x_mm, d.y_mm, p.wafer_size_mm
            FROM defects d
            JOIN wafers w ON w.wafer_id = d.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN products p ON p.product_id = l.product_id"""):
        counts[wafer] += 1
        bucket = edge[wafer]
        bucket[1] += 1
        if math.hypot(x_mm, y_mm) >= EDGE_RADIUS_FRACTION * (size / 2.0):
            bucket[0] += 1

    deviation: dict[int, list[float]] = defaultdict(list)
    for wafer, value, target in _rows(db_path, """
            SELECT m.wafer_id, m.value, rc.metric_target
            FROM metrology m
            JOIN flow_steps f ON f.flow_step_id = m.flow_step_id
            JOIN wafers w ON w.wafer_id = m.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN recipes rc ON rc.step_id = f.step_id
                           AND rc.product_id = l.product_id
            WHERE m.param_name LIKE 'cd_nm_%' AND m.param_name != 'cd_nm_sigma'"""):
        if target:
            deviation[wafer].append(abs(value - target) / target)

    out: dict[int, tuple[float, float, float]] = {}
    for wafer in counts:
        if wafer not in deviation:
            continue
        outer, total = edge[wafer]
        out[wafer] = (float(counts[wafer]), outer / total if total else 0.0,
                      st.mean(deviation[wafer]))
    return out


def _least_squares(samples: Sequence[tuple[Sequence[float], float]]
                   ) -> list[float] | None:
    """OLS via normal equations and Gaussian elimination with partial pivot."""
    width = len(samples[0][0])
    xtx = [[0.0] * width for _ in range(width)]
    xty = [0.0] * width
    for row, target in samples:
        for i in range(width):
            xty[i] += row[i] * target
            for j in range(width):
                xtx[i][j] += row[i] * row[j]

    matrix = [xtx[i] + [xty[i]] for i in range(width)]
    for column in range(width):
        pivot = max(range(column, width), key=lambda r: abs(matrix[r][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        scale = matrix[column][column]
        matrix[column] = [v / scale for v in matrix[column]]
        for row_index in range(width):
            if row_index == column:
                continue
            factor = matrix[row_index][column]
            matrix[row_index] = [v - factor * p for v, p
                                 in zip(matrix[row_index], matrix[column])]
    return [matrix[i][width] for i in range(width)]


def l4_perfect_separation(dataset: Any) -> Finding:
    """No categorical value with real support occurring only in the cohort."""
    affected = _affected_wafers(dataset.truth)
    if not affected:
        return Finding("L4 perfect separation", True, "no cohort",
                       skipped=True)
    problems: list[str] = []
    for table, column in (("defects", "classified_type"),
                          ("defects", "layer"),
                          ("die_bins", "bin_code")):
        rows = _rows(dataset.db_path,
                     f"SELECT wafer_id, {column} FROM {table}")
        support: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for wafer, value in rows:
            support[str(value)][0 if wafer in affected else 1] += 1
        for value, (inside, outside) in support.items():
            if inside >= 5 and outside == 0:
                problems.append(f"{table}.{column}={value!r}")
    return Finding("L4 perfect separation", not problems,
                   "no separating value" if not problems
                   else f"separating: {problems}")


def l5_classifier_honesty(dataset: Any, tolerance: float = 0.08) -> Finding:
    """The observable class is a noisy read of the hidden origin.

    Measured against the world's **own declared confusion matrix**, not
    against a number chosen here. Three properties, and the third is the one
    that matters:

    * every class arises from more than one origin, so a label cannot be read
      back into a cause;
    * every origin reaches more than one class, so no origin is a label;
    * the realized per-origin class distribution matches the declared row
      within sampling tolerance — the classifier is doing what the world says
      it does, and a classifier that quietly became deterministic would show
      up here as a row that no longer matches.

    An earlier version of this check compared the origin's *name* against the
    class's and called the difference "disagreement". That was meaningless:
    origins and classes are different vocabularies (`edge_ring` is called
    PATTERN 62% of the time and there is no EDGE_RING class), so the metric
    read 0.96 on a perfectly honest classifier. The design's "5-15%" is a
    *confusion* band, and confusion is only defined against the declared row.
    """
    origins = {o.defect_id: o.origin for o in dataset.defects.origins}
    classes = {d.defect_id: d.classified_type for d in dataset.defects.defects}
    if not origins:
        return Finding("L5 classifier honesty", True, "no defects",
                       skipped=True)

    declared = dataset.response.world.observation.classifier
    realized: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    reverse: dict[str, set[str]] = defaultdict(set)
    for defect_id, origin in origins.items():
        label = classes[defect_id]
        realized[origin][label] += 1
        reverse[label].add(origin)

    problems: list[str] = []
    worst = 0.0
    for origin, counts in sorted(realized.items()):
        total = sum(counts.values())
        if total < 50:
            continue
        row = dict(declared.row(origin))
        if len(counts) < 2:
            problems.append(f"{origin} reaches only {sorted(counts)}")
        for label, probability in row.items():
            observed = counts.get(label, 0) / total
            worst = max(worst, abs(observed - probability))
            if abs(observed - probability) > tolerance:
                problems.append(
                    f"{origin}->{label}: realized {observed:.3f} vs declared "
                    f"{probability:.3f}")
    single = sorted(label for label, sources in reverse.items()
                    if len(sources) < 2)
    if single:
        problems.append(f"class(es) {single} arise from one origin only")

    return Finding(
        "L5 classifier honesty", not problems,
        (f"realized confusion matches the declared matrix (worst cell "
         f"{worst:.3f}, tolerance {tolerance}); every class arises from "
         f">1 origin") if not problems else "; ".join(problems))


def l6_signature_overlap(dataset: Any) -> Finding:
    """Affected and unaffected per-wafer edge shares overlap, not partition."""
    affected = _affected_wafers(dataset.truth)
    if not affected:
        return Finding("L6 signature overlap", True, "no cohort", skipped=True)
    per_wafer: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for wafer, x_mm, y_mm, size in _rows(dataset.db_path, """
            SELECT d.wafer_id, d.x_mm, d.y_mm, p.wafer_size_mm
            FROM defects d JOIN wafers w ON w.wafer_id = d.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN products p ON p.product_id = l.product_id"""):
        bucket = per_wafer[wafer]
        bucket[1] += 1
        if math.hypot(x_mm, y_mm) >= EDGE_RADIUS_FRACTION * (size / 2.0):
            bucket[0] += 1
    inside = [o / t for w, (o, t) in per_wafer.items()
              if t >= 10 and w in affected]
    outside = [o / t for w, (o, t) in per_wafer.items()
               if t >= 10 and w not in affected]
    if len(inside) < 5 or len(outside) < 20:
        return Finding("L6 signature overlap", True, "too few wafers",
                       skipped=True)
    overlap = _overlap_coefficient(inside, outside)
    return Finding("L6 signature overlap", overlap >= 0.2,
                   f"overlap coefficient {overlap:.2f} (floor 0.20)")


def _overlap_coefficient(left: Sequence[float],
                         right: Sequence[float]) -> float:
    """Histogram overlap of two samples on a shared 20-bin grid."""
    low, high = min(*left, *right), max(*left, *right)
    if high <= low:
        return 1.0
    bins = 20
    def histogram(values: Sequence[float]) -> list[float]:
        counts = [0] * bins
        for value in values:
            index = min(bins - 1, int((value - low) / (high - low) * bins))
            counts[index] += 1
        return [c / len(values) for c in counts]
    return sum(min(a, b) for a, b in zip(histogram(left), histogram(right)))


def l7_null_blindness(dataset: Any, floor: float = 2.5) -> Finding:
    """On a null, no chamber may stand out beyond the natural-variation floor.

    The reference queries are run unchanged on a world with nothing wrong in
    it. The floor is expressed in leave-one-out σ across chambers, which is
    the same currency the fault scenarios are reported in — so "below the
    floor" and "above it" are the same measurement, not two.
    """
    if dataset.truth["events"]:
        return Finding("L7 null blindness", True, "not a null scenario",
                       skipped=True)
    worst: list[str] = []
    for name, scores in (("edge CD", _values(chamber_edge_cd(dataset))),
                         ("edge share",
                          _values(chamber_edge_defect_share(dataset.db_path))),
                         ("yield split",
                          _values(chamber_yield_split(dataset.db_path)))):
        if len(scores) < 3:
            continue
        extremes = {label: abs(zscore(scores, label)) for label in scores}
        top = max(extremes, key=extremes.get)
        if extremes[top] > floor:
            worst.append(f"{name}: {top} at {extremes[top]:.2f}sigma")
    return Finding("L7 null blindness", not worst,
                   "no chamber above the floor" if not worst
                   else "; ".join(worst))


def chamber_edge_cd(dataset: Any) -> dict[str, Any]:
    from fabeval.queries import chamber_edge_cd_deviation
    return chamber_edge_cd_deviation(dataset.db_path)


def _values(scores: Mapping[str, Any]) -> dict[str, float]:
    return {label: score.value for label, score in scores.items()}


def l8_seed_sensitivity(datasets: Sequence[Any]) -> Finding:
    """Different seeds, different realizations, identical scenario semantics.

    Two halves and both matter: the affected-wafer sets must differ (Jaccard
    below 0.9, `PHASE_1_ACCEPTANCE.md` A2) so a benchmark is not scoring one
    lucky draw, and the mechanism, target and onset *intent* must be identical
    so the three datasets are the same scenario.
    """
    if len(datasets) < 2:
        return Finding("L8 seed sensitivity", True, "one seed", skipped=True)
    semantics = {(e["mechanism"], e["target"]["tool"], e["target"]["chamber"],
                  e["severity"])
                 for d in datasets for e in d.truth["events"]}
    cohorts = [_affected_wafers(d.truth) for d in datasets]
    if not any(cohorts):
        digests = {d.observable.content_sha256() for d in datasets}
        return Finding("L8 seed sensitivity", len(digests) == len(datasets),
                       f"null: {len(digests)} distinct realizations")
    jaccards = []
    for i in range(len(cohorts)):
        for j in range(i + 1, len(cohorts)):
            union = cohorts[i] | cohorts[j]
            jaccards.append(len(cohorts[i] & cohorts[j]) / len(union)
                            if union else 1.0)
    ok = len(semantics) == 1 and max(jaccards) < 0.9
    return Finding(
        "L8 seed sensitivity", ok,
        f"max pairwise Jaccard {max(jaccards):.3f} (limit 0.9); "
        f"{len(semantics)} distinct scenario semantics (want 1)")


def l9_code_plane_lint(_dataset: Any = None) -> Finding:
    """`fabops`, `app` and the notebooks reach neither plane of a dataset."""
    repository = Path(__file__).resolve().parents[2]
    problems: list[str] = []
    for root in ("src/fabops", "app", "notebooks"):
        directory = repository / root
        if not directory.exists():
            continue
        for module in sorted(directory.rglob("*.py")):
            if "__pycache__" in module.parts:
                continue
            source = module.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in ("fabsim", "fabeval"):
                        problems.append(f"{module.name} imports {name}")
            for token in ("truth.json", "truth/", "scenarios/"):
                if token in source:
                    problems.append(f"{module.name} names {token!r}")
    return Finding("L9 code-plane lint", not problems,
                   "clean" if not problems else "; ".join(problems))


def l10_constant_fingerprint(dataset: Any) -> Finding:
    """No numeric column constant inside the cohort while varying outside."""
    affected = _affected_wafers(dataset.truth)
    if not affected:
        return Finding("L10 constant fingerprint", True, "no cohort",
                       skipped=True)
    problems: list[str] = []
    for table, column in (("wafer_yield", "yield_pct"),
                          ("defects", "size_um"), ("defects", "x_mm"),
                          ("metrology", "value"),
                          ("run_measurements", "value")):
        join = ("" if table in ("wafer_yield", "defects", "metrology")
                else "JOIN runs r ON r.run_id = t.run_id ")
        key = "t.wafer_id" if not join else "r.wafer_id"
        rows = _rows(dataset.db_path,
                     f"SELECT {key}, t.{column} FROM {table} t {join}")
        inside = [v for w, v in rows if w in affected]
        outside = [v for w, v in rows if w not in affected]
        if len(inside) < 10 or len(outside) < 10:
            continue
        if st.pstdev(inside) == 0.0 and st.pstdev(outside) > 0.0:
            problems.append(f"{table}.{column}")
    return Finding("L10 constant fingerprint", not problems,
                   "no constant column" if not problems
                   else f"constant inside cohort: {problems}")


def l11_reference_recovery(dataset: Any,
                           expectation: Mapping[str, Any] | None) -> Finding:
    """The intended evidence is recoverable — or is at the floor, if subtle.

    L11 is the flip side of leakage, and its expectations live in a fixture
    table (`fabeval.fixtures`) rather than here: a check that decided for
    itself what "recoverable" means could always be satisfied.
    """
    if expectation is None:
        return Finding("L11 reference recovery", True,
                       "no expectation declared for this scenario",
                       skipped=True)
    from fabeval.fixtures import evaluate_expectation

    ok, detail = evaluate_expectation(dataset, expectation)
    return Finding("L11 reference recovery", ok, detail)


#: The suite, in order. `L9` takes no dataset and `L8` takes several; the
#: runner knows which is which rather than forcing one signature on all.
LEAKAGE_TESTS = ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L9", "L10", "L11")


def run_leakage_suite(dataset: Any,
                      expectation: Mapping[str, Any] | None = None
                      ) -> list[Finding]:
    """Every per-dataset leakage check. L8 is cross-dataset; see the matrix."""
    return [
        l1_schema_token_lint(dataset),
        l2_plane_separation(dataset),
        l3_mediation(dataset),
        l4_perfect_separation(dataset),
        l5_classifier_honesty(dataset),
        l6_signature_overlap(dataset),
        l7_null_blindness(dataset),
        l9_code_plane_lint(dataset),
        l10_constant_fingerprint(dataset),
        l11_reference_recovery(dataset, expectation),
    ]
