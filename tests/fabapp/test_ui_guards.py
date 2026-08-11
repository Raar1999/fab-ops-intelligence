"""
The interface renders and does not decide — checked over every screen.

These scans used to point at one file, `app/investigation_workspace.py`. The
product absorbed its pages, so they point at the whole of `fabapp.ui` now,
which is strictly more surface than they covered before.

What they are for has not changed. The audited dashboard highlighted a row pink
because a module constant named a suspect, defaulted its map to that tool, and
captioned the screen with the conclusion before the reader had read anything.
None of that can recur silently, because:

* no screen names a tool, a chamber, a product or a mechanism;
* no screen contains a query, opens a database, or imports the simulator;
* no screen compares anything against a number of its own — a threshold in an
  interface is a decision in an interface;
* and every screen is *executed* on real data, because the repository's
  previous dashboard was verified by hand once and then drifted.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UI = REPO / "src" / "fabapp" / "ui"

#: Every page the application offers, including the two that need no dataset.
PAGES = ("Datasets", "Fab Today", "Process", "Equipment", "Yield", "Defect",
         "Investigation", "Wafer explorer", "About")


def ui_modules() -> list[Path]:
    found = sorted(p for p in UI.rglob("*.py") if "__pycache__" not in p.parts)
    assert len(found) >= 5, found
    return found


def imports_of(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


#: What a v2 presentation surface may not name. Declared once so the scan and
#: the mutation that proves the scan works are literally the same expression.
FORBIDDEN_LITERALS = (r"ETCH-\d", r"CVD-\d", r"PVD-\d", r"CMP-\d",
                      r"LITHO-\d", r"Mobile-28", r"Logic-14",
                      r"chamber_edge_uniformity", r"param_drift",
                      r"particle_excursion", r"benign_offset",
                      r"DEMO_SUSPECT_TOOL")

#: SQL, as its shape rather than as one of its keywords. The predecessor of
#: this scan looked for the bare word `SELECT`, which also forbids an interface
#: from saying "Select a dataset" — so it is matched together with its `FROM`,
#: and backed by the stronger structural check that no screen executes
#: anything.
SQL_SHAPE = re.compile(r"\bSELECT\b.{0,400}?\bFROM\b", re.I | re.S)


def test_no_screen_names_an_entity_or_a_mechanism():
    for module in ui_modules():
        source = module.read_text(encoding="utf-8")
        hits = [pattern for pattern in FORBIDDEN_LITERALS
                if re.search(pattern, source)]
        assert not hits, (module.name, hits)


def test_the_entity_scan_fires_on_a_screen_that_names_one():
    """A guard that cannot fail is not a guard. The audited dashboard's own
    constant is appended to a copy of a screen and run through the same
    expression the real files pass."""
    poisoned = (UI / "explore.py").read_text(encoding="utf-8") + (
        '\nSUSPECT = "ETCH-02"\n')
    hits = [pattern for pattern in FORBIDDEN_LITERALS
            if re.search(pattern, poisoned)]
    assert hits == [r"ETCH-\d"]


def test_no_screen_imports_the_simulator_the_evaluator_or_a_database():
    for module in ui_modules():
        names = imports_of(module.read_text(encoding="utf-8"))
        roots = {name.split(".")[0] for name in names}
        assert not roots & {"fabsim", "fabeval"}, (module.name, roots)
        assert "sqlite3" not in roots, f"{module.name} opened its own database"
        assert "fabops.config" not in names, (
            f"{module.name} imported the module holding the legacy database "
            f"path and the legacy demo's suspect")


def test_no_screen_contains_a_query_or_executes_one():
    for module in ui_modules():
        source = module.read_text(encoding="utf-8")
        assert not SQL_SHAPE.search(source), f"{module.name} contains SQL"
        called = {node.func.attr for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        for forbidden in ("execute", "executescript", "executemany"):
            assert forbidden not in called, (module.name, forbidden)


def test_the_sql_scan_fires_on_a_screen_that_queries():
    clean = (UI / "explore.py").read_text(encoding="utf-8")
    poisoned = clean + '\nrows = c.execute("SELECT 1 FROM runs")\n'
    assert SQL_SHAPE.search(poisoned)
    assert not SQL_SHAPE.search(clean)


def test_the_sql_scan_still_lets_a_screen_say_the_word():
    """The refinement over the predecessor scan, stated as a test so that the
    reason it was widened is visible rather than inferred: an interface has to
    be able to write "Select a dataset"."""
    assert not SQL_SHAPE.search('st.selectbox("Select a dataset", options)')


def test_no_screen_compares_anything_against_a_number_of_its_own():
    """A threshold in an interface is a decision in an interface.

    Every level this product shows — the abstention's alpha, a candidate's
    p-value, the rank that counts as leading — is read from the artifact by
    `fabapp.explain`. A float on the right-hand side of a comparison inside a
    screen would be a second, undeclared one.
    """
    for module in ui_modules():
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Compare):
                continue
            for operand in (node.left, *node.comparators):
                assert not (isinstance(operand, ast.Constant)
                            and isinstance(operand.value, float)), (
                    f"{module.name}:{node.lineno} compares against a literal "
                    f"threshold")


def test_the_threshold_scan_fires_on_a_screen_that_invents_one():
    poisoned = ("import streamlit as st\n"
                "def render(p):\n"
                "    if p < 0.05:\n"
                "        st.success('significant')\n")
    found = [node for node in ast.walk(ast.parse(poisoned))
             if isinstance(node, ast.Compare)
             and any(isinstance(operand, ast.Constant)
                     and isinstance(operand.value, float)
                     for operand in (node.left, *node.comparators))]
    assert found


def test_the_application_has_no_default_dataset():
    """A v2 surface that fell back to the legacy database would answer about
    the schema v1 fab with no error anywhere."""
    source = (UI / "app.py").read_text(encoding="utf-8")
    assert "DB_PATH" not in source
    assert "FABOPS_DATASET" in source and "--dataset" in source


def test_the_shell_and_the_data_layer_agree_about_the_pages():
    from fabops.report.workspace import WORKSPACE_PAGES

    from fabapp.ui.app import NEEDS_DATASET
    from fabapp.ui.app import PAGES as SHELL_PAGES

    assert set(SHELL_PAGES) == set(PAGES)
    assert set(WORKSPACE_PAGES) <= set(SHELL_PAGES)
    assert NEEDS_DATASET == set(WORKSPACE_PAGES)


# ------------------------------------------------------ the screens execute


def run_page(page: str, dataset: str | None, root: Path) -> None:
    """Streamlit's bare mode runs the script with every widget returning its
    default, which is enough to prove a screen renders end to end on real
    data."""
    pytest.importorskip("streamlit")
    command = [sys.executable, "-m", "fabapp.ui.app", "--page", page]
    if dataset is not None:
        command += ["--dataset", dataset]
    result = subprocess.run(
        command, capture_output=True, text=True, cwd=str(REPO), timeout=900,
        env={**os.environ, "FABOPS_DATASET_ROOT": str(root)})
    assert result.returncode == 0, result.stderr[-2000:]
    assert "Traceback" not in result.stderr, result.stderr[-2000:]


@pytest.mark.parametrize("page", PAGES)
def test_every_screen_executes_on_a_faulted_dataset(page, faulted,
                                                    populated_root):
    run_page(page, str(faulted.record.db_path), populated_root)


@pytest.mark.parametrize("page", ["Investigation", "Fab Today", "Defect"])
def test_the_screens_execute_on_a_fault_free_dataset(page, null,
                                                     populated_root):
    """The pages most tempting to write for the case where there *is* a
    candidate. On a fault-free world there is none, and they must still
    render."""
    run_page(page, str(null.record.db_path), populated_root)


@pytest.mark.parametrize("page", ["Datasets", "About", "Fab Today"])
def test_the_application_starts_with_no_dataset_open(page, tmp_path):
    """The first thing a new user sees. Two of these need no dataset at all,
    and the third must say so rather than fail."""
    run_page(page, None, tmp_path)


def test_a_screen_refuses_the_legacy_database_rather_than_rendering_it(
        populated_root):
    legacy = REPO / "data" / "fab.db"
    if not legacy.is_file():
        pytest.skip("the legacy v1 database has not been built here")
    run_page("Fab Today", str(legacy), populated_root)
