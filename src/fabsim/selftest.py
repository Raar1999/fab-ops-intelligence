"""
selftest.py — the generation invariants, checked on every build.

`SCHEMA_V2_DESIGN.md` §4 lists four families of invariant that generated data
must satisfy, and `FABSIM_DESIGN.md` §4 stage 7 says a build that violates one
**fails** rather than shipping. This module is that stage. It is not a test
suite: the test suite checks that the simulator is right, and this checks that
*this dataset* is well formed before anybody is handed it.

    §4.1 referential      every FK resolves, and resolves to the right thing
    §4.2 clock            runs ordered, no run inside DOWN/PM, observations
                          follow what they observe, test follows the last run
    §4.3 reconciliation   defect counts, die-bin sums, state ribbons tile
    §4.4 vocabulary       every categorical value comes from the world

Two of these the database already enforces — foreign keys are declared and
`PRAGMA foreign_key_check` runs at write time — so what is left here is the
half SQLite cannot express: relationships between *values* (a run's recipe
must match its step and its wafer's product), the clock, the sums, and the
closed vocabularies.

It reads the **observable plane only**. A self-test that consulted the
realization could confirm a dataset against the thing that produced it rather
than against the contract, which is the same mistake as marking your own
homework; `check_dataset` takes the rows that were written and the world that
declared the vocabularies, and nothing else.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from fabsim.world import World

__all__ = ["SelfTestError", "check_dataset", "check_observable"]


class SelfTestError(AssertionError):
    """A generated dataset violated an invariant it is required to satisfy.

    An `AssertionError` subclass so a build failure reads the way a failed
    check reads, and a named type so a caller can tell "the dataset is wrong"
    from "the checker crashed".
    """

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        super().__init__(f"{rule}: {detail}")


def _require(rule: str, condition: bool, detail: str) -> None:
    if not condition:
        raise SelfTestError(rule, detail)


def _column(table: Sequence[Sequence[Any]], index: int) -> list[Any]:
    return [row[index] for row in table]


def check_observable(tables: Mapping[str, Sequence[Sequence[Any]]],
                     world: World) -> None:
    """Every §4 invariant, against the emitted rows and the declared world."""
    from fabsim.emit.observable import SCHEMA_COLUMNS, SCHEMA_TABLES

    def col(table: str, name: str) -> int:
        return SCHEMA_COLUMNS[table].index(name)

    def rows(table: str) -> Sequence[Sequence[Any]]:
        return tables[table]

    def values(table: str, name: str) -> list[Any]:
        return _column(rows(table), col(table, name))

    # -- 0. shape ---------------------------------------------------------
    _require("§2 shape", set(tables) == set(SCHEMA_TABLES),
             f"expected {len(SCHEMA_TABLES)} tables, got {sorted(tables)}")
    for table in SCHEMA_TABLES:
        width = len(SCHEMA_COLUMNS[table])
        bad = [r for r in rows(table) if len(r) != width]
        _require("§2 shape", not bad, f"{table}: row width != {width}")

    # -- 1. referential, beyond what the DDL can say ----------------------
    # SQLite enforces "the FK resolves"; these are the relationships between
    # values that a foreign key cannot express.
    chamber_tool = {r[col("chambers", "chamber_id")]:
                    r[col("chambers", "tool_id")] for r in rows("chambers")}
    for run in rows("runs"):
        chamber = run[col("runs", "chamber_id")]
        # A dangling id cannot reach `fab.db` — the DDL rejects it — but this
        # checker also runs over in-memory rows, where nothing has yet said no.
        _require("§4.1 referential", chamber in chamber_tool,
                 f"run {run[0]}: chamber {chamber} does not exist")
        _require("§4.1 referential",
                 chamber_tool[chamber] == run[col("runs", "tool_id")],
                 f"run {run[0]}: chamber {chamber} is not on its tool")

    flow_step = {r[col("flow_steps", "flow_step_id")]:
                 (r[col("flow_steps", "flow_id")],
                  r[col("flow_steps", "step_id")]) for r in rows("flow_steps")}
    recipe_key = {r[col("recipes", "recipe_id")]:
                  (r[col("recipes", "step_id")],
                   r[col("recipes", "product_id")]) for r in rows("recipes")}
    wafer_lot = {r[col("wafers", "wafer_id")]: r[col("wafers", "lot_id")]
                 for r in rows("wafers")}
    lot_product = {r[col("lots", "lot_id")]: r[col("lots", "product_id")]
                   for r in rows("lots")}
    for run in rows("runs"):
        for name, table in (("flow_step_id", flow_step),
                            ("recipe_id", recipe_key),
                            ("wafer_id", wafer_lot)):
            _require("§4.1 referential", run[col("runs", name)] in table,
                     f"run {run[0]}: {name} does not exist")
        _, step_id = flow_step[run[col("runs", "flow_step_id")]]
        product = lot_product[wafer_lot[run[col("runs", "wafer_id")]]]
        _require("§4.1 referential",
                 recipe_key[run[col("runs", "recipe_id")]] == (step_id,
                                                               product),
                 f"run {run[0]}: recipe does not match its step and product")

    # -- 2. the clock -----------------------------------------------------
    by_wafer: dict[int, list[tuple[int, str, str]]] = {}
    sequence = {r[col("flow_steps", "flow_step_id")]:
                r[col("flow_steps", "step_sequence")]
                for r in rows("flow_steps")}
    for run in rows("runs"):
        start = run[col("runs", "start_time")]
        end = run[col("runs", "end_time")]
        _require("§4.2 clock", start < end,
                 f"run {run[0]}: start {start} is not before end {end}")
        by_wafer.setdefault(run[col("runs", "wafer_id")], []).append(
            (sequence[run[col("runs", "flow_step_id")]], start, end))
    for wafer_id, entries in by_wafer.items():
        entries.sort()
        for (_s1, _a1, end1), (_s2, start2, _b2) in zip(entries, entries[1:]):
            _require("§4.2 clock", end1 <= start2,
                     f"wafer {wafer_id}: runs overlap in step order")

    blocking = {"DOWN", "PM"}
    windows: dict[int, list[tuple[str, str]]] = {}
    for state in rows("tool_states"):
        if state[col("tool_states", "state")] in blocking:
            windows.setdefault(state[col("tool_states", "chamber_id")],
                               []).append(
                (state[col("tool_states", "start_time")],
                 state[col("tool_states", "end_time")]))
    for run in rows("runs"):
        start = run[col("runs", "start_time")]
        end = run[col("runs", "end_time")]
        for block_start, block_end in windows.get(
                run[col("runs", "chamber_id")], ()):
            _require("§4.2 clock", end <= block_start or start >= block_end,
                     f"run {run[0]} overlaps a DOWN/PM window on its chamber")

    run_end = {(r[col("runs", "wafer_id")],
                r[col("runs", "flow_step_id")]): r[col("runs", "end_time")]
               for r in rows("runs")}
    last_run = {}
    for run in rows("runs"):
        wafer = run[col("runs", "wafer_id")]
        last_run[wafer] = max(last_run.get(wafer, ""),
                              run[col("runs", "end_time")])
    for row in rows("metrology"):
        key = (row[col("metrology", "wafer_id")],
               row[col("metrology", "flow_step_id")])
        _require("§4.2 clock",
                 key in run_end
                 and row[col("metrology", "meas_time")] >= run_end[key],
                 "metrology precedes the run it measures")
    for row in rows("inspections"):
        key = (row[col("inspections", "wafer_id")],
               row[col("inspections", "flow_step_id")])
        _require("§4.2 clock",
                 key in run_end
                 and row[col("inspections", "inspection_time")] >= run_end[key],
                 "an inspection precedes its own scan run")
    for row in rows("wafer_yield"):
        wafer = row[col("wafer_yield", "wafer_id")]
        _require("§4.2 clock",
                 row[col("wafer_yield", "test_time")] >= last_run[wafer],
                 f"wafer {wafer}: test time precedes its last run")
    for lot in rows("lots"):
        finish = lot[col("lots", "finish_time")]
        if finish is None:
            continue
        latest = max((last_run[w] for w, l in wafer_lot.items()
                      if l == lot[col("lots", "lot_id")] and w in last_run),
                     default=None)
        _require("§4.2 clock", latest is None or finish >= latest,
                 f"lot {lot[0]}: finish precedes its last activity")

    # -- 3. reconciliation ------------------------------------------------
    defect_counts: dict[int, int] = {}
    for defect in rows("defects"):
        key = defect[col("defects", "inspection_id")]
        defect_counts[key] = defect_counts.get(key, 0) + 1
    for inspection in rows("inspections"):
        declared = inspection[col("inspections", "total_defect_count")]
        actual = defect_counts.get(
            inspection[col("inspections", "inspection_id")], 0)
        _require("§4.3 reconciliation", declared == actual,
                 f"inspection {inspection[0]}: {declared} declared, "
                 f"{actual} defect rows")

    bins: dict[int, list[str]] = {}
    for row in rows("die_bins"):
        bins.setdefault(row[col("die_bins", "wafer_id")], []).append(
            row[col("die_bins", "bin_code")])
    for summary in rows("wafer_yield"):
        wafer = summary[col("wafer_yield", "wafer_id")]
        codes = bins.get(wafer, [])
        total = summary[col("wafer_yield", "total_die")]
        good = summary[col("wafer_yield", "good_die")]
        _require("§4.3 reconciliation", total == len(codes),
                 f"wafer {wafer}: total_die {total} != {len(codes)} die rows")
        _require("§4.3 reconciliation",
                 good == sum(1 for c in codes if c == "PASS"),
                 f"wafer {wafer}: good_die does not match PASS bins")
        _require("§4.3 reconciliation", 0 <= good <= total,
                 f"wafer {wafer}: good_die out of range")
        _require("§4.3 reconciliation",
                 abs(summary[col("wafer_yield", "yield_pct")]
                     - (100.0 * good / total)) < 1e-9,
                 f"wafer {wafer}: yield_pct is not good ÷ total")
    _require("§4.3 reconciliation",
             set(bins) == {s[col("wafer_yield", "wafer_id")]
                           for s in rows("wafer_yield")},
             "die_bins and wafer_yield disagree about which wafers were tested")

    ribbons: dict[int, list[tuple[str, str]]] = {}
    for state in rows("tool_states"):
        ribbons.setdefault(state[col("tool_states", "chamber_id")], []).append(
            (state[col("tool_states", "start_time")],
             state[col("tool_states", "end_time")]))
    for chamber_id, spans in ribbons.items():
        spans.sort()
        for (_a, end), (start, _b) in zip(spans, spans[1:]):
            _require("§4.3 reconciliation", end == start,
                     f"chamber {chamber_id}: state intervals do not tile")

    # -- 4. vocabulary closure --------------------------------------------
    maintenance = world.maintenance
    closed: list[tuple[str, str, set[str]]] = [
        ("tool_states", "state", {"PRODUCTIVE", "IDLE", "DOWN", "PM", "QUAL"}),
        ("alarms", "alarm_code", {c.code for c in world.alarms.codes}),
        ("alarms", "severity", set(world.alarms.severities)),
        ("alarms", "message", {c.message for c in world.alarms.codes}),
        ("maintenance", "maint_type", {"PM", "UNSCHEDULED"}),
        ("maintenance", "technician", set(maintenance.technicians)),
        ("maintenance", "action_code", set(maintenance.pm_action_codes)
         | set(maintenance.unscheduled_action_codes)),
        ("defects", "classified_type",
         set(world.observation.classifier.classes)),
        ("defects", "layer", set(world.layers)),
        ("die_bins", "bin_code", set(world.die_kill.tester.codes)),
        ("process_steps", "operation_type", set(world.operation_types)),
        ("operators", "shift", {o.shift for o in world.operators}),
    ]
    for table, name, vocabulary in closed:
        seen = set(values(table, name))
        _require("§4.4 vocabulary", seen <= vocabulary,
                 f"{table}.{name}: minted value(s) {sorted(seen - vocabulary)}")

    # -- 5. the boundary --------------------------------------------------
    # Cheap, and it runs on every build: no column of the observable plane may
    # be named after something only the hidden plane knows.
    forbidden = {"origin", "mechanism", "event_id", "scenario", "severity_sigma",
                 "fault", "suspect", "truth", "killer_flag", "is_affected",
                 "latent", "cause", "counterfactual"}
    for table in SCHEMA_TABLES:
        overlap = {c for c in SCHEMA_COLUMNS[table] if c in forbidden}
        _require("anti-leakage", not overlap,
                 f"{table}: column(s) {sorted(overlap)} name hidden state")


def check_dataset(dataset: Any) -> None:
    """Self-test one built dataset. Raises `SelfTestError` on a violation."""
    check_observable(dataset.observable.tables, dataset.response.world)
