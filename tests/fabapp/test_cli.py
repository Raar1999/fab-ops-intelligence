"""
`fabops-app`: the one command, and the promises it makes.

The audited P0 defect of this repository was a documented command that did not
work on a clean clone, so an entry point is tested by running it rather than by
importing it. Three properties matter beyond exit codes:

* it works from **any** working directory, which is the whole reason the
  application lives inside the package instead of in a scripts folder;
* it locates the interface without *running* it, because a Streamlit module
  runs when it is imported and a launcher that imported it would draw the
  application into its own process;
* and when Streamlit is absent it says so in a sentence with a command in it,
  instead of failing with an import error.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fabapp import cli

REPO = Path(__file__).resolve().parents[2]


def run(arguments, cwd: Path, root: Path | None = None):
    environment = dict(os.environ)
    if root is not None:
        environment["FABOPS_DATASET_ROOT"] = str(root)
    return subprocess.run(
        [sys.executable, "-m", "fabapp.cli", *arguments],
        capture_output=True, text=True, cwd=str(cwd), timeout=900,
        env=environment)


def test_the_launcher_locates_the_interface_without_importing_it():
    path = cli.ui_path()
    assert path.is_file() and path.name == "app.py"
    assert "fabapp" in path.parts
    assert "fabapp.ui.app" not in sys.modules, (
        "locating the interface imported it, which runs the whole "
        "application inside the launcher's own process")


def test_where_reports_the_paths_from_any_directory(tmp_path):
    result = run(["--where"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    for key in ("workspace", "scenarios", "datasets", "legacy_database"):
        assert key in result.stdout
    assert str(REPO) in result.stdout


def test_listing_from_outside_the_repository_finds_the_datasets(
        tmp_path, populated_root, faulted):
    result = run(["--list"], cwd=tmp_path, root=populated_root)
    assert result.returncode == 0, result.stderr
    assert faulted.record.dataset_id in result.stdout
    assert "chamber_edge_uniformity" in result.stdout
    assert "2 dataset(s)" in result.stdout


def test_listing_an_empty_root_says_how_to_get_one(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run(["--list"], cwd=tmp_path, root=empty)
    assert result.returncode == 0, result.stderr
    assert "No datasets" in result.stdout
    assert "fabops-app" in result.stdout


def test_the_check_runs_the_whole_workflow_from_outside_the_repository(
        tmp_path, populated_root):
    """The end-to-end product test, as a command a person can type."""
    result = run(["--check"], cwd=tmp_path, root=populated_root)
    assert result.returncode == 0, result.stderr
    for line in ("datasets discovered", "dataset opened", "pages with data",
                 "diagnosis", "export"):
        assert line in result.stdout, result.stdout
    assert "Fab Today" in result.stdout and "Investigation" in result.stdout
    assert "fabops.report/v1" in result.stdout


def test_the_check_names_a_dataset_when_it_is_given_one(tmp_path,
                                                        populated_root, null):
    result = run(["--check", "--dataset", str(null.record.db_path)],
                 cwd=tmp_path, root=populated_root)
    assert result.returncode == 0, result.stderr
    assert null.record.dataset_id in result.stdout
    assert "null_baseline" in result.stdout


def test_the_check_fails_loudly_on_an_empty_root(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    result = run(["--check"], cwd=tmp_path, root=empty)
    assert result.returncode != 0
    assert "create one first" in (result.stderr + result.stdout)


def test_a_workspace_that_is_not_a_checkout_is_refused_with_a_way_out(
        tmp_path):
    """`fabsim` resolves its world registry from the checkout, so the product
    needs one. The failure has to be a sentence, not a traceback four frames
    into a path derivation."""
    environment = {**os.environ, "FABOPS_HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "fabapp.cli", "--where"],
        capture_output=True, text=True, cwd=str(REPO), timeout=900,
        env=environment)
    assert result.returncode == 2
    assert "does not look like a Fab Ops checkout" in result.stderr
    assert "FABOPS_HOME" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_missing_streamlit_is_explained_rather_than_raised(monkeypatch):
    """The one dependency the interface needs and the rest of the product does
    not."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "streamlit":
            raise ImportError("no streamlit here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.delitem(sys.modules, "streamlit", raising=False)
    assert cli.main([]) == 2


def test_the_missing_streamlit_message_names_the_command_that_fixes_it():
    message = cli._streamlit_missing()
    assert "pip install" in message and '".[app]"' in message
    assert "--check" in message, (
        "the message should say what still works without an interface")


def test_the_legacy_dashboard_is_reachable_and_labelled():
    """ADR-010's demo is a different fab. The product can start it and says so
    rather than merging its pages in."""
    legacy = REPO / "app" / "ops_dashboard.py"
    assert legacy.is_file()
    source = (REPO / "src" / "fabapp" / "cli.py").read_text(encoding="utf-8")
    assert "--legacy" in source
    assert "ops_dashboard.py" in source


def test_the_help_names_the_actions_a_user_needs():
    result = run(["--help"], cwd=REPO)
    assert result.returncode == 0
    for flag in ("--dataset", "--page", "--list", "--check", "--legacy",
                 "--where"):
        assert flag in result.stdout


@pytest.mark.parametrize("script", ["fabops-app"])
def test_the_console_script_is_declared_and_resolves(script):
    import importlib
    import tomllib

    declared = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8"))
    target = declared["project"]["scripts"][script]
    module, _, attribute = target.partition(":")
    assert callable(getattr(importlib.import_module(module), attribute))
