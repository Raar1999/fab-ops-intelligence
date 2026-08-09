"""
observable.py — the observable plane: schema v2 SQLite, and a portable dump.

The projection `SCHEMA_V2_DESIGN.md` §2 specifies, and nothing else. Every
row here is something a real fab's MES, FDC, metrology, defect-inspection or
test system would have recorded; nothing in it encodes a fault, a scenario, a
mechanism or a latent value, because none of those has a column to live in::

    world + timeline + observations + defects + die
                        │  a pure projection: no draws, no inference
                        ▼
              22 tables, in schema order
                        │
            ┌───────────┴───────────┐
            ▼                       ▼
         fab.db            fab_database.sql
            │                       │
            └──── content_sha256 ───┘   canonical, row-level, portable

**This module is a serializer, not a model.** It draws no random numbers,
computes no physics and makes no causal judgement — everything it writes was
already realized upstream. That matters twice: a build is reproducible because
the emitter adds no entropy, and the emitter cannot become the place where a
fault reaches an observable, because it has nothing to reach with.

**It is never handed the hidden plane.** `emit_observable` takes a timeline,
the process observations, the defect population, the die population and the
alarms — five observable collections. There is no `Realization` parameter, so latent
trajectories, mechanism records, distractors, the counterfactual, the hidden
defect origin and the die kill cause are unreachable rather than merely
unwritten. The truth emitter is a separate module that this one does not
import (ADR-013), and the two planes meet only in `eval/`, on `dataset_id`.

**Determinism is a property of the writing, not of luck.** Tables are created
in a fixed order, rows are inserted in primary-key order, no wall clock
reaches a value, and the `.sql` dump is written by this module rather than by
`sqlite3.iterdump()` — whose statement order follows `sqlite_master` and is
therefore a property of the storage engine. The portable identity is
`content_sha256`: a canonical row-level digest, tables in name order, rows in
primary-key order, values type-tagged (`PHASE_1_ACCEPTANCE.md` A1 §2). A byte
compare of `fab.db` would be testing SQLite's page layout, which is why A1
demoted it to a reference-image check.

**One stated deviation from §2.11.** `lots.priority` is a v1 carry-over —
there, a `random.choice` over HOT/STANDARD/LOW, causally inert like the
operator dimension. The FabSim timeline models no lot priority (releases are
a cadence, and scheduling is availability-driven, `TEMPORAL_MODEL.md` §2), so
there is nothing realized for the column to hold. Drawing one *here* would put
entropy in the serializer and make the emitter a generator, so the column is
omitted rather than invented, and the gap is recorded in ADR-023 as work for
a timeline slice if a scenario ever needs it.
"""
from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from fabsim.defects import DefectPopulation
from fabsim.die import DiePopulation
from fabsim.observation import ProcessObservations
from fabsim.timeline import Timeline
from fabsim.world import World

__all__ = [
    "INSERT_ORDER",
    "OBSERVABLE_DDL",
    "SCHEMA_TABLES",
    "ObservableDataset",
    "content_sha256",
    "emit_observable",
    "project",
]

#: The 22 tables of `SCHEMA_V2_DESIGN.md` §2.1–2.22, in the order that
#: document numbers them. This is the schema's own identity for the list and
#: the order the schema's own documentation reads in. It is deliberately
#: *not* the order rows are written in, because §2 numbers `products` (2.2)
#: before the `process_flows` (2.3) it references. Three orders, each stated
#: where it is used: this one for the document, `INSERT_ORDER` for both the
#: database and the dump, and plain name order for the digest — so none of
#: them can quietly become another.
SCHEMA_TABLES: tuple[str, ...] = (
    "dataset_meta",
    "products",
    "process_flows",
    "flow_steps",
    "process_steps",
    "recipes",
    "recipe_settings",
    "tools",
    "chambers",
    "operators",
    "lots",
    "wafers",
    "runs",
    "run_measurements",
    "metrology",
    "tool_states",
    "alarms",
    "maintenance",
    "inspections",
    "defects",
    "wafer_yield",
    "die_bins",
)

