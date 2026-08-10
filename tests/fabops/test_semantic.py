"""
The semantic layer, checked against the tables it claims to summarize.

A view layer's failure mode is not a crash — it is a number that is quietly
wrong because a join multiplied a row or a filter dropped one. So almost every
test here is a *reconciliation*: the layer's own count or total against the
base table's, computed a second way.

Three properties are structural rather than arithmetic and are checked first,
because each one is a defect this repository has either had or come close to:

* installing the layer must not change the database (the manifest records that
  file's hash and a benchmark scores it afterwards);
* the layer must refuse a schema v1 database (`fabops.config.DB_PATH` names
  one, and answering v2 questions about it would produce no error anywhere);
* "the edge" must mean the same thing here as in the evaluator's reference
  queries, or two surfaces of this project disagree about a word.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from fabops.semantic import (CENTER_RADIUS_FRACTION, EDGE_RADIUS_FRACTION,
                             LAYER_VERSION, VIEWS, columns, iter_dicts,
                             open_layer, read, rows)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def layer(demo_dataset):
    connection = open_layer(demo_dataset["db_path"])
    yield connection
    connection.close()


# --------------------------------------------------------------- structural


def test_installing_the_layer_leaves_the_dataset_byte_identical(demo_dataset):
    """The property that makes a read-only analytical layer safe to point at a
    scored artifact. `CREATE TEMP VIEW` lands in the connection's temp schema;
    a plain `CREATE VIEW` would land in the file and move its manifest hash."""
    path = Path(demo_dataset["db_path"])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    connection = open_layer(path)
    try:
        for view in VIEWS:
            connection.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()
    finally:
        connection.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_the_connection_cannot_write(layer):
    with pytest.raises(sqlite3.OperationalError):
        layer.execute("UPDATE wafer_yield SET yield_pct = 0")


def test_a_persistent_view_would_be_refused(layer):
    """The mutation that shows *why* the views are TEMP views.

    A `CREATE VIEW` writes into `sqlite_master` — into the file whose SHA-256
    the manifest records and whose content the benchmark scores. On this
    connection it cannot even be attempted, which is what makes the byte
    identity above a property of the design rather than of the SQL happening to
    say TEMP today.
    """
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        layer.execute("CREATE VIEW v_persistent AS SELECT 1 AS x")
    layer.execute("CREATE TEMP VIEW v_temporary AS SELECT 1 AS x")
    assert layer.execute("SELECT x FROM v_temporary").fetchone() == (1,)


def test_every_view_in_the_layer_file_is_declared_temp():
    """The static half: a view added later without TEMP would write into the
    dataset the first time anybody opened it read-write."""
    from fabops.semantic import VIEWS, layer_sql

    sql = layer_sql()
    assert sql.upper().count("CREATE VIEW") == 0
    assert sql.upper().count("CREATE TEMP VIEW") == len(VIEWS)


def test_the_layer_refuses_a_database_that_is_not_schema_v2(tmp_path):
    legacy = REPO / "data" / "fab.db"
    if not legacy.exists():                                # pragma: no cover
        pytest.skip("the legacy database has not been built")
    with pytest.raises(ValueError, match="schema version"):
        open_layer(legacy)


def test_the_layer_has_no_default_database(tmp_path):
    """A v2 surface that defaulted to `fabops.config.DB_PATH` would answer
    about a different fab in silence. The path is always the caller's."""
    import ast
    import inspect

    from fabops import semantic

    signature = inspect.signature(semantic.open_layer)
    assert signature.parameters["db_path"].default is inspect.Parameter.empty

    tree = ast.parse(Path(semantic.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert "fabops.config" not in imported


def test_every_view_is_queryable(layer):
    assert len(VIEWS) >= 20
    for view in VIEWS:
        layer.execute(f"SELECT * FROM {view} LIMIT 5").fetchall()
        assert columns(layer, view)


def test_read_requires_an_order(layer):
    import inspect

    from fabops import semantic

    assert (inspect.signature(semantic.read).parameters["order_by"].default
            is inspect.Parameter.empty)
    with pytest.raises(ValueError):
        read(layer, "v_product_attainment", order_by="  ")
    with pytest.raises(KeyError):
        read(layer, "sqlite_master", order_by="name")


def test_the_edge_means_the_same_thing_as_it_does_to_the_evaluator():
    """Two packages declare this constant independently — on purpose, because
    `fabops` may not import `fabeval`. What is not acceptable is that they
    drift, so the drift is what the test measures."""
    from fabeval.queries import EDGE_RADIUS_FRACTION as evaluator_edge

    assert EDGE_RADIUS_FRACTION == evaluator_edge
    assert 0.0 < CENTER_RADIUS_FRACTION < EDGE_RADIUS_FRACTION < 1.0
    assert LAYER_VERSION.count(".") == 2


# ------------------------------------------------------------ reconciliation


@pytest.mark.parametrize("view, table", [
    ("fact_wafer_step", "runs"),
    ("fact_yield", "wafer_yield"),
    ("fact_die", "die_bins"),
    ("fact_defect", "defects"),
    ("v_defect_zone", "defects"),
    ("fact_run_param", "run_measurements"),
    ("v_chamber_state_intervals", "tool_states"),
    ("fact_maintenance", "maintenance"),
    ("fact_alarm", "alarms"),
])
def test_a_fact_view_neither_multiplies_nor_drops_a_row(layer, view, table):
    """The classic view defect: one extra join row per fact. Every fact here
    is one row per base row, and a join that fanned out would show up as a
    count larger than the table it came from."""
    fact = layer.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
    base = layer.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert fact == base, f"{view} has {fact} rows against {table}'s {base}"


def test_metrology_is_the_one_fact_that_may_be_smaller(layer):
    """`fact_metrology` joins the recipe metric, and a step whose recipe
    declares no metric has nothing to reference a reading against — so it is
    filtered rather than carried with a NULL target. It may never be *larger*,
    which would mean the run join fanned out."""
    fact = layer.execute("SELECT COUNT(*) FROM fact_metrology").fetchone()[0]
    base = layer.execute("SELECT COUNT(*) FROM metrology").fetchone()[0]
    assert 0 < fact <= base
    assert layer.execute(
        "SELECT COUNT(*) FROM fact_metrology "
        "WHERE metric_target IS NULL").fetchone()[0] == 0


def test_the_die_facts_reconcile_with_the_wafer_yield_rows(layer):
    """§4.3's reconciliation invariant, read through the layer: a wafer's
    `total_die` is the count of its die rows and `good_die` the count of its
    PASS rows. The generator guarantees it; a view that grouped wrongly would
    break it, which is what makes this a test of the layer."""
    mismatched = layer.execute("""
        SELECT COUNT(*) FROM (
            SELECT y.wafer_id, y.total_die, y.good_die,
                   COUNT(*) AS die, SUM(d.is_pass) AS good
            FROM fact_yield y JOIN fact_die d ON d.wafer_id = y.wafer_id
            GROUP BY y.wafer_id, y.total_die, y.good_die
            HAVING die <> y.total_die OR good <> y.good_die)""").fetchone()[0]
    assert mismatched == 0


def test_utilization_accounts_for_the_whole_horizon(layer):
    """State intervals tile the horizon per chamber, so every chamber's
    minutes must sum to the horizon. This is what makes `utilization` a
    fraction of something real rather than of whatever was recorded."""
    horizon_days = layer.execute(
        "SELECT horizon_days FROM dataset_meta").fetchone()[0]
    expected = horizon_days * 1440.0
    seen = read(layer, "v_chamber_utilization", order_by="chamber_label")
    assert len(seen) >= 7
    for row in seen:
        total = row[6]
        assert abs(total - expected) < 1.0, row
        assert 0.0 <= row[7] <= 1.0


def test_lot_exposure_covers_every_wafer_that_ran_the_step(layer):
    """Containment ranks lots by exposure, so a lot missing from the view is a
    lot nobody holds."""
    step = layer.execute(
        "SELECT step_name FROM v_chamber_exposure "
        "ORDER BY runs DESC, step_name LIMIT 1").fetchone()[0]
    exposed = layer.execute(
        "SELECT SUM(exposed_wafers) FROM v_lot_exposure WHERE step_name = ?",
        (step,)).fetchone()[0]
    ran = layer.execute(
        "SELECT COUNT(DISTINCT wafer_id) FROM fact_wafer_step "
        "WHERE step_name = ?", (step,)).fetchone()[0]
    assert exposed == ran


def test_the_zone_cut_is_the_declared_one(layer):
    """A zone is derived from geometry here, so the boundary has to be the
    constant the module declares — not a number typed twice."""
    wrong = layer.execute("""
        SELECT COUNT(*) FROM v_defect_zone
        WHERE (zone = 'EDGE'   AND radius_fraction < ?)
           OR (zone = 'CENTER' AND radius_fraction > ?)
           OR (zone = 'MID'    AND (radius_fraction >= ?
                                    OR radius_fraction <= ?))""",
        (EDGE_RADIUS_FRACTION, CENTER_RADIUS_FRACTION,
         EDGE_RADIUS_FRACTION, CENTER_RADIUS_FRACTION)).fetchone()[0]
    assert wrong == 0


def test_the_wafer_profile_shares_sum_to_one(layer):
    for row in iter_dicts(layer, "v_wafer_defect_profile",
                          order_by="wafer_id, layer"):
        total = row["edge_share"] + row["mid_share"] + row["center_share"]
        assert abs(total - 1.0) < 1e-9, row


# ------------------------------------------------------- mix normalization


def test_a_pure_mix_shift_moves_the_raw_trend_and_not_the_normalized_one(
        demo_dataset, tmp_path):
    """The audited defect, reproduced and then shown fixed.

    `v_weekly_yield` in the v1 layer swung 24 points on product mix alone,
    because each week's bucket held roughly one lot of one product. The test
    sets **every wafer's yield to exactly its own product's target** — so the
    fab is, by construction, performing identically everywhere and every
    week's variation is mix and nothing else. The raw weekly mean must still
    move (the mix really does change); the target-normalized one must be
    exactly zero in every week.
    """
    victim = tmp_path / "constant_per_product.db"
    shutil.copy(demo_dataset["db_path"], victim)
    connection = sqlite3.connect(str(victim))
    try:
        connection.execute("""
            UPDATE wafer_yield SET yield_pct = (
                SELECT p.target_yield_pct FROM lots l
                JOIN products p ON p.product_id = l.product_id
                WHERE l.lot_id = wafer_yield.lot_id)""")
        connection.commit()
    finally:
        connection.close()

    layer = open_layer(victim)
    try:
        weekly = read(layer, "v_yield_trend", order_by="week_index")
    finally:
        layer.close()

    raw = [row[3] for row in weekly]
    normalized = [row[4] for row in weekly]
    assert max(normalized) - min(normalized) < 1e-9, (
        "the target-normalized trend moved on a fab that performed identically "
        "in every week; the normalization is not removing the product mix")
    assert max(raw) - min(raw) > 1.0, (
        "the raw weekly mean did not move either, so this dataset has no mix "
        "shift to normalize and the test is measuring nothing")


# ------------------------------------------- agreement with the evaluator


def test_the_facts_reproduce_the_evaluators_edge_share_exactly(demo_dataset):
    """The layer and `fabeval.queries` are separate code by design — the
    analyst's vocabulary and the grader's fixed instrument — so what has to be
    proven is that they describe the same fab.

    Two grains differ and both are defensible, which is why this is measured
    rather than assumed. The evaluator pools the etch *operation* and counts one
    row per (defect, run) pair, so a wafer that met the same chamber at both
    etch steps weighs double; the layer's own view is per step and counts a
    wafer once. The evaluator's grain is reproduced here **from the layer's
    facts** — proving the facts are faithful — and the layer's deduplicated
    share is then checked to be close but deliberately not identical, so that
    neither definition can drift silently into the other.
    """
    from fabeval.queries import chamber_edge_defect_share

    reference = chamber_edge_defect_share(demo_dataset["db_path"],
                                          layer="GATE", operation="ETCH")
    connection = open_layer(demo_dataset["db_path"])
    try:
        evaluator_grain = {
            label: (outer / total, total)
            for label, outer, total in connection.execute("""
                SELECT f.chamber_label, SUM(z.zone = 'EDGE'), COUNT(*)
                FROM fact_wafer_step f
                JOIN v_defect_zone z ON z.wafer_id = f.wafer_id
                WHERE f.operation_type = 'ETCH' AND z.layer = 'GATE'
                GROUP BY f.chamber_label
                ORDER BY f.chamber_label""")}
        layer_grain = {
            row[1]: row[6] for row in read(
                connection, "v_chamber_defect_signature",
                order_by="step_name, chamber_label, layer",
                where="step_name = ? AND layer = ?",
                params=("GATE_ETCH", "GATE"))}
    finally:
        connection.close()

    assert set(reference) <= set(evaluator_grain)
    for label, score in reference.items():
        share, total = evaluator_grain[label]
        assert total == score.support, label
        assert share == pytest.approx(score.value, rel=1e-12), label

    assert layer_grain, "the layer produced no per-step signature to compare"
    for label, share in layer_grain.items():
        assert share == pytest.approx(reference[label].value, abs=0.05), label
