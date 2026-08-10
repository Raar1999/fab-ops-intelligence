"""
Row order must not reach a result.

SQL licenses a database to return rows in **any** order unless the query says
otherwise, and both analysis packages fold rows into floats: a bin mean, a
product mean, a per-wafer defect share, a leave-one-out spread. Two orders of
the same rows are therefore two answers differing in their last bits — and the
decision plane turns values into *ranks*, where a last-bit difference is a tie
broken by whichever plan the query planner happened to choose.

The defect was real and latent when it was found. Measured on the library at
seed 42 before the fix: reversing the row order moved **383 of the
peer-differenced series** on the null world alone, while leaving the report
itself intact — so nothing was visibly wrong, and nothing would have been until
two candidates sat close enough together for the last bit to decide which one
the fab was told to go and inspect. Phase 6 then runs the engine over hundreds
of datasets to *select* a method; a selection partly decided by SQLite's query
planner is not a measurement of the method.

Two clauses, because either alone can pass while the property is false:

* **Static** — every multi-row query in the analysis plane states a total
  order. Checkable by reading, and it fails on a query added later.
* **Runtime** — a connection that shuffles the rows of any query *without* an
  `ORDER BY` leaves the report byte-identical. That harness is not arbitrary:
  it is exactly the licence SQL grants, so a query that relies on scan order
  fails and a query that states its order cannot.

The runtime clause needs its mirror or it passes on an inert engine, so the
same harness is used to show that perturbing an *observable value* does move
the report.
"""
from __future__ import annotations

import ast
import json
import random
import re
import sqlite3
from pathlib import Path

import pytest

from tests.fabops.datasets import build_all

REPO = Path(__file__).resolve().parents[1]

#: The packages whose numbers a row order could reach. The legacy v1 surfaces
#: (`fabops.investigation`, `fabops.charts`, `fabops.db`, `sql/`) read a
#: different database and are scoped out on purpose — see
#: `test_the_legacy_demo_surface_is_row_order_stable`, which measures that
#: rather than assuming it.
#:
#: The Phase 2–7 packages join the list as they land, for the same reason the
#: original two are on it: each folds rows into floats and the surfaces above
#: them turn floats into ranks. `fabops.semantic` also enforces the rule in its
#: own signature — `read(..., order_by=...)` has no default — so a caller has
#: to state an order before the lint ever sees the query.
ANALYSIS_PACKAGES = ("src/fabops/diagnosis", "src/fabops/semantic",
                     "src/fabops/monitors", "src/fabops/impact",
                     "src/fabops/actions", "src/fabops/report", "src/fabeval")

#: A query returning at most one row has no order to depend on.
_SINGLE_ROW = ("FROM dataset_meta",)

#: …and neither does a bare aggregate. `SELECT COUNT(*) FROM t WHERE …` with no
#: `GROUP BY` returns exactly one row by definition, so requiring an order on it
#: would be requiring a no-op — and a rule that demands no-ops is a rule people
#: learn to satisfy without reading.
_AGGREGATES = ("COUNT(", "SUM(", "AVG(", "MIN(", "MAX(", "TOTAL(")


def _returns_one_row(sql: str) -> bool:
    if any(marker in sql for marker in _SINGLE_ROW):
        return True
    # `LIMIT 0` returns no rows at all — it is how a caller reads a cursor's
    # column names — so there is no order for anything to depend on. `LIMIT 1`
    # is deliberately *not* exempt: without an order it returns whichever row
    # the planner reached first, which is the defect this lint exists for.
    if re.search(r"\bLIMIT\s+0\b", sql, re.I):
        return True
    head = sql.upper().split("FROM", 1)[0]
    return (any(fn in head for fn in _AGGREGATES)
            and "GROUP BY" not in sql.upper())

#: The two worlds the runtime clause is exercised on: one with nothing wrong in
#: it, where every candidate is a near-tie and last bits matter most, and one
#: with a planted fault, where a moved rank would be a moved conclusion.
_WORLDS = ("null_baseline", "chamber_edge_uniformity")


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    """A local build, so this module does not depend on another package's
    conftest — it tests both analysis packages and belongs to neither."""
    root = tmp_path_factory.mktemp("row-order")
    built = build_all([(name, 42, str(root)) for name in _WORLDS])
    return {record["scenario"]: record for record in built}