#: Dependency order: a table appears after everything it references, so a
#: database with foreign keys **on** can be filled in one pass. It matches the
#: order `OBSERVABLE_DDL` creates the tables in, and a test holds the two
#: together by reading the FKs back out of the DDL rather than trusting this
#: list.
INSERT_ORDER: tuple[str, ...] = (
    "dataset_meta",
    "process_flows",
    "process_steps",
    "flow_steps",
    "products",
    "recipes",
    "recipe_settings",
    "tools",
    "chambers",
    "operators",
    "lots",
    "wafers",
    "runs",
    "run_measurements",
    "metrology",
    "tool_states",
    "alarms",
    "maintenance",
    "inspections",
    "defects",
    "wafer_yield",
    "die_bins",
)

#: Column order per table. Stable, because a consumer that does `SELECT *`
#: is entitled to a stable shape and because the content digest walks columns
#: in this order.
SCHEMA_COLUMNS: dict[str, tuple[str, ...]] = {
    "dataset_meta": ("schema_version", "fabsim_version", "dataset_id",
                     "time_origin", "horizon_days"),
    "products": ("product_id", "product_name", "flow_id",
                 "technology_node_nm", "wafer_size_mm", "die_size_mm2",
                 "target_yield_pct"),
    "process_flows": ("flow_id", "flow_name"),
    "flow_steps": ("flow_step_id", "flow_id", "step_id", "step_sequence"),
    "process_steps": ("step_id", "step_name", "operation_type",
                      "is_inspection"),
    "recipes": ("recipe_id", "step_id", "product_id", "recipe_name",
                "version", "metric_name", "metric_target", "metric_usl",
                "metric_lsl"),
    "recipe_settings": ("recipe_id", "setting_name", "set_value"),
    "tools": ("tool_id", "tool_name", "tool_type", "vendor", "install_date",
              "location_bay"),
    "chambers": ("chamber_id", "tool_id", "chamber_name", "install_date"),
    "operators": ("operator_id", "operator_name", "shift",
                  "certification_level"),
    "lots": ("lot_id", "lot_number", "product_id", "start_time",
             "finish_time", "status", "wafer_count"),
    "wafers": ("wafer_id", "lot_id", "slot_number", "status"),
    "runs": ("run_id", "wafer_id", "flow_step_id", "tool_id", "chamber_id",
             "recipe_id", "operator_id", "start_time", "end_time"),
    "run_measurements": ("run_meas_id", "run_id", "param_name", "value",
                         "unit", "set_value"),
    "metrology": ("metrology_id", "wafer_id", "flow_step_id",
                  "metrology_tool_id", "meas_time", "param_name", "value",
                  "unit"),
    "tool_states": ("state_id", "tool_id", "chamber_id", "state",
                    "start_time", "end_time"),
    "alarms": ("alarm_id", "tool_id", "chamber_id", "alarm_time",
               "alarm_code", "severity", "message"),
    "maintenance": ("maint_id", "tool_id", "chamber_id", "maint_type",
                    "start_time", "end_time", "technician", "action_code",
                    "description"),
    "inspections": ("inspection_id", "wafer_id", "flow_step_id",
                    "inspection_tool_id", "inspection_time",
                    "total_defect_count", "scan_area_mm2"),
    "defects": ("defect_id", "inspection_id", "wafer_id", "x_mm", "y_mm",
                "size_um", "classified_type", "layer"),
    "wafer_yield": ("yield_id", "wafer_id", "lot_id", "total_die", "good_die",
                    "yield_pct", "test_time"),
    "die_bins": ("wafer_id", "die_x", "die_y", "bin_code"),
}

#: Primary key per table, as the digest and the insert order use it. Where a
#: table has no surrogate key the natural key is spelled out, so "rows in
#: primary-key order" is a defined thing for every table rather than for most.
SCHEMA_KEYS: dict[str, tuple[str, ...]] = {
    "dataset_meta": ("dataset_id",),
    "products": ("product_id",),
    "process_flows": ("flow_id",),
    "flow_steps": ("flow_step_id",),
    "process_steps": ("step_id",),
    "recipes": ("recipe_id",),
    "recipe_settings": ("recipe_id", "setting_name"),
    "tools": ("tool_id",),
    "chambers": ("chamber_id",),
    "operators": ("operator_id",),
    "lots": ("lot_id",),
    "wafers": ("wafer_id",),
    "runs": ("run_id",),
    "run_measurements": ("run_meas_id",),
    "metrology": ("metrology_id",),
    "tool_states": ("state_id",),
    "alarms": ("alarm_id",),
    "maintenance": ("maint_id",),
    "inspections": ("inspection_id",),
    "defects": ("defect_id",),
    "wafer_yield": ("yield_id",),
    "die_bins": ("wafer_id", "die_x", "die_y"),
}

