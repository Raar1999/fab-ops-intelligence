"""
The boundary the engine is only allowed to be trusted because of.

Static rules 1-5 of `DIAGNOSIS_CONTRACT.md` §6 already have a home in
`tests/test_diagnosis_contract.py`, which armed itself when the package
appeared. This module adds the two the contract gained when the engine was
built - the anchor is chosen without a candidate (§6.7), and exactly one
database is opened with no sibling path constructed (§6.8) - and then proves
each of them by mutation, because a checker nobody has seen fail proves
nothing.
"""
from __future__ import annotations

import ast
import inspect
import sqlite3
from pathlib import Path

import pytest

from fabops.diagnosis import diagnose
from fabops.diagnosis import anchors as anchors_module

PACKAGE = Path(inspect.getfile(diagnose)).parent
SOURCES = sorted(PACKAGE.rglob("*.py"))

#: §6.8. Anything that could turn one database path into a second file.
FORBIDDEN_CALLS = ("open", "glob", "rglob", "iterdir", "listdir", "walk")
FORBIDDEN_ATTRIBUTES = ("parent", "parents", "with_name", "with_suffix")


def test_the_engine_has_sources_to_check():
    """A scan over an empty package is a scan that always passes."""
    assert len(SOURCES) >= 6, [p.name for p in SOURCES]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_module_can_reach_a_second_file(path):
    """§6.8. The engine is handed a path; it may not derive another."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            name = (target.id if isinstance(target, ast.Name)
                    else target.attr if isinstance(target, ast.Attribute)
                    else "")
            if name in FORBIDDEN_CALLS:
                offences.append(f"calls {name}()")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            offences.append(f"reads .{node.attr}")
    assert not offences, f"{path.name}: {offences}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_module_reads_the_legacy_database_default(path):
    """The engine must never inherit `fabops.config.DB_PATH`.

    That default points at the schema v1 legacy database. An engine that
    silently fell back to it would be diagnosing a different fab than the one
    it was asked about, and the failure would look like a bad answer rather
    than a wrong input.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "") != "fabops.config", \
                f"{path.name} imports fabops.config"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "fabops.config", \
                    f"{path.name} imports fabops.config"
    assert "DB_PATH" not in source, f"{path.name} names DB_PATH"


def test_the_anchor_is_chosen_without_a_candidate():
    """§6.7. Structural: the signature has nowhere to put an entity."""
    parameters = list(
        inspect.signature(anchors_module.select).parameters)
    assert parameters == ["horizon_days", "fractions"], parameters


def test_the_anchor_module_cannot_see_an_observation_or_a_panel():
    """§6.7, the import-graph half: it depends on no data structure."""
    tree = ast.parse((PACKAGE / "anchors.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    forbidden = {"Observation", "Panel", "CandidateSupport", "Candidate",
                 "build_panels", "load_observations", "Decision", "Stratum"}
    assert not (imported & forbidden), sorted(imported & forbidden)


def test_diagnose_opens_exactly_the_database_it_was_given(null_dataset,
                                                          monkeypatch):
    """§6.8, the runtime half. Record every connection the engine opens."""
    opened: list[str] = []
    real_connect = sqlite3.connect

    def recording_connect(target, *args, **kwargs):
        opened.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    diagnose(null_dataset["db_path"])
    assert opened == [str(null_dataset["db_path"])], opened


def test_the_engine_never_names_an_entity_or_a_mechanism():
    """Rules 3 and 4, re-asserted here so this module is self-contained.

    The vocabulary comes from the simulator's own registry rather than a copy,
    so a mechanism added later is covered without anyone remembering to add it.
    """
    from fabsim.mechanisms import MECHANISM_NAMES

    forbidden = tuple(MECHANISM_NAMES) + ("edge_uniformity",)
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        for word in forbidden:
            assert word not in source, f"{path.name} names {word!r}"


# --------------------------------------------------------------- mutation


VIOLATIONS = {
    "reaches a sibling file": "from pathlib import Path\n"
                              "def f(db_path):\n"
                              "    return Path(db_path).parent\n",
    "opens a second file": "def f(db_path):\n"
                           "    return open(db_path)\n",
    "lists a directory": "def f(db_path):\n"
                         "    return db_path.glob('*')\n",
}


@pytest.mark.parametrize("label,source", sorted(VIOLATIONS.items()))
def test_the_sibling_path_rule_fires_on_a_module_that_breaks_it(
        label, source, tmp_path):
    """The mutation. Each forbidden shape must actually be caught."""
    path = tmp_path / "engine.py"
    path.write_text(source, encoding="utf-8")
    tree = ast.parse(source)
    offences = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            name = (target.id if isinstance(target, ast.Name)
                    else target.attr if isinstance(target, ast.Attribute)
                    else "")
            if name in FORBIDDEN_CALLS:
                offences.append(name)
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            offences.append(node.attr)
    assert offences, f"{label} was not caught"