def _sql_literals(path: Path) -> list[tuple[int, str]]:
    """Every string constant in a module that looks like a SELECT."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []

    def text_of(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                value.value if isinstance(value, ast.Constant) else "{}"
                for value in node.values)
        return None

    for node in ast.walk(tree):
        # Only strings that are *arguments to a query call* are SQL; a
        # docstring that mentions SELECT is prose.
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (callee.attr if isinstance(callee, ast.Attribute)
                else getattr(callee, "id", ""))
        if name not in ("execute", "_rows", "executescript"):
            continue
        for argument in node.args:
            body = text_of(argument)
            if body and re.search(r"\bSELECT\b", body, re.I):
                found.append((argument.lineno, " ".join(body.split())))
    return found


def test_every_multi_row_query_in_the_analysis_plane_states_an_order():
    """The static clause. A query added later without one fails here."""
    offenders: list[str] = []
    scanned = 0
    for package in ANALYSIS_PACKAGES:
        directory = REPO / package
        assert directory.is_dir(), (
            f"{package} is on the list and does not exist; a package that is "
            f"listed and absent is a package this lint silently skips")
        for path in sorted(directory.rglob("*.py")):
            scanned += 1
            for line, sql in _sql_literals(path):
                # A correlated EXISTS(...) is a predicate, not a row sequence.
                outer = re.sub(r"EXISTS\s*\(SELECT.*?\)", "", sql,
                               flags=re.I | re.S)
                if not re.search(r"\bSELECT\b", outer, re.I):
                    continue
                if _returns_one_row(outer):
                    continue
                if re.search(r"\bORDER BY\b", outer, re.I):
                    continue
                offenders.append(
                    f"{path.relative_to(REPO).as_posix()}:{line}  "
                    f"{outer[:90]}")
    assert not offenders, (
        "these queries let the storage engine choose the row order, and the "
        "value they feed is accumulated in float:\n  " + "\n  ".join(offenders))
    assert scanned >= 20, (
        f"only {scanned} modules were read; a lint that stops finding files "
        f"stops finding defects")


class _ShuffledRows:
    """Enough of a cursor for the callers under test."""

    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _PlannerFromHell:
    """A connection that exercises the licence SQL actually grants.

    Rows come back shuffled for every query that does not state an order, and
    in the stated order for every query that does. A module relying on scan
    order fails against it; one that states its order cannot notice it is
    there.
    """

    def __init__(self, inner: sqlite3.Connection, seed: int) -> None:
        self._inner = inner
        self._rng = random.Random(seed)

    def execute(self, sql, *args, **kwargs):
        rows = self._inner.execute(sql, *args, **kwargs).fetchall()
        outer = re.sub(r"EXISTS\s*\(SELECT.*?\)", "", sql, flags=re.I | re.S)
        if not re.search(r"\bORDER BY\b", outer, re.I):
            rows = list(rows)
            self._rng.shuffle(rows)
        return _ShuffledRows(rows)

    def executescript(self, sql):
        """Installing a view layer is a DDL statement, not a row sequence."""
        return self._inner.executescript(sql)

    def close(self):
        self._inner.close()


@pytest.fixture()
def hostile_planner(monkeypatch):
    """Install `_PlannerFromHell` for the duration of one test."""
    real = sqlite3.connect

    def install(seed: int) -> None:
        monkeypatch.setattr(
            sqlite3, "connect",
            lambda path, *a, **k: _PlannerFromHell(real(path, *a, **k), seed))

    return install


def _report(db_path) -> str:
    from fabops.diagnosis import diagnose

    return json.dumps(diagnose(db_path).to_dict(), sort_keys=True)


@pytest.mark.parametrize("scenario", ["null_baseline",
                                      "chamber_edge_uniformity"])
def test_the_report_survives_a_hostile_row_order(worlds, hostile_planner,
                                                 scenario):
    """The runtime clause, on a fault-free world and on a faulted one."""
    db_path = worlds[scenario]["db_path"]
    baseline = _report(db_path)
    for seed in (1, 2, 3):
        hostile_planner(seed)
        assert _report(db_path) == baseline, (
            f"the report on {scenario} moved when the rows arrived in a "
            f"different order (shuffle seed {seed})")


@pytest.mark.parametrize("scenario", ["null_baseline",
                                      "chamber_edge_uniformity"])
def test_the_monitor_report_survives_a_hostile_row_order(worlds,
                                                         hostile_planner,
                                                         scenario):
    """The Phase 3 plane, held to the same rule as the engine.

    Every family folds rows into daily means and then into control limits, and
    a limit is a threshold a value is either side of — the same place a last-bit
    difference becomes a different answer.
    """
    from fabops.monitors import monitor

    db_path = worlds[scenario]["db_path"]
    baseline = json.dumps(monitor(db_path).to_dict(), sort_keys=True)
    for seed in (6, 7):
        hostile_planner(seed)
        assert json.dumps(monitor(db_path).to_dict(),
                          sort_keys=True) == baseline, (
            f"the monitor report on {scenario} moved when the rows arrived in "
            f"a different order (shuffle seed {seed})")


def test_the_decision_artifact_survives_a_hostile_row_order(worlds,
                                                            hostile_planner):
    """The Phase 7 plane. Impact is a difference of means over rows, and
    containment is a *ranking* of lots by exposure."""
    from fabops.report import build_report

    db_path = worlds["chamber_edge_uniformity"]["db_path"]
    subject = "ETCH-01/A"
    baseline = build_report(db_path, subject=subject).to_json(indent=None)
    for seed in (8, 9):
        hostile_planner(seed)
        assert build_report(db_path,
                            subject=subject).to_json(indent=None) == baseline


def test_the_reference_queries_survive_a_hostile_row_order(worlds,
                                                           hostile_planner):
    """The evaluator's side. Its dict *order* used to move, and `zscore`
    sums the peers in that order while `rank` breaks ties by it."""
    from fabeval import queries

    db_path = worlds["chamber_edge_uniformity"]["db_path"]
    functions = (queries.chamber_yield_split, queries.chamber_edge_defect_share,
                 queries.chamber_edge_cd_deviation, queries.alarm_counts)
    baseline = {fn.__name__: [(label, float.hex(score.value))
                              for label, score in fn(db_path).items()]
                for fn in functions}
    for seed in (4, 5):
        hostile_planner(seed)
        moved = {fn.__name__: [(label, float.hex(score.value))
                               for label, score in fn(db_path).items()]
                 for fn in functions}
        assert moved == baseline


def test_the_legacy_demo_surface_is_row_order_stable(tmp_path):
    """The legacy v1 narrative is out of the correction's scope — measured.

    `fabops.investigation` reads the committed schema-v1 database through
    pandas and is protected by ADR-010, so it was deliberately not rewritten.
    Deliberate is not the same as unexamined: the database is copied with every
    table physically rewritten in reverse row order — the thing an
    `ORDER BY`-less query is at the mercy of — and the narrated story must come
    out the same. It does, so the surface carries no deferred debt; if a future
    edit gives it some, this is where it shows up.
    """
    import contextlib
    import importlib
    import io
    import shutil

    import fabops.db as db

    original = REPO / "data" / "fab.db"
    if not original.exists():                            # pragma: no cover
        pytest.skip("the legacy database has not been built")

    shuffled = tmp_path / "fab_reversed.db"
    shutil.copy(original, shuffled)
    connection = sqlite3.connect(str(shuffled))
    try:
        tables = [r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        connection.execute("PRAGMA foreign_keys = OFF;")
        for table in tables:
            connection.execute(f'CREATE TABLE "__tmp" AS SELECT * FROM '
                               f'"{table}" ORDER BY rowid DESC')
            connection.execute(f'DELETE FROM "{table}"')
            connection.execute(f'INSERT INTO "{table}" SELECT * FROM "__tmp"')
            connection.execute('DROP TABLE "__tmp"')
        connection.commit()
    finally:
        connection.close()

    def story(db_path: Path) -> str:
        real_query, real_view = db.run_query, db.run_view
        db.run_query = lambda sql, path=None, params=None: real_query(
            sql, db_path, params)
        db.run_view = lambda view, path=None, order_by=None: real_view(
            view, db_path, order_by)
        try:
            investigation = importlib.reload(
                importlib.import_module("fabops.investigation"))
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                investigation.main()
            return buffer.getvalue()
        finally:
            db.run_query, db.run_view = real_query, real_view

    assert story(original) == story(shuffled), (
        "the legacy ETCH-02 story changed when the rows were stored in a "
        "different order; the v1 surface now carries the defect the analysis "
        "plane was corrected for, and ADR-010 protection is not a reason to "
        "leave it")


def test_the_harness_can_still_see_a_changed_observable(tmp_path, worlds):
    """The mirror. Invariance is worthless if the engine is inert.

    A single wafer's yield is edited in a copy of the database and the report
    must move. Without this, a `diagnose` that returned a constant would pass
    every assertion above.
    """
    import shutil

    source = Path(worlds["chamber_edge_uniformity"]["db_path"])
    victim = tmp_path / "perturbed.db"
    shutil.copy(source, victim)

    baseline = _report(source)
    connection = sqlite3.connect(str(victim))
    try:
        connection.execute(
            "UPDATE wafer_yield SET yield_pct = yield_pct * 0.5 "
            "WHERE wafer_id IN (SELECT wafer_id FROM wafer_yield "
            "                   ORDER BY wafer_id LIMIT 40)")
        connection.commit()
    finally:
        connection.close()

    assert _report(victim) != baseline, (
        "halving forty wafers' yield left the report unchanged; the "
        "row-order invariance above is passing because nothing is being read")