#: The schema itself. Written out rather than generated from the tables above
#: because constraints are the interesting part: foreign keys are declared and
#: enforced, so "every FK resolves" (§4.1) is the database's job rather than a
#: check that runs afterwards and hopes.
OBSERVABLE_DDL = """\
CREATE TABLE dataset_meta (
    schema_version  TEXT    NOT NULL,
    fabsim_version  TEXT    NOT NULL,
    dataset_id      TEXT    NOT NULL PRIMARY KEY,
    time_origin     TEXT    NOT NULL,
    horizon_days    INTEGER NOT NULL
);

CREATE TABLE process_flows (
    flow_id    INTEGER NOT NULL PRIMARY KEY,
    flow_name  TEXT    NOT NULL UNIQUE
);

CREATE TABLE process_steps (
    step_id         INTEGER NOT NULL PRIMARY KEY,
    step_name       TEXT    NOT NULL UNIQUE,
    operation_type  TEXT    NOT NULL,
    is_inspection   INTEGER NOT NULL
);

CREATE TABLE flow_steps (
    flow_step_id   INTEGER NOT NULL PRIMARY KEY,
    flow_id        INTEGER NOT NULL REFERENCES process_flows(flow_id),
    step_id        INTEGER NOT NULL REFERENCES process_steps(step_id),
    step_sequence  INTEGER NOT NULL,
    UNIQUE (flow_id, step_sequence)
);

CREATE TABLE products (
    product_id          INTEGER NOT NULL PRIMARY KEY,
    product_name        TEXT    NOT NULL UNIQUE,
    flow_id             INTEGER NOT NULL REFERENCES process_flows(flow_id),
    technology_node_nm  INTEGER NOT NULL,
    wafer_size_mm       INTEGER NOT NULL,
    die_size_mm2        REAL    NOT NULL,
    target_yield_pct    REAL    NOT NULL
);

CREATE TABLE recipes (
    recipe_id      INTEGER NOT NULL PRIMARY KEY,
    step_id        INTEGER NOT NULL REFERENCES process_steps(step_id),
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    recipe_name    TEXT    NOT NULL,
    version        TEXT    NOT NULL,
    metric_name    TEXT,
    metric_target  REAL,
    metric_usl     REAL,
    metric_lsl     REAL,
    UNIQUE (step_id, product_id)
);

CREATE TABLE recipe_settings (
    recipe_id     INTEGER NOT NULL REFERENCES recipes(recipe_id),
    setting_name  TEXT    NOT NULL,
    set_value     REAL    NOT NULL,
    PRIMARY KEY (recipe_id, setting_name)
);

CREATE TABLE tools (
    tool_id       INTEGER NOT NULL PRIMARY KEY,
    tool_name     TEXT    NOT NULL UNIQUE,
    tool_type     TEXT    NOT NULL,
    vendor        TEXT    NOT NULL,
    install_date  TEXT    NOT NULL,
    location_bay  TEXT    NOT NULL
);

CREATE TABLE chambers (
    chamber_id    INTEGER NOT NULL PRIMARY KEY,
    tool_id       INTEGER NOT NULL REFERENCES tools(tool_id),
    chamber_name  TEXT    NOT NULL,
    install_date  TEXT    NOT NULL,
    UNIQUE (tool_id, chamber_name)
);

CREATE TABLE operators (
    operator_id          INTEGER NOT NULL PRIMARY KEY,
    operator_name        TEXT    NOT NULL,
    shift                TEXT    NOT NULL,
    certification_level  TEXT    NOT NULL
);

CREATE TABLE lots (
    lot_id       INTEGER NOT NULL PRIMARY KEY,
    lot_number   TEXT    NOT NULL UNIQUE,
    product_id   INTEGER NOT NULL REFERENCES products(product_id),
    start_time   TEXT    NOT NULL,
    finish_time  TEXT,
    status       TEXT    NOT NULL,
    wafer_count  INTEGER NOT NULL
);

CREATE TABLE wafers (
    wafer_id     INTEGER NOT NULL PRIMARY KEY,
    lot_id       INTEGER NOT NULL REFERENCES lots(lot_id),
    slot_number  INTEGER NOT NULL,
    status       TEXT    NOT NULL,
    UNIQUE (lot_id, slot_number)
);

CREATE TABLE runs (
    run_id        INTEGER NOT NULL PRIMARY KEY,
    wafer_id      INTEGER NOT NULL REFERENCES wafers(wafer_id),
    flow_step_id  INTEGER NOT NULL REFERENCES flow_steps(flow_step_id),
    tool_id       INTEGER NOT NULL REFERENCES tools(tool_id),
    chamber_id    INTEGER NOT NULL REFERENCES chambers(chamber_id),
    recipe_id     INTEGER NOT NULL REFERENCES recipes(recipe_id),
    operator_id   INTEGER NOT NULL REFERENCES operators(operator_id),
    start_time    TEXT    NOT NULL,
    end_time      TEXT    NOT NULL,
    UNIQUE (wafer_id, flow_step_id)
);

CREATE TABLE run_measurements (
    run_meas_id  INTEGER NOT NULL PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES runs(run_id),
    param_name   TEXT    NOT NULL,
    value        REAL    NOT NULL,
    unit         TEXT    NOT NULL,
    set_value    REAL    NOT NULL,
    UNIQUE (run_id, param_name)
);

CREATE TABLE metrology (
    metrology_id       INTEGER NOT NULL PRIMARY KEY,
    wafer_id           INTEGER NOT NULL REFERENCES wafers(wafer_id),
    flow_step_id       INTEGER NOT NULL REFERENCES flow_steps(flow_step_id),
    metrology_tool_id  INTEGER NOT NULL REFERENCES tools(tool_id),
    meas_time          TEXT    NOT NULL,
    param_name         TEXT    NOT NULL,
    value              REAL    NOT NULL,
    unit               TEXT    NOT NULL
);

CREATE TABLE tool_states (
    state_id    INTEGER NOT NULL PRIMARY KEY,
    tool_id     INTEGER NOT NULL REFERENCES tools(tool_id),
    chamber_id  INTEGER REFERENCES chambers(chamber_id),
    state       TEXT    NOT NULL,
    start_time  TEXT    NOT NULL,
    end_time    TEXT    NOT NULL
);

CREATE TABLE alarms (
    alarm_id    INTEGER NOT NULL PRIMARY KEY,
    tool_id     INTEGER NOT NULL REFERENCES tools(tool_id),
    chamber_id  INTEGER NOT NULL REFERENCES chambers(chamber_id),
    alarm_time  TEXT    NOT NULL,
    alarm_code  TEXT    NOT NULL,
    severity    TEXT    NOT NULL,
    message     TEXT    NOT NULL
);

CREATE TABLE maintenance (
    maint_id     INTEGER NOT NULL PRIMARY KEY,
    tool_id      INTEGER NOT NULL REFERENCES tools(tool_id),
    chamber_id   INTEGER REFERENCES chambers(chamber_id),
    maint_type   TEXT    NOT NULL,
    start_time   TEXT    NOT NULL,
    end_time     TEXT    NOT NULL,
    technician   TEXT    NOT NULL,
    action_code  TEXT    NOT NULL,
    description  TEXT    NOT NULL
);

CREATE TABLE inspections (
    inspection_id       INTEGER NOT NULL PRIMARY KEY,
    wafer_id            INTEGER NOT NULL REFERENCES wafers(wafer_id),
    flow_step_id        INTEGER NOT NULL REFERENCES flow_steps(flow_step_id),
    inspection_tool_id  INTEGER NOT NULL REFERENCES tools(tool_id),
    inspection_time     TEXT    NOT NULL,
    total_defect_count  INTEGER NOT NULL,
    scan_area_mm2       REAL    NOT NULL
);

CREATE TABLE defects (
    defect_id        INTEGER NOT NULL PRIMARY KEY,
    inspection_id    INTEGER NOT NULL REFERENCES inspections(inspection_id),
    wafer_id         INTEGER NOT NULL REFERENCES wafers(wafer_id),
    x_mm             REAL    NOT NULL,
    y_mm             REAL    NOT NULL,
    size_um          REAL    NOT NULL,
    classified_type  TEXT    NOT NULL,
    layer            TEXT    NOT NULL
);

CREATE TABLE wafer_yield (
    yield_id    INTEGER NOT NULL PRIMARY KEY,
    wafer_id    INTEGER NOT NULL UNIQUE REFERENCES wafers(wafer_id),
    lot_id      INTEGER NOT NULL REFERENCES lots(lot_id),
    total_die   INTEGER NOT NULL,
    good_die    INTEGER NOT NULL,
    yield_pct   REAL    NOT NULL,
    test_time   TEXT    NOT NULL
);

CREATE TABLE die_bins (
    wafer_id  INTEGER NOT NULL REFERENCES wafers(wafer_id),
    die_x     INTEGER NOT NULL,
    die_y     INTEGER NOT NULL,
    bin_code  TEXT    NOT NULL,
    PRIMARY KEY (wafer_id, die_x, die_y)
);
"""


