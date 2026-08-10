"""
Final integration: the whole chain, on one dataset, from world to artifact.

Every stage of this project has its own tests. What none of them can check is
that the stages still compose — that the thing `fabsim` writes is the thing
`fabops.semantic` reads, that the report `fabops.diagnosis` returns is the one
`fabops.report` embeds, that the evaluator can still join the two planes, and
that nothing downstream of the emitter has quietly acquired a way to reach the
answer key.

Two properties are the substance and the rest is plumbing.

**Truth invariance now covers the whole analysis plane, not only the engine.**
`DIAGNOSIS_CONTRACT.md` §6.6 requires that rewriting every hidden record leave
the `Investigation` identical. Three new surfaces have landed since — the
monitors, the decision artifact and the workspace payload — and each is a new
opportunity to read a sibling path. The rewrite is applied once and all four
outputs are compared.

**The mirror is required too**, because invariance is worthless on an inert
stack: moving one observable value must move the outputs that read it.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Schema v2's own count (`SCHEMA_V2_DESIGN.md` §2.1–2.22).
SCHEMA_V2_TABLES = 22

#: Tokens that must not appear in any table or column name of the observable
#: plane. L1's own list, applied at the integration boundary rather than inside
#: the evaluator, so that a schema change anywhere fails here too, plus two
#: hidden-plane words L1 predates.
#:
#: Two words are deliberately **not** on the list, and both were tried. `origin`
#: appears in `dataset_meta.time_origin`, which is provenance — the instant the
#: horizon starts — and banning the substring would ban the column whose whole
#: purpose is letting a reader convert a timestamp into a day. `severity`
#: appears in `alarms.severity`, which is the alarm's own coded level from the
#: world's shared vocabulary and has nothing to do with a mechanism's. A lint
#: that forbids an honest observable is a lint people learn to silence.
FORBIDDEN_SCHEMA_TOKENS = ("fault", "truth", "scenario", "bad", "marginal",
                           "suspect", "inject", "ground", "mechanism",
                           "counterfactual", "latent")


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """One faulted dataset, built here so this module depends on no package."""
    from tests.fabops.datasets import build_one

    root = tmp_path_factory.mktemp("integration")
    return build_one(("chamber_edge_uniformity", 42, str(root)))


@pytest.fixture(scope="module")
def outputs(chain):
    """Everything the analysis plane produces from that one dataset."""
    from fabops.diagnosis import diagnose
    from fabops.monitors import monitor
    from fabops.report import build_report
    from fabops.report.workspace import load_workspace

    db_path = chain["db_path"]
    return {
        "investigation": diagnose(db_path).to_dict(),
        "monitor": monitor(db_path).to_dict(),
        "report": build_report(db_path).to_dict(),
        "workspace": load_workspace(db_path),
    }


# ------------------------------------------------------- the two planes


def test_the_two_planes_are_separate_files_and_only_one_is_the_dataset(chain):
    database = Path(chain["db_path"])
    answer_key = Path(chain["truth_path"])
    assert database.exists() and answer_key.exists()
    assert answer_key.parent.name == "truth"
    assert answer_key.parent != database.parent, (
        "the answer key sits in the directory an analyst is handed")
    assert database.parent == answer_key.parent.parent


def test_the_observable_plane_is_schema_v2_and_names_nothing_hidden(chain):
    connection = sqlite3.connect(f"file:{Path(chain['db_path']).as_posix()}"
                                 f"?mode=ro", uri=True)
    try:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        assert len(tables) == SCHEMA_V2_TABLES, tables
        names = list(tables)
        for table in tables:
            names += [row[1] for row in connection.execute(
                f"PRAGMA table_info({table})")]
    finally:
        connection.close()

    lowered = " ".join(names).lower()
    for token in FORBIDDEN_SCHEMA_TOKENS:
        assert token not in lowered, token


def test_the_manifest_records_the_five_reproducibility_inputs(chain):
    manifest = json.loads(
        (Path(chain["db_path"]).parent / "manifest.json").read_text(
            encoding="utf-8"))
    payload = json.dumps(manifest)
    for field in ("config_sha256", "world_sha256", "seed", "fabsim_version",
                  "schema_version", "build_fingerprint", "content_sha256"):
        assert field in payload, field
    assert "chamber_edge_uniformity" not in payload, (
        "the manifest names the scenario; rule D5 forbids it")


# --------------------------------------------------- the chain composes


def test_every_stage_produced_something_and_they_agree_on_the_dataset(outputs,
                                                                      chain):
    dataset_id = outputs["investigation"]["dataset_id"]
    assert dataset_id == chain["dataset_id"]
    assert outputs["monitor"]["dataset_id"] == dataset_id
    assert outputs["report"]["dataset_id"] == dataset_id
    assert outputs["workspace"]["dataset"]["dataset_id"] == dataset_id
    assert outputs["monitor"]["signals"], "the monitors saw nothing at all"
    assert outputs["investigation"]["candidates"]
    assert outputs["report"]["actions"]


def test_the_artifact_embeds_the_engines_report_unaltered(outputs):
    assert outputs["report"]["investigation"] == outputs["investigation"]
    assert outputs["report"]["investigation"]["schema"] == \
        "fabops.investigation/v1"
    assert outputs["report"]["schema"] == "fabops.report/v1"


def test_the_evaluator_can_still_join_the_two_planes(chain):
    """`fabeval` is the only component permitted to hold both, and the join is
    on `dataset_id`. If that stops working the benchmark stops existing."""
    from fabeval.diagnosisscore import score_dataset
    from fabops.diagnosis import diagnose

    truth = json.loads(Path(chain["truth_path"]).read_text(encoding="utf-8"))
    outcome = score_dataset(diagnose(chain["db_path"]).to_dict(), truth,
                            chain["scenario"], None)
    assert outcome.dataset_id == chain["dataset_id"]
    assert outcome.faulted
    assert outcome.planted


# ------------------------------------------- truth invariance, extended


def _rewrite_hidden_plane(path: Path) -> None:
    """Replace every value in the answer key with something else."""
    payload = json.loads(path.read_text(encoding="utf-8"))

    def scramble(node):
        if isinstance(node, dict):
            return {key: scramble(value) for key, value in node.items()}
        if isinstance(node, list):
            return [scramble(value) for value in node]
        if isinstance(node, str):
            return node[::-1] + "-rewritten"
        if isinstance(node, bool):
            return not node
        if isinstance(node, (int, float)):
            return node + 1
        return node

    payload["events"] = scramble(payload.get("events", []))
    payload["distractors"] = scramble(payload.get("distractors", []))
    payload["scenario_name"] = "something-else-entirely"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def test_the_whole_analysis_plane_is_invariant_to_the_answer_key(chain,
                                                                 tmp_path,
                                                                 outputs):
    """§6.6, extended past the engine to every surface built on it."""
    from fabops.monitors import monitor
    from fabops.report import build_report
    from fabops.report.workspace import load_workspace
    from fabops.diagnosis import diagnose

    source = Path(chain["db_path"])
    workspace = tmp_path / "invariance"
    (workspace / "truth").mkdir(parents=True)
    database = workspace / source.name
    shutil.copy2(source, database)
    shutil.copy2(Path(chain["truth_path"]),
                 workspace / "truth" / Path(chain["truth_path"]).name)
    shutil.copy2(source.parent / "manifest.json", workspace / "manifest.json")

    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    before = {
        "investigation": diagnose(database).to_dict(),
        "monitor": monitor(database).to_dict(),
        "report": build_report(database).to_dict(),
        "workspace": load_workspace(database),
    }
    _rewrite_hidden_plane(workspace / "truth" /
                          Path(chain["truth_path"]).name)
    after = {
        "investigation": diagnose(database).to_dict(),
        "monitor": monitor(database).to_dict(),
        "report": build_report(database).to_dict(),
        "workspace": load_workspace(database),
    }

    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest, (
        "the observable plane moved; the comparison below would mean nothing")
    for name in before:
        assert json.dumps(after[name], sort_keys=True, default=str) == \
            json.dumps(before[name], sort_keys=True, default=str), name


def test_moving_one_observable_value_moves_the_stack(chain, tmp_path):
    """The mirror. Invariance on an inert stack is not invariance."""
    from fabops.monitors import monitor
    from fabops.report import build_report

    database = tmp_path / "mirror.db"
    shutil.copy2(chain["db_path"], database)
    before = (json.dumps(monitor(database).to_dict(), sort_keys=True),
              json.dumps(build_report(database).to_dict(), sort_keys=True))

    connection = sqlite3.connect(str(database))
    try:
        connection.execute(
            "UPDATE metrology SET value = value * 1.05 "
            "WHERE param_name = 'cd_nm_edge'")
        connection.execute(
            "UPDATE wafer_yield SET yield_pct = yield_pct * 0.8 "
            "WHERE wafer_id IN (SELECT wafer_id FROM wafer_yield "
            "                   ORDER BY wafer_id LIMIT 60)")
        connection.commit()
    finally:
        connection.close()

    after = (json.dumps(monitor(database).to_dict(), sort_keys=True),
             json.dumps(build_report(database).to_dict(), sort_keys=True))
    assert after[0] != before[0], "the monitors did not notice a 5% CD shift"
    assert after[1] != before[1], "the artifact did not notice a yield change"


# --------------------------------------------------- the other project


def test_fabkg_is_not_in_this_repository():
    """`FABOPS_VS_FABKG_BOUNDARY.md`: either project must build and run with
    the other entirely absent. The test of that is that nothing here has heard
    of it — no import, no dependency, and no graph machinery under another
    name."""
    import ast

    forbidden_imports = ("neo4j", "rdflib", "networkx", "owlready2",
                         "langchain", "llama_index", "openai", "anthropic",
                         "transformers", "chromadb", "faiss", "sentence_transformers")
    for path in sorted((REPO / "src").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {alias.name for node in ast.walk(tree)
                 if isinstance(node, ast.Import) for alias in node.names}
        names |= {node.module for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) and node.module}
        roots = {name.split(".")[0] for name in names if name}
        assert not (roots & set(forbidden_imports)), (path.name, roots)
        assert "fabkg" not in {root.lower() for root in roots}, path.name


def test_the_export_boundary_is_a_versioned_file_and_nothing_else(outputs):
    """The only thing that crosses the boundary is a schema-versioned JSON
    document this repository writes and forgets about."""
    payload = json.dumps(outputs["report"])
    assert json.loads(payload) == outputs["report"], (
        "the artifact does not round-trip through JSON, so it is not a file "
        "another system could read")
    assert outputs["report"]["provenance"]["knowledge"]["schema"] == \
        "fabops.knowledge/v1"
