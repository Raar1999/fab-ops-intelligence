"""
Invariant tests for `fabsim.emit` — the two planes, and the wall between them.

The emission layer is where the answer-blindness of everything upstream either
survives contact with a file or does not. So most of what is pinned here is
*separation*: the observable database has no column a hidden fact could live
in, rewriting the hidden plane cannot change a byte of the observable one,
`fabops` can open the database and cannot reach the truth beside it, and the
only thing the two planes share is a `dataset_id`.

The rest is reproducibility, which A1 defines precisely enough to test
literally: the same five inputs give the same content hash, the same truth
bytes and the same manifest apart from its clock, and moving any one input
moves the identity.

Nothing here writes into the repository. Every build goes to a `tmp_path`, and
one test checks that the repository has no emitted dataset in it at all.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fabsim import SCHEMA_VERSION, __version__
from fabsim.emit import DATASET_ROOT, build_dataset, realize_dataset
from fabsim.emit.manifest import MANIFEST_SCHEMA, VOLATILE_FIELDS, stable_view
from fabsim.emit.observable import (
    INSERT_ORDER,
    OBSERVABLE_DDL,
    SCHEMA_COLUMNS,
    SCHEMA_KEYS,
    SCHEMA_TABLES,
    content_sha256,
    project,
)
from fabsim.emit.truth import TRUTH_SCHEMA, build_truth
from fabsim.scenario import from_mapping
from fabsim.selftest import SelfTestError, check_observable

#: Small on purpose: emission writes a die-bin row per die per tested wafer, so
#: a full 84-day build is ~1M rows. Twenty days and three lots still exercises
#: every one of the 22 tables — a test asserts that — at a fifth of the bytes.
REFERENCE: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "emission-reference",
    "world": "baseline_fab_v1",
    "horizon_days": 20,
    "lots": 3,
    "default_seed": 42,
}

EDGE_EVENT: dict[str, Any] = {
    "mechanism": "chamber_edge_uniformity",
    "target": {"tool": "ETCH-02", "chamber": "B"},
    "onset_day": 6,
    "profile": {"type": "ramp", "ramp_days": 4},
    "severity": "obvious",
}

PINNED_CLOCK = "2026-01-01T00:00:00+00:00"


def config(**overrides: Any):
    return from_mapping({**REFERENCE, **overrides})


@pytest.fixture(scope="module")
def built(world, tmp_path_factory):
    """One faulted dataset, built once. The whole pipeline runs here."""
    directory = tmp_path_factory.mktemp("faulted")
    return build_dataset(config(events=[EDGE_EVENT]), world=world,
                         root=directory, created_at=PINNED_CLOCK)


@pytest.fixture(scope="module")
def null_built(world, tmp_path_factory):
    directory = tmp_path_factory.mktemp("null")
    return build_dataset(config(), world=world, root=directory,
                         created_at=PINNED_CLOCK)


def query(db_path: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# ------------------------------------------------------------------- layout


def test_the_dataset_layout_is_the_one_the_contract_states(built):
    """`GROUND_TRUTH_CONTRACT.md` §2, exactly — including the subdirectory.

    The hidden plane is one directory down, which is what makes the boundary
    auditable with a directory listing rather than with a code review: a
    consumer is handed `fab.db`, and `truth/` is visibly not part of it.
    """
    emitted = sorted(p.relative_to(built.directory).as_posix()
                     for p in built.directory.rglob("*") if p.is_file())
    assert emitted == ["fab.db", "fab_database.sql", "manifest.json",
                       "truth/truth.json"]
    assert built.directory.name == built.dataset_id
    assert re.fullmatch(r"scn-[0-9a-f]{12}-s\d+", built.dataset_id)
    assert built.db_path.exists() and built.truth_path.exists()


def test_the_directory_name_discloses_nothing(built):
    """Anti-leakage D5: opaque ids, and the slug lives only in the truth."""
    haystack = built.directory.name.lower()
    for token in ("etch", "chamber", "uniformity", "edge", "emission",
                  "reference", "obvious", "fault"):
        assert token not in haystack
    assert built.truth["scenario_name"] == "emission-reference"


def test_nothing_is_emitted_into_the_repository():
    """Tests build into `tmp_path`; the suite adds nothing to the default root.

    It used to assert the default root was *empty*, which is a fact about the
    machine rather than about the suite: anyone who had built a dataset — the
    product's ordinary first action since ADR-037 — failed it. The intent is
    that the **suite** never writes 120 MB into the repository, and that is
    what is measured now, against a snapshot taken before collection.
    """
    from tests.conftest import datasets_added_during_the_session

    assert datasets_added_during_the_session() == []
    repository = Path(__file__).resolve().parents[2]
    assert not list(repository.glob("truth.json"))
    assert not list((repository / "src").rglob("*.db"))
    assert DATASET_ROOT == repository / "data" / "scenarios", (
        "the emitter's default root moved; the snapshot watches the old one")


def test_the_legacy_artifacts_are_untouched():
    """ADR-010: the v1 demonstration keeps working, byte for byte."""
    repository = Path(__file__).resolve().parents[2]
    legacy = repository / "data" / "fab.db"
    assert legacy.exists()
    names = {r[0] for r in query(legacy, "SELECT name FROM sqlite_master "
                                         "WHERE type='table'")}
    assert "run_history" in names and "yield_data" in names   # schema v1
    assert "runs" not in names and "die_bins" not in names    # not v2


# ------------------------------------------------------------- schema shape


def test_the_emitter_covers_the_twenty_two_declared_tables(built):
    """§2.1–2.22, and no table the schema does not declare."""
    assert len(SCHEMA_TABLES) == 22
    assert set(SCHEMA_TABLES) == set(SCHEMA_COLUMNS) == set(SCHEMA_KEYS)
    assert set(INSERT_ORDER) == set(SCHEMA_TABLES)
    in_db = {r[0] for r in query(built.db_path, "SELECT name FROM sqlite_master"
                                                " WHERE type='table'")}
    assert in_db == set(SCHEMA_TABLES)


def test_every_table_has_rows_in_the_reference_build(built):
    """A tiny build that left a table empty would test the emitter for it
    not at all."""
    counts = built.observable.row_counts()
    empty = sorted(name for name, count in counts.items() if count == 0)
    assert empty == [], empty


def test_the_declared_column_order_is_what_the_database_has(built):
    for table in SCHEMA_TABLES:
        columns = [r[1] for r in query(built.db_path,
                                       f"PRAGMA table_info({table})")]
        assert tuple(columns) == SCHEMA_COLUMNS[table], table


def test_the_insert_order_satisfies_every_declared_foreign_key():
    """Read back out of the DDL, so the ordering constant cannot drift from
    the constraints it exists to satisfy."""
    created = re.findall(r"CREATE TABLE (\w+)", OBSERVABLE_DDL)
    assert tuple(created) == INSERT_ORDER
    seen: set[str] = set()
    for table in INSERT_ORDER:
        body = OBSERVABLE_DDL.split(f"CREATE TABLE {table} (")[1].split(");")[0]
        for target in re.findall(r"REFERENCES (\w+)\(", body):
            assert target in seen or target == table, (table, target)
        seen.add(table)


def test_foreign_keys_are_declared_and_enforced(built):
    """§4.1 is the database's job, not a check that runs afterwards."""
    connection = sqlite3.connect(str(built.db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO runs (run_id, wafer_id, flow_step_id, tool_id, "
                "chamber_id, recipe_id, operator_id, start_time, end_time) "
                "VALUES (999999, 999999, 1, 1, 1, 1, 1, 'x', 'y')")
    finally:
        connection.close()


def test_the_sql_dump_replays_into_the_same_dataset(built, tmp_path):
    """§3 of A1: the text artifact is the portable one, and it is complete."""
    replayed = tmp_path / "replayed.db"
    connection = sqlite3.connect(str(replayed))
    try:
        connection.executescript(
            (built.directory / "fab_database.sql").read_text(encoding="utf-8"))
    finally:
        connection.close()

    rebuilt = {}
    for table in SCHEMA_TABLES:
        columns = ", ".join(SCHEMA_COLUMNS[table])
        rebuilt[table] = [tuple(r) for r in
                          query(replayed, f"SELECT {columns} FROM {table}")]
    assert content_sha256(rebuilt) == built.observable.content_sha256()


# ---------------------------------------------------------- reproducibility


def test_the_same_inputs_give_the_same_dataset(world, tmp_path):
    """A1: same config + world + seed + versions ⇒ the same content."""
    first = build_dataset(config(events=[EDGE_EVENT]), world=world,
                          root=tmp_path / "a", created_at=PINNED_CLOCK)
    second = build_dataset(config(events=[EDGE_EVENT]), world=world,
                           root=tmp_path / "b", created_at=PINNED_CLOCK)

    assert first.observable.content_sha256() == second.observable.content_sha256()
    assert (first.directory / "fab_database.sql").read_bytes() == \
        (second.directory / "fab_database.sql").read_bytes()
    assert first.truth_path.read_bytes() == second.truth_path.read_bytes()
    assert first.manifest == second.manifest
    assert first.identity.build_fingerprint == second.identity.build_fingerprint


def test_only_the_clock_differs_between_two_builds(world, tmp_path):
    """`created_at` is the one wall-clock value, and it is in no hash."""
    first = build_dataset(config(), world=world, root=tmp_path / "a")
    second = build_dataset(config(), world=world, root=tmp_path / "b")
    assert stable_view(first.manifest) == stable_view(second.manifest)
    assert VOLATILE_FIELDS == ("created_at",)
    differing = {k for k in first.manifest
                 if first.manifest[k] != second.manifest[k]}
    assert differing <= {"created_at"}


@pytest.mark.parametrize("dimension", ["seed", "config", "world"])
def test_moving_one_input_moves_the_identity(world, tmp_path, dimension,
                                             make_template):
    """One dimension at a time, as A1's four checks require."""
    from fabsim.world import build_world

    baseline = build_dataset(config(), world=world, root=tmp_path / "base",
                             created_at=PINNED_CLOCK)
    if dimension == "seed":
        other = build_dataset(config(), 101, world=world,
                              root=tmp_path / "x", created_at=PINNED_CLOCK)
        assert other.identity.dataset_id != baseline.identity.dataset_id
    elif dimension == "config":
        other = build_dataset(config(lots=4), world=world,
                              root=tmp_path / "x", created_at=PINNED_CLOCK)
        assert other.identity.scenario_id != baseline.identity.scenario_id
    else:
        template = make_template()
        template["die_kill"]["parametric"]["kill_limit_tolerances"] = 2.75
        other = build_dataset(config(), world=build_world(template),
                              root=tmp_path / "x", created_at=PINNED_CLOCK)
        # A different world is the same *dataset id* built differently, which
        # is exactly why the fingerprint has to carry the world.
        assert other.identity.dataset_id == baseline.identity.dataset_id
        assert other.identity.world_sha256 != baseline.identity.world_sha256

    assert other.identity.build_fingerprint != baseline.identity.build_fingerprint
    assert other.observable.content_sha256() != \
        baseline.observable.content_sha256()
    assert other.truth_path.read_bytes() != baseline.truth_path.read_bytes()


_PROBE = """
import sys, tempfile, pathlib
from fabsim.emit import build_dataset
from fabsim.scenario import from_mapping
config = from_mapping({
    "fabsim": "scenario/v1", "name": "emission-reference",
    "world": "baseline_fab_v1", "horizon_days": 20, "lots": 3,
    "default_seed": 42,
})
with tempfile.TemporaryDirectory() as tmp:
    dataset = build_dataset(config, root=pathlib.Path(tmp), created_at="pinned")
    print(dataset.observable.content_sha256())
    print(dataset.truth_path.read_bytes().hex()[:64])
"""


def test_emission_does_not_depend_on_the_process_it_ran_in(tmp_path):
    """No locale, no timezone, no hash salt, no cwd."""
    outputs = []
    for name, hash_seed, extra in (("a", "0", {"LANG": "C", "TZ": "UTC"}),
                                   ("b", "999", {"LANG": "de_DE.UTF-8",
                                                 "TZ": "Asia/Tokyo"})):
        directory = tmp_path / name
        directory.mkdir()
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env.update(extra)
        outputs.append(subprocess.run(
            [sys.executable, "-c", _PROBE], cwd=str(directory), env=env,
            capture_output=True, text=True, check=True).stdout)
    assert outputs[0] == outputs[1]


# -------------------------------------------------------------- T1: no truth


#: Names that would mean the observable plane had learned something only the
#: generator knows. Checked against columns *and* against every text value.
FORBIDDEN = (
    "fault", "suspect", "mechanism", "scenario", "truth", "ground_truth",
    "origin", "counterfactual", "latent", "severity_sigma", "killer",
    "is_affected", "is_faulted", "event_id", "expected", "injected",
)


#: Column names that contain a forbidden token but mean something else. Two
#: unrelated things are spelled `origin` in this project — a defect's hidden
#: *physical* origin, which must never be observable, and the clock's
#: `time_origin`, which is provenance §2.1 requires. Listing the exception is
#: the honest way to keep the scan: dropping the token would stop it catching
#: the thing it exists for.
COLUMN_EXCEPTIONS = {("dataset_meta", "time_origin")}


def test_no_observable_column_names_a_hidden_fact(built):
    """T1, on the schema."""
    for table in SCHEMA_TABLES:
        columns = [r[1] for r in query(built.db_path,
                                       f"PRAGMA table_info({table})")]
        for column in columns:
            if (table, column) in COLUMN_EXCEPTIONS:
                continue
            for token in FORBIDDEN:
                # `severity` alone is legitimate on `alarms` — it is the
                # alarm's own coded severity, fab-wide vocabulary. What is
                # forbidden is a *fault* severity, which is why the token is
                # `severity_sigma` and this loop is over exact names.
                assert token not in column.lower(), (table, column, token)


def test_no_observable_value_names_a_hidden_fact(built):
    """T1, on the data — every text value of every row of every table.

    The stronger half: a column called `description` could carry a mechanism
    name and no column scan would notice.
    """
    scenario_words = {"edge_uniformity", "chamber_edge_uniformity",
                      "param_drift", "particle_excursion", "benign_offset",
                      "emission-reference", "obvious", "moderate", "subtle"}
    for table in SCHEMA_TABLES:
        for row in built.observable.rows(table):
            for value in row:
                if not isinstance(value, str):
                    continue
                lowered = value.lower()
                for word in scenario_words:
                    assert word not in lowered, (table, value, word)


def test_the_maintenance_description_is_templated_from_its_codes(built):
    """§2.18: fab-wide text, so the free-text column carries no prose.

    Every window with the same type and action code has the identical
    description, which means the column adds no information the two coded
    columns did not already carry — and therefore cannot leak any.
    """
    columns = SCHEMA_COLUMNS["maintenance"]
    seen: dict[tuple[str, str], set[str]] = {}
    for row in built.observable.rows("maintenance"):
        key = (row[columns.index("maint_type")],
               row[columns.index("action_code")])
        seen.setdefault(key, set()).add(row[columns.index("description")])
    assert seen
    for key, descriptions in seen.items():
        assert len(descriptions) == 1, key


def test_the_observable_plane_carries_no_seed_column(built):
    """§2.1: the seed lives in the manifest and inside the opaque id, not as a
    first-class column."""
    for table in SCHEMA_TABLES:
        columns = {r[1] for r in query(built.db_path,
                                       f"PRAGMA table_info({table})")}
        assert "seed" not in columns, table
    meta = query(built.db_path, "SELECT * FROM dataset_meta")
    assert len(meta) == 1


# ---------------------------------------------------------- T2: truth is more


def test_the_truth_carries_what_the_observable_plane_cannot(built):
    """T2, against `GROUND_TRUTH_CONTRACT.md` §3."""
    truth = built.truth
    assert truth["schema"] == TRUTH_SCHEMA
    assert set(truth) >= {"dataset_id", "scenario_id", "scenario_name",
                          "config_sha256", "seed", "fabsim_version",
                          "schema_version", "events", "distractors",
                          "latent_summaries"}
    (event,) = truth["events"]
    assert set(event) >= {"event_id", "mechanism", "target", "onset", "end",
                          "profile", "severity", "severity_realized",
                          "causal_chain", "alarms_emitted",
                          "maintenance_response", "affected_runs",
                          "affected_wafers", "expected_impact"}
    assert event["mechanism"] == "chamber_edge_uniformity"
    assert event["target"]["tool"] == "ETCH-02"
    assert event["affected_runs"] and event["affected_wafers"]
    assert truth["latent_summaries"]

    # …and none of it is anywhere in the database.
    for table in SCHEMA_TABLES:
        for row in built.observable.rows(table):
            assert "chamber_edge_uniformity" not in [
                v for v in row if isinstance(v, str)]


def test_the_truth_records_the_realization_and_not_the_intent(built):
    """§10: realized, not configured.

    `severity` is the configured level and stays a label; `severity_realized`
    is what the latent plane actually produced, and the two are different
    kinds of number. The affected sets are the runs that really happened.
    """
    (event,) = built.truth["events"]
    assert event["severity"] == "obvious"
    realized = event["severity_realized"]["aggregate_shift_sigma"]
    assert isinstance(realized, float) and realized > 0.0
    assert realized != 6.0                      # not the configured ladder

    chambers = set(event["target"]["chamber_ids"])
    runs = {r.run_id: r for r in built.response.timeline.runs}
    for run_id in event["affected_runs"]:
        assert runs[run_id].chamber_id in chambers
    for entry in event["affected_wafers"]:
        assert 0.0 < entry["exposure"] <= 1.0


def test_the_truth_lists_the_distractors_it_must_be_scored_against(built):
    """The contract's own emphasis: false attribution has to be scorable."""
    kinds = [d["kind"] for d in built.truth["distractors"]]
    assert "benign_offset_baseline" in kinds
    standing = next(d for d in built.truth["distractors"]
                    if d["kind"] == "benign_offset_baseline")
    assert standing["declared"] is False
    assert standing["chamber_count"] == len(built.response.world.chambers)
    assert set(standing["latents"]) == set(
        built.response.world.observation.latents)


def test_a_declared_distractor_reaches_the_truth(world, tmp_path):
    dataset = build_dataset(
        config(distractors=[{"mechanism": "benign_offset",
                             "target": {"tool": "CVD-01"},
                             "magnitude": "large"}]),
        world=world, root=tmp_path, created_at=PINNED_CLOCK)
    declared = [d for d in dataset.truth["distractors"] if d["declared"]]
    assert len(declared) == 1
    assert declared[0]["kind"] == "benign_offset"
    assert declared[0]["target"]["tool"] == "CVD-01"
    assert declared[0]["added"]


def test_a_null_dataset_has_an_empty_answer_key_that_still_answers(null_built):
    """§3: "the null scenario emits `events: []` with distractors populated —
    an empty answer key is still an answer key"."""
    assert null_built.truth["events"] == []
    assert null_built.truth["distractors"]
    assert null_built.truth["latent_summaries"] == {}
    assert null_built.observable.row_counts()["wafer_yield"] > 0


def test_the_truth_is_canonical_json(built):
    text = built.truth_path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == built.truth
    assert json.dumps(built.truth, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n" == text


# ------------------------------------------------------- T5/T6: the direction


def test_rewriting_the_hidden_origin_changes_no_observable_byte(built, world):
    """T5, the critical one.

    Every hidden record is rewritten — the defect's physical origin, the die's
    kill cause — and the observable projection is recomputed from the same
    inputs. It must come out byte-identical, because the projection was never
    given a way to read any of it.
    """
    doctored_defects = replace(built.defects, origins=tuple(
        replace(o, origin="scratch", contributing_chamber_id=999,
                contributing_flow_step_id=999)
        for o in built.defects.origins))
    doctored_die = replace(built.die, outcomes=tuple(
        replace(o, cause="parametric", p_background=0.5, p_defect=0.5,
                p_parametric=0.5, defect_ids=())
        for o in built.die.outcomes))
    assert doctored_defects.origins != built.defects.origins
    assert doctored_die.outcomes != built.die.outcomes

    again = project(built.response.timeline, built.observations,
                    doctored_defects, doctored_die, built.response.alarms,
                    dataset_id=built.dataset_id,
                    fabsim_version=__version__, schema_version=SCHEMA_VERSION)
    assert again.content_sha256() == built.observable.content_sha256()
    assert again.tables == built.observable.tables


def test_rewriting_an_observable_value_does_change_the_output(built):
    """T6: the mirror, so T5 is not passing for the wrong reason.

    If the projection were inert the previous test would pass trivially. One
    changed *observable* defect coordinate must move the content hash.
    """
    moved = replace(built.defects, defects=tuple(
        (replace(d, x_mm=d.x_mm + 1.0) if index == 0 else d)
        for index, d in enumerate(built.defects.defects)))
    again = project(built.response.timeline, built.observations,
                    moved, built.die, built.response.alarms,
                    dataset_id=built.dataset_id,
                    fabsim_version=__version__, schema_version=SCHEMA_VERSION)
    assert again.content_sha256() != built.observable.content_sha256()


# --------------------------------------------------------- T3/T4: the wall


def _module(name: str) -> Path:
    return (Path(__file__).resolve().parents[2] / "src" / "fabsim" / "emit"
            / name)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_observable_emitter_cannot_reach_the_hidden_plane():
    """T4, structurally: the import graph and the signature.

    `fabsim.emit.observable` does not import the truth emitter, the latent
    plane or the response layer, and `project` has no `Realization`
    parameter — so a hidden value has no route into a row even by mistake.
    """
    import inspect

    imports = _imports(_module("observable.py"))
    assert "fabsim.emit.truth" not in imports
    assert "fabsim.latent" not in imports
    assert "fabsim.response" not in imports
    assert not any(name.startswith("fabops") for name in imports)

    parameters = list(inspect.signature(project).parameters)
    assert parameters == ["timeline", "observations", "defects", "die",
                          "alarms", "dataset_id", "fabsim_version",
                          "schema_version"]
    identifiers = {n.attr for n in ast.walk(
        ast.parse(_module("observable.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.Attribute)}
    for forbidden in ("realization", "origins", "origin_of", "outcomes",
                      "mechanisms", "distractors", "counterfactual",
                      "trajectories", "alarm_details", "resets", "detail"):
        assert forbidden not in identifiers, forbidden


def test_the_truth_emitter_never_writes_the_observable_plane():
    """§3 of the gate: truth may not enrich the dataset it sits beside."""
    imports = _imports(_module("truth.py"))
    assert "fabsim.emit.observable" not in imports
    # Identifiers and code strings, never the docstring: this module's prose
    # explains where the observable plane sits, and saying so is not a path
    # to it — the same exclusion every other scan in this project makes.
    tree = ast.parse(_module("truth.py").read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)
                  and isinstance(n.body[0].value.value, str)}
    code = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for token in ("fab.db", "fab_database.sql"):
        assert token not in code, token
    for token in ("sqlite3", "OBSERVABLE_DDL", "write_sqlite", "project"):
        assert token not in names, token


def test_fabops_cannot_import_or_reach_the_emitters():
    """T3: the code-plane lint, aimed at the new package.

    ADR-013's read discipline in its enforceable form — `fabops` may not
    import `fabsim`, and may not name the paths the hidden plane lives at.
    """
    repository = Path(__file__).resolve().parents[2]
    forbidden_text = ("truth.json", "truth/", "scenarios/", "fabsim")
    for root in ("src/fabops", "app"):
        for module in sorted((repository / root).rglob("*.py")):
            if "__pycache__" in module.parts:
                continue
            source = module.read_text(encoding="utf-8")
            for token in forbidden_text:
                assert token not in source, (module, token)


def test_the_analytical_entry_point_takes_a_database_not_a_directory():
    """`GROUND_TRUTH_CONTRACT.md` §4 rule 2, as an API-shape test.

    Reaching truth has to require deliberate circumvention, not an accident:
    every fabops data-access helper is given a path to `fab.db`, so there is
    no call it can make that receives the dataset directory `truth/` sits in.
    """
    import inspect

    from fabops import db as fabops_db

    for name in ("connect", "run_query", "run_view"):
        parameters = inspect.signature(getattr(fabops_db, name)).parameters
        assert "db_path" in parameters, name
        assert not {"dataset", "dataset_dir", "directory", "truth"} & set(
            parameters), name


# ------------------------------------------------------ fabops compatibility


def test_fabops_can_read_the_emitted_observable_dataset(built):
    """§15: the diagnostic plane consumes the dataset without importing FabSim.

    Through the real `fabops.db` helpers, on the emitted `fab.db`. What is
    *not* claimed here is that the legacy v1 analytical SQL runs against v2 —
    schema v2 deliberately renamed the changed-grain tables so nothing
    silently reads the wrong schema, and the compatibility views are a Phase 2
    deliverable (`SCHEMA_V2_DESIGN.md` §6). What must hold now is that the
    data-access layer opens it, enforces its constraints and returns rows.
    """
    from fabops.db import connect, run_query

    connection = connect(built.db_path)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()

    frame = run_query(
        "SELECT t.tool_name, COUNT(*) AS runs "
        "FROM runs r JOIN tools t ON t.tool_id = r.tool_id "
        "GROUP BY t.tool_name ORDER BY t.tool_name", built.db_path)
    assert len(frame) > 5
    assert frame["runs"].sum() == built.observable.row_counts()["runs"]

    yields = run_query(
        "SELECT AVG(yield_pct) AS mean_yield FROM wafer_yield", built.db_path)
    assert 50.0 < float(yields["mean_yield"].iloc[0]) < 100.0


def test_a_consumer_given_the_database_cannot_see_the_truth(built):
    """The boundary, from the consumer's side: `fab.db` holds no answer.

    An analytical consumer receives one path. Everything reachable from it is
    the 22 observable tables; the hidden plane is a sibling directory it was
    never given and has no reference to.
    """
    objects = query(built.db_path,
                    "SELECT type, name FROM sqlite_master ORDER BY name")
    names = {name for _type, name in objects}
    assert not any("truth" in name.lower() for name in names)
    text = built.db_path.read_bytes()
    for token in (b"truth.json", b"chamber_edge_uniformity",
                  b"emission-reference"):
        assert token not in text, token


# ---------------------------------------------------------------- manifest


def test_the_manifest_carries_the_five_inputs_and_their_fingerprint(built):
    manifest = built.manifest
    assert manifest["schema"] == MANIFEST_SCHEMA
    for field in ("dataset_id", "scenario_id", "config_sha256",
                  "world_sha256", "seed", "fabsim_version", "schema_version",
                  "build_fingerprint", "content_sha256", "row_counts",
                  "files", "created_at"):
        assert field in manifest, field
    assert manifest["fabsim_version"] == __version__
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["content_sha256"] == built.observable.content_sha256()
    assert manifest["row_counts"] == built.observable.row_counts()


def test_the_manifest_names_no_scenario(built):
    """ADR-013 / rule D5: provenance, never semantics.

    `row_counts` is checked separately and by shape rather than by token: its
    keys are the schema's own table names, one of which is `chambers`, and a
    substring scan over them would be scanning the schema rather than the
    manifest's content.
    """
    assert set(built.manifest["row_counts"]) == set(SCHEMA_TABLES)
    rest = {k: v for k, v in built.manifest.items() if k != "row_counts"}
    text = json.dumps(rest).lower()
    for token in ("emission-reference", "etch", "chamber", "uniformity",
                  "mechanism", "severity", "fault", "obvious", "edge"):
        assert token not in text, token


def test_the_manifest_hashes_every_emitted_file(built):
    from fabsim.emit.manifest import file_sha256

    files = built.manifest["files"]
    assert set(files) == {"fab.db", "fab_database.sql", "truth/truth.json"}
    for relative, digest in files.items():
        assert file_sha256(built.directory / relative) == digest

    # The truth file's *hash* is provenance, not disclosure: it proves the
    # hidden plane was not edited between generation and scoring.
    assert len(files["truth/truth.json"]) == 64


# ----------------------------------------------------------------- selftest


def test_the_build_self_tests_before_it_returns(built):
    """Stage 7 of `FABSIM_DESIGN.md` §4 ran, and passed, on this dataset."""
    check_observable(built.observable.tables, built.response.world)


@pytest.mark.parametrize("rule, mutate", [
    ("§4.3 reconciliation", lambda t: t.__setitem__(
        "inspections", tuple((r[0], r[1], r[2], r[3], r[4], r[5] + 1, r[6])
                             for r in t["inspections"]))),
    ("§4.3 reconciliation", lambda t: t.__setitem__(
        "wafer_yield", tuple((r[0], r[1], r[2], r[3], r[4] + 1, r[5], r[6])
                             for r in t["wafer_yield"]))),
    # A *failing* bin, so the PASS count — and therefore §4.3 — is untouched
    # and the vocabulary rule is the one that fires.
    ("§4.4 vocabulary", lambda t: t.__setitem__("die_bins", tuple(
        (r[0], r[1], r[2], "MELTED") if r[3] != "PASS" else r
        for r in t["die_bins"]))),
    ("§4.2 clock", lambda t: t.__setitem__(
        "runs", ((*t["runs"][0][:7], t["runs"][0][8], t["runs"][0][7]),)
        + t["runs"][1:])),
    ("§4.1 referential", lambda t: t.__setitem__(
        "runs", ((*t["runs"][0][:4], 999, *t["runs"][0][5:]),)
        + t["runs"][1:])),
])
def test_the_self_test_catches_a_broken_dataset(built, rule, mutate):
    """Mutation: each §4 family, broken on purpose, must be refused."""
    tables = dict(built.observable.tables)
    mutate(tables)
    with pytest.raises(SelfTestError) as excinfo:
        check_observable(tables, built.response.world)
    assert excinfo.value.rule == rule


def test_the_self_test_reads_only_the_observable_plane():
    """It checks the dataset against the contract, not against the simulator
    that produced it — marking your own homework is not a check."""
    source = (Path(__file__).resolve().parents[2] / "src" / "fabsim"
              / "selftest.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    identifiers = {n.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute)}
    for forbidden in ("realization", "trajectories", "mechanisms", "origins",
                      "outcomes", "counterfactual", "resets", "distractors"):
        assert forbidden not in identifiers, forbidden