@dataclass(frozen=True)
class ObservableDataset:
    """The observable plane of one dataset, as rows, before it is a file.

    Held as plain tuples keyed by table name so that the projection can be
    tested, digested and compared without a database in the way — and so that
    "the emitter writes what the projection produced" is one assertion rather
    than a round trip.
    """

    tables: Mapping[str, tuple[tuple[Any, ...], ...]]

    def rows(self, table: str) -> tuple[tuple[Any, ...], ...]:
        return self.tables[table]

    def row_counts(self) -> dict[str, int]:
        return {name: len(self.tables[name]) for name in SCHEMA_TABLES}

    def content_sha256(self) -> str:
        return content_sha256(self.tables)


# ------------------------------------------------------------ the identity


def _encode(value: Any) -> str:
    """One value, type-tagged, in the normalized form A1 §2 specifies.

    Integers exact, floats by shortest round-trip repr, NULL distinct from the
    empty string, text in NFC. The tag is what keeps `1`, `1.0` and `"1"` three
    different values: without it a digest would claim two schemas agree because
    SQLite happened to widen an integer.
    """
    if value is None:
        return "N:"
    if isinstance(value, bool):                     # before int: bool is an int
        return f"I:{int(value)}"
    if isinstance(value, int):
        return f"I:{value}"
    if isinstance(value, float):
        return f"R:{value!r}"
    return "T:" + unicodedata.normalize("NFC", str(value))


