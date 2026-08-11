"""
cli.py — `fabops-app`, the one command a user has to know.

    fabops-app                     start FabOps
    fabops-app --dataset <path>    ...opening on a dataset
    fabops-app --page Investigation  ...on a particular screen
    fabops-app --list              what datasets exist, without a browser
    fabops-app --check             run the whole workflow headlessly
    fabops-app --legacy            the legacy schema v1 demo dashboard

**Why a launcher exists at all.** `streamlit run app/whatever.py` only works
from the checkout's root, needs the reader to know which file is current, and
gives them a second one to get wrong. The application ships *inside* the
package, so this command resolves its path from the import system and runs from
anywhere. That is the whole of the productization on this surface, and it is
also what makes the entry point testable: `--check` runs the same chain the
screens run, with no browser in it.

**It launches Streamlit as a subprocess rather than importing it.** Streamlit
owns the process it runs in — its own argument parsing, its own signal
handling, its own server. `python -m streamlit` is used rather than the
`streamlit` executable because on Windows a console script may not be on the
PATH of the interpreter that installed it, and the whole point of this file is
that the command works.

**`--legacy` is a signpost, not a merge.** The schema v1 dashboard reads a
different fab and narrates a conclusion that is a constant (ADR-003, ADR-010).
Putting its pages inside the product would make one application answer about
two fabs with one navigation bar, which is the legacy/v2 confusion this
repository has spent several gates avoiding. So the product knows where it is,
can start it, and says plainly what it is.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from fabapp import APP
from fabapp.config import WorkspaceError, locations
from fabapp.registry import discover
from fabapp.service import workflow_check

__all__ = ["APPLICATION", "main", "ui_path"]

#: The environment is how the launcher talks to the application. Streamlit
#: re-runs the script on every interaction, and an environment variable
#: survives that identically on every platform.
DATASET_ENV = "FABOPS_DATASET"
PAGE_ENV = "FABOPS_PAGE"

APPLICATION = "FabOps"


def ui_path() -> Path:
    """The Streamlit entry module, resolved through the import system.

    Located rather than imported. A Streamlit script *runs* when it is
    imported — that is what a Streamlit script is — so a launcher that reached
    it with an `import` would draw the whole application into its own process
    before handing the file to the server that is supposed to run it.
    """
    import importlib.util

    spec = importlib.util.find_spec("fabapp.ui.app")
    if spec is None or not spec.origin:                     # pragma: no cover
        raise RuntimeError(
            "the FabOps application module could not be located; the "
            "installation is incomplete")
    return Path(spec.origin).resolve()


def _streamlit_missing() -> str:
    return (
        "FabOps needs Streamlit to draw its interface, and it is not "
        "installed in this environment.\n"
        f"  {sys.executable} -m pip install -e \".[app]\"\n"
        "Everything except the interface works without it: `fabops-app "
        "--check` runs the whole workflow headlessly, and `fabops-diagnose`, "
        "`fabops-monitor` and `fabops-report` are unaffected.")


def _run_streamlit(script: Path, argv: Sequence[str], *, port: int | None,
                   headless: bool) -> int:
    try:
        import streamlit                                    # noqa: F401
    except ImportError:
        print(_streamlit_missing(), file=sys.stderr)
        return 2

    command = [sys.executable, "-m", "streamlit", "run", str(script)]
    if port is not None:
        command += ["--server.port", str(port)]
    if headless:
        command += ["--server.headless", "true"]
    if argv:
        command += ["--", *argv]
    return subprocess.call(command)


def _list_datasets() -> int:
    records = discover()
    where = locations()
    if not records:
        print(f"No datasets in {where.datasets}.")
        print("Start FabOps with `fabops-app` and create one, or run "
              "`make dataset`.")
        return 0
    width = max(len(record.dataset_id or record.db_path.name)
                for record in records)
    for record in records:
        name = (record.dataset_id or record.db_path.name).ljust(width)
        if record.usable:
            print(f"{name}  {record.status:<8}"
                  f"{record.scenario or 'unknown scenario':<30}"
                  f"seed {record.seed}  {record.horizon_days} d")
        else:
            print(f"{name}  {record.status:<8}{record.detail}")
    print(f"\n{len(records)} dataset(s) in {where.datasets}")
    return 0


def _check(dataset: str | None) -> int:
    result = workflow_check(dataset)
    print(f"{APPLICATION} workflow check — {APP}")
    print(result.describe())
    return 0


def _where() -> int:
    where = locations()
    for key, value in where.to_dict().items():
        if key == "overrides":
            continue
        print(f"{key:<18}{value}")
    overrides = where.to_dict()["overrides"]
    print(f"{'overrides':<18}{overrides or '(none)'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fabops-app",
        description=("Start FabOps: create or load a dataset, explore the "
                     "simulated fab, and run the answer-blind diagnosis."))
    parser.add_argument("--dataset", default=None,
                        help="open on this dataset (a path or a dataset id)")
    parser.add_argument("--page", default=None,
                        help="open on this page")
    parser.add_argument("--port", type=int, default=None,
                        help="serve on this port")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window")
    parser.add_argument("--legacy", action="store_true",
                        help="start the legacy schema v1 demo dashboard "
                             "instead (a different fab; its conclusion is a "
                             "documented constant)")
    parser.add_argument("--list", action="store_true",
                        help="list the datasets that exist and exit")
    parser.add_argument("--check", action="store_true",
                        help="run the whole workflow headlessly and exit")
    parser.add_argument("--where", action="store_true",
                        help="print the paths this installation uses and exit")
    arguments = parser.parse_args(argv)

    try:
        if arguments.where:
            return _where()
        if arguments.list:
            return _list_datasets()
        if arguments.check:
            return _check(arguments.dataset)

        if arguments.legacy:
            legacy = locations().workspace / "app" / "ops_dashboard.py"
            if not legacy.is_file():
                print(f"the legacy dashboard is not at {legacy}",
                      file=sys.stderr)
                return 2
            print(f"Starting the LEGACY schema v1 demo dashboard ({legacy}).")
            print("It reads the legacy database and narrates a conclusion "
                  "that is a constant; the product is `fabops-app`.")
            return _run_streamlit(legacy, (), port=arguments.port,
                                  headless=arguments.no_browser)

        if arguments.dataset:
            os.environ[DATASET_ENV] = arguments.dataset
        if arguments.page:
            os.environ[PAGE_ENV] = arguments.page
        return _run_streamlit(ui_path(), (), port=arguments.port,
                              headless=arguments.no_browser)
    except WorkspaceError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