def content_sha256(tables: Mapping[str, Sequence[Sequence[Any]]]) -> str:
    """The portable dataset identity (`PHASE_1_ACCEPTANCE.md` A1 §2).

    Tables in **name** order — not schema order, so the digest cannot inherit
    a creation-order decision — rows in primary-key order, columns in schema
    order, values type-tagged. This is what CI compares across operating
    systems and SQLite versions, and unlike a byte compare it can name the
    table and the row that diverged.
    """
    digest = hashlib.sha256()
    digest.update(b"fabsim.observable/v1\n")
    for table in sorted(tables):
        header = "\t".join(SCHEMA_COLUMNS[table])
        digest.update(f"#{table}\t{header}\n".encode("utf-8"))
        for row in _in_key_order(table, tables[table]):
            digest.update("\t".join(_encode(v) for v in row).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _in_key_order(table: str, rows: Sequence[Sequence[Any]]
                  ) -> list[Sequence[Any]]:
    positions = [SCHEMA_COLUMNS[table].index(key) for key in SCHEMA_KEYS[table]]
    return sorted(rows, key=lambda row: tuple(row[i] for i in positions))


# ------------------------------------------------------------ the projection


class _Clock:
    """Minutes on the simulated clock → wall-clock text, and nothing else.

    The one place a timestamp is formatted. `time_origin` comes from the world
    template, so the text is a function of declared data: no `datetime.now()`,
    no locale, no timezone. Seconds resolution, because the clock's resolution
    is minutes (`TEMPORAL_MODEL.md` §1) and a fabricated sub-minute component
    would be precision the simulator does not have.
    """

    def __init__(self, origin: datetime) -> None:
        self.origin = origin

    def at(self, minutes: int | None) -> str | None:
        if minutes is None:
            return None
        return (self.origin + timedelta(minutes=int(minutes))).isoformat(
            sep=" ", timespec="seconds")


def _maintenance_description(maint_type: str, action_code: str) -> str:
    """Templated from the action code, fab-wide (`SCHEMA_V2_DESIGN.md` §2.18).

    Every window of a given type and action code gets the identical string, so
    the text carries exactly what the two coded columns already carry and
    cannot become a channel for prose that names a cause. It exists because a
    real MES record has a description field, and because a later consumer
    reading free text must find nothing in it.
    """
    return f"{maint_type} {action_code}"


def project(timeline: Timeline, observations: ProcessObservations,
            defects: DefectPopulation, die: DiePopulation,
            alarms: Sequence[Any] = (), *,
            dataset_id: str, fabsim_version: str,
            schema_version: str) -> ObservableDataset:
    """Project one realized world into schema v2 rows. Pure; no I/O, no draws.

    Five observable collections in, twenty-two tables out. The hidden plane is
    not a parameter, so the projection cannot read a latent value, a mechanism,
    a defect's origin or a die's kill cause — the boundary is the signature,
    the same way it is for `fabsim.die.probe`.

    `alarms` is the response layer's observable collection. `Alarm` has no
    field for a cause (`GROUND_TRUTH_CONTRACT.md` §3.1); the hidden
    `alarm_details` that sits beside it is not a parameter here and is not
    reachable from this module.
    """
    world: World = timeline.world
    clock = _Clock(world.time_origin)
    tables: dict[str, tuple[tuple[Any, ...], ...]] = {}

    tables["dataset_meta"] = ((schema_version, fabsim_version, dataset_id,
                               clock.at(0), timeline.horizon_days),)

    tables["process_flows"] = tuple(
        (flow.flow_id, flow.flow_name) for flow in world.process_flows)

    tables["process_steps"] = tuple(
        (step.step_id, step.step_name, step.operation_type,
         int(step.is_inspection)) for step in world.process_steps)

    tables["flow_steps"] = tuple(
        (fs.flow_step_id, fs.flow_id, fs.step_id, fs.step_sequence)
        for fs in world.flow_steps)

    tables["products"] = tuple(
        (p.product_id, p.product_name, p.flow_id, p.technology_node_nm,
         p.wafer_size_mm, p.die_size_mm2, p.target_yield_pct)
        for p in world.products)

    tables["recipes"] = tuple(
        (r.recipe_id, r.step_id, r.product_id, r.recipe_name, r.version,
         r.metric_name, r.metric_target, r.metric_usl, r.metric_lsl)
        for r in world.recipes)

    tables["recipe_settings"] = tuple(
        (recipe.recipe_id, name, value)
        for recipe in world.recipes
        for name, value in recipe.settings)

    tables["tools"] = tuple(
        (t.tool_id, t.tool_name, t.tool_type, t.vendor,
         t.install_date.isoformat(), t.location_bay) for t in world.tools)

    tables["chambers"] = tuple(
        (c.chamber_id, c.tool_id, c.chamber_name, c.install_date.isoformat())
        for c in world.chambers)

    tables["operators"] = tuple(
        (o.operator_id, o.operator_name, o.shift, o.certification_level)
        for o in world.operators)

    tables["lots"] = tuple(
        (lot.lot_id, lot.lot_number, lot.product_id, clock.at(lot.release_min),
         clock.at(lot.finish_min), lot.status, lot.wafer_count)
        for lot in timeline.lots)

    tables["wafers"] = tuple(
        (w.wafer_id, w.lot_id, w.slot_number, w.status)
        for w in timeline.wafers)

    tables["runs"] = tuple(
        (r.run_id, r.wafer_id, r.flow_step_id, r.tool_id, r.chamber_id,
         r.recipe_id, r.operator_id, clock.at(r.start_min),
         clock.at(r.end_min))
        for r in timeline.runs)

    tables["run_measurements"] = tuple(
        (m.run_meas_id, m.run_id, m.param_name, m.value, m.unit, m.set_value)
        for m in observations.run_measurements)

    tables["metrology"] = tuple(
        (m.metrology_id, m.wafer_id, m.flow_step_id, m.metrology_tool_id,
         clock.at(m.meas_time_min), m.param_name, m.value, m.unit)
        for m in observations.metrology)

    tables["tool_states"] = tuple(
        (s.state_id, s.tool_id, s.chamber_id, s.state, clock.at(s.start_min),
         clock.at(s.end_min)) for s in timeline.states)

    tables["alarms"] = tuple(
        (a.alarm_id, a.tool_id, a.chamber_id, clock.at(a.minute), a.code,
         a.severity, a.message) for a in alarms)

    tables["maintenance"] = tuple(
        (m.maint_id, m.tool_id, m.chamber_id, m.maint_type,
         clock.at(m.start_min), clock.at(m.end_min), m.technician,
         m.action_code, _maintenance_description(m.maint_type, m.action_code))
        for m in timeline.maintenance)

    tables["inspections"] = tuple(
        (i.inspection_id, i.wafer_id, i.flow_step_id, i.inspection_tool_id,
         clock.at(i.inspection_time_min), i.total_defect_count,
         i.scan_area_mm2) for i in defects.inspections)

    tables["defects"] = tuple(
        (d.defect_id, d.inspection_id, d.wafer_id, d.x_mm, d.y_mm, d.size_um,
         d.classified_type, d.layer) for d in defects.defects)

    tables["wafer_yield"] = tuple(
        (y.yield_id, y.wafer_id, y.lot_id, y.total_die, y.good_die,
         y.yield_pct, clock.at(y.test_time_min)) for y in die.wafer_yield)

    tables["die_bins"] = tuple(
        (b.wafer_id, b.die_x, b.die_y, b.bin_code) for b in die.die_bins)

    return ObservableDataset(tables={
        name: tuple(_in_key_order(name, tables[name]))
        for name in SCHEMA_TABLES})


# ---------------------------------------------------------------- the files


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def write_sql_dump(dataset: ObservableDataset, path: Path) -> None:
    """A portable, deterministic `.sql` dump — byte-comparable across hosts.

    Written here rather than by `sqlite3.iterdump()`, whose statement order
    follows `sqlite_master` and is therefore a property of the storage engine
    rather than of the data. Tables in schema order, rows in primary-key
    order, one statement per row, LF endings, UTF-8, no preamble and no clock.
    """
    lines: list[str] = [
        "-- fabsim observable plane, schema v2. Deterministic dump: tables in",
        "-- schema order, rows in primary-key order, no environment and no",
        "-- wall clock. Byte-comparable across hosts (A1 §3).",
        "PRAGMA foreign_keys = ON;",
        "BEGIN TRANSACTION;",
        "",
        OBSERVABLE_DDL.strip(),
        "",
    ]
    # Dependency order, not §2 order: the dump declares `PRAGMA foreign_keys
    # = ON` and is meant to be replayable into an empty database, so a child
    # row may not be inserted before its parent. A test replays it and
    # compares the content hash, which is what caught this.
    for table in INSERT_ORDER:
        rows = dataset.rows(table)
        if not rows:
            continue
        columns = ", ".join(SCHEMA_COLUMNS[table])
        lines.append(f"-- {table}: {len(rows)} row(s)")
        for row in rows:
            values = ", ".join(_sql_literal(v) for v in row)
            lines.append(f"INSERT INTO {table} ({columns}) VALUES ({values});")
        lines.append("")
    lines.append("COMMIT;")
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def write_sqlite(dataset: ObservableDataset, path: Path) -> None:
    """Create the database and fill it. Foreign keys on, so §4.1 is enforced."""
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(OBSERVABLE_DDL)
        for table in INSERT_ORDER:
            rows = dataset.rows(table)
            if not rows:
                continue
            columns = SCHEMA_COLUMNS[table]
            placeholders = ", ".join("?" * len(columns))
            connection.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({placeholders})", rows)
        # The database must satisfy its own declared constraints before it is
        # handed to anybody, not merely have been filled without an exception.
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(f"foreign key violations: {violations[:5]}")
        connection.commit()
    finally:
        connection.close()


def emit_observable(timeline: Timeline, observations: ProcessObservations,
                    defects: DefectPopulation, die: DiePopulation,
                    alarms: Iterable[Any], directory: Path, *,
                    dataset_id: str, fabsim_version: str,
                    schema_version: str) -> ObservableDataset:
    """Write `fab.db` and `fab_database.sql` into `directory`.

    `alarms` is the observable alarm collection from the response layer —
    `Alarm` rows, which have no field for a cause (`GROUND_TRUTH_CONTRACT.md`
    §3.1). The hidden `alarm_details` beside them is not a parameter and is
    not reachable from here.
    """
    dataset = project(timeline, observations, defects, die, tuple(alarms),
                      dataset_id=dataset_id, fabsim_version=fabsim_version,
                      schema_version=schema_version)
    directory.mkdir(parents=True, exist_ok=True)
    write_sqlite(dataset, directory / "fab.db")
    write_sql_dump(dataset, directory / "fab_database.sql")
    return dataset
