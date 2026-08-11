"""
Repository stability: the properties that stop debt accumulating quietly.

None of these is a feature. Each is a defect this repository has had, or a
defect its own audit found in the system it replaced, expressed as something a
reviewer no longer has to remember:

* a package that reaches backwards into another plane;
* an import cycle, which is how module ownership stops being a fact;
* a console script that names a function nobody kept;
* a version constant that stops moving;
* a data file the package needs and the wheel would not carry;
* an analytical surface that quietly reads the *legacy* database;
* a generated artifact that is not reproducible across processes;
* a dataset, a credential or a build product committed by accident.

They are fast, they need no dataset except where stated, and they are the ones
that must never be allowed to fail.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

#: The packages this repository owns, and the plane each one belongs to.
PLANES = {
    "fabsim": "generation",
    "fabops": "analysis",
    "fabeval": "evaluation",
    "fabapp": "product",
}

#: The analysis surfaces that read the **legacy schema v1** database, and are
#: allowed to. Everything else in `fabops` reads a schema v2 dataset at a path
#: the caller supplies, and a default would silently answer about another fab.
LEGACY_SURFACES = {
    "fabops/config.py", "fabops/db.py", "fabops/build_db.py",
    "fabops/charts.py", "fabops/investigation.py",
}


def modules_of(package: str) -> list[Path]:
    return sorted(p for p in (SRC / package).rglob("*.py")
                  if "__pycache__" not in p.parts)


def imports_of(path: Path, *, module_level_only: bool = False) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = tree.body if module_level_only else list(ast.walk(tree))
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def module_name(path: Path) -> str:
    relative = path.relative_to(SRC).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


# ------------------------------------------------------------- architecture


def test_the_planes_import_in_one_direction_only():
    """ADR-013's privilege table, extended by ADR-037, as an import rule.

    `fabsim` writes both planes and must not know its grader. `fabops` reads
    the observable plane and must reach neither of the others. `fabeval` reads
    both and may import either — it is the only component allowed to. `fabapp`
    is the product: it may import the simulator, because creating a dataset is
    a thing a user does, and the analysis, because reading one is the other
    thing — and it may **not** import the evaluator, which is what would let a
    screen score the dataset it is displaying.

    The direction still only runs one way. Nothing imports `fabapp`.
    """
    allowed = {"fabsim": set(), "fabops": set(),
               "fabeval": {"fabsim", "fabops"},
               "fabapp": {"fabsim", "fabops"}}
    for package, permitted in allowed.items():
        for path in modules_of(package):
            roots = {name.split(".")[0] for name in imports_of(path)}
            reached = (roots & set(PLANES)) - {package} - permitted
            assert not reached, (
                f"{path.relative_to(REPO).as_posix()} imports {sorted(reached)}, "
                f"which the {PLANES[package]} plane may not reach")


def test_there_are_no_module_level_import_cycles():
    """A cycle at *module* scope is how an import order becomes load-bearing.

    Two distinctions make this a real check rather than a noisy one. A package
    importing its own submodules is not a cycle — it is what a package is, and
    the `__init__` is where a public surface is assembled. And an import
    *inside a function* is the standard remedy for a genuine cycle, not an
    instance of one, so only module-scope imports are considered.
    """
    edges: dict[str, set[str]] = {}
    for package in PLANES:
        for path in modules_of(package):
            name = module_name(path)
            edges[name] = {
                target for target in imports_of(path, module_level_only=True)
                if target.split(".")[0] in PLANES
                and not target.startswith(name + ".")
                and not name.startswith(target + ".")}

    def reaches(start: str, goal: str, seen: set[str]) -> bool:
        for target in edges.get(start, ()):
            if target == goal:
                return True
            if target in edges and target not in seen:
                seen.add(target)
                if reaches(target, goal, seen):
                    return True
        return False

    cycles = sorted((name, target) for name in edges
                    for target in edges[name]
                    if target in edges and reaches(target, name, {target}))
    assert not cycles, f"module-level import cycles: {cycles}"


def test_only_the_legacy_surfaces_know_the_legacy_database():
    """The hazard the v2 packages had to avoid: `fabops.config.DB_PATH` names
    the schema v1 database, and v1 and v2 share table names where the intent
    carries over — so a v2 surface that fell back to it would answer about a
    different fab with no error anywhere."""
    for path in modules_of("fabops"):
        relative = path.relative_to(SRC).as_posix()
        if relative in LEGACY_SURFACES:
            continue
        assert "fabops.config" not in imports_of(path), relative


def test_every_owned_package_declares_a_version_and_they_are_all_semver():
    """A version that cannot move cannot warn anybody that generation did."""
    import fabapp
    import fabeval
    import fabops.diagnosis
    import fabops.impact
    import fabops.monitors
    import fabops.report
    import fabops.semantic
    import fabsim

    versions = {
        "fabsim": fabsim.__version__,
        "fabops.diagnosis": fabops.diagnosis.ENGINE_VERSION,
        "fabops.semantic": fabops.semantic.LAYER_VERSION,
        "fabops.monitors": fabops.monitors.MONITOR_VERSION,
        "fabops.impact": fabops.impact.IMPACT_VERSION,
        "fabops.report": fabops.report.REPORT_VERSION,
        "fabapp": fabapp.APP_VERSION,
    }
    for name, version in versions.items():
        parts = version.split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts), (
            name, version)


# ----------------------------------------------------------- packaging


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def test_every_console_script_resolves_to_something_callable(pyproject):
    """The audited P0 defect was a documented command that did not work. Every
    entry point is imported and checked here rather than discovered by a user."""
    import importlib

    scripts = pyproject["project"]["scripts"]
    assert len(scripts) >= 7, scripts
    for name, target in sorted(scripts.items()):
        module, _, attribute = target.partition(":")
        function = getattr(importlib.import_module(module), attribute)
        assert callable(function), name


def test_there_is_exactly_one_way_to_start_the_product(pyproject):
    """The productization's own stability rule: one entry point, not three.

    Before it there were two Streamlit files under `app/` and no command that
    started either from outside a checkout. A reader had to know which of the
    two was current — which is the "duplicate entry points" debt, and it is the
    kind that grows rather than sits still.

    What is checked is the shape rather than a count: exactly one console
    script starts the application, the superseded v2 workspace has not come
    back, and the legacy v1 dashboard is still there because ADR-010 keeps it.
    """
    scripts = pyproject["project"]["scripts"]
    assert scripts["fabops-app"] == "fabapp.cli:main"

    streamlit_apps = sorted(p.name for p in (REPO / "app").glob("*.py"))
    assert streamlit_apps == ["ops_dashboard.py"], streamlit_apps

    from fabapp.cli import ui_path

    assert ui_path().is_file()
    assert ui_path().is_relative_to(SRC / "fabapp")


def test_the_engine_and_the_demo_are_separately_named(pyproject):
    """ADR-003's note: typing the obvious command must not hand somebody the
    hard-coded story while they believe they ran the engine."""
    scripts = pyproject["project"]["scripts"]
    assert scripts["fabops-investigate"].startswith("fabops.investigation")
    assert scripts["fabops-diagnose"].startswith("fabops.diagnosis")


def test_the_packages_that_ship_data_declare_it(pyproject):
    """A `.sql` file and a `.json` table are load-bearing. An editable install
    finds them from the source tree and would hide their absence from a wheel."""
    declared = pyproject["tool"]["setuptools"]["package-data"]
    assert declared["fabops.semantic"] == ["*.sql"]
    assert declared["fabops.actions"] == ["*.json"]

    from fabops.actions import DEFAULT_KNOWLEDGE_PATH
    from fabops.semantic import layer_sql

    assert "CREATE TEMP VIEW" in layer_sql()
    assert json.loads(DEFAULT_KNOWLEDGE_PATH.read_text(encoding="utf-8"))


def test_no_dependency_arrives_from_the_other_project(pyproject):
    """`FABOPS_VS_FABKG_BOUNDARY.md` §4, anti-coupling rule 1. The whole test
    of the boundary is that this repository builds and runs with FabKG
    absent — which it does, because nothing here has heard of it."""
    declared = " ".join(pyproject["project"]["dependencies"])
    for extra in pyproject["project"]["optional-dependencies"].values():
        declared += " " + " ".join(extra)
    lowered = declared.lower()
    for token in ("fabkg", "neo4j", "rdflib", "networkx", "owlready",
                  "langchain", "openai", "anthropic", "transformers",
                  "chromadb", "faiss"):
        assert token not in lowered, token


# ------------------------------------------------------- reproducibility


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """One faulted dataset, built locally.

    This module belongs to no package and must not depend on another one's
    conftest — a stability check that only runs when the diagnosis fixtures do
    is a stability check with a precondition.
    """
    from tests.fabops.datasets import build_one

    root = tmp_path_factory.mktemp("stability")
    return build_one(("chamber_edge_uniformity", 42, str(root)))


def test_the_new_artifacts_are_reproducible_across_processes(dataset):
    """In-process determinism can hide a dependence on dictionary ordering
    that a fresh interpreter would expose. Both artifacts are produced twice,
    in two subprocesses, and compared byte for byte."""
    script = (
        "import json, sys;"
        "from fabops.monitors import monitor;"
        "from fabops.report import build_report;"
        "db = sys.argv[1];"
        "print(json.dumps([monitor(db).to_dict(),"
        "                  build_report(db, subject=sys.argv[2]).to_dict()],"
        "                 sort_keys=True))")
    runs = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script, dataset["db_path"],
             "ETCH-01/A"],
            capture_output=True, text=True, cwd=str(REPO), timeout=900)
        assert result.returncode == 0, result.stderr[-2000:]
        runs.append(result.stdout)
    assert runs[0] == runs[1]


def test_no_generated_artifact_carries_a_wall_clock(dataset):
    """`created_at` in a dataset manifest is the only wall-clock value in this
    project, and it is in no hash (ADR-023). An analysis artifact that carried
    one would be unreproducible by construction."""
    from fabops.monitors import monitor
    from fabops.report import build_report

    for payload in (monitor(dataset["db_path"]).to_dict(),
                    build_report(dataset["db_path"]).to_dict()):
        serialized = json.dumps(payload)
        for word in ("created_at", "generated_at", "timestamp", "run_at"):
            assert word not in serialized, word


# ------------------------------------------------------------ hygiene


def test_no_dataset_or_build_product_is_committed():
    """A schema v2 dataset is ~118 MB and its `truth/` sibling is the answer
    key. `.gitignore` excludes `data/scenarios/`; this checks the working tree
    as well, because an ignore rule added after the fact does not remove a
    file already tracked."""
    tracked = subprocess.run(["git", "ls-files"], capture_output=True,
                             text=True, cwd=str(REPO), timeout=120).stdout
    for pattern in ("data/scenarios/", "truth.json", ".egg-info/",
                    "__pycache__/", ".pytest_cache/"):
        offenders = [line for line in tracked.splitlines() if pattern in line]
        assert not offenders, (pattern, offenders[:5])


def test_no_credential_shaped_string_is_committed():
    """This project has no secrets and should acquire none by accident."""
    listing = subprocess.run(["git", "ls-files"], capture_output=True,
                             text=True, cwd=str(REPO), timeout=120).stdout
    suspicious = [name for name in listing.splitlines()
                  if Path(name).name in (".env", "credentials.json",
                                         "secrets.json", "id_rsa")]
    assert not suspicious, suspicious


def test_the_readme_names_every_console_script(pyproject):
    """A command that exists and is undocumented is a command nobody runs; one
    that is documented and does not exist is the audited P0 defect."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for name in pyproject["project"]["scripts"]:
        assert name in readme, name


def test_the_maintainer_scripts_are_at_the_root_and_not_console_scripts():
    """Two scripts write into the repository and neither is installed.

    `build_notebook.py` regenerates the notebook and `publish_readme.py`
    regenerates the README's benchmark section. Both live at the root rather
    than inside a package, and `publish_readme.py` is there for a reason worth
    keeping visible: ADR-024 §1 makes "`fabeval` writes nothing" an absolute,
    lexically checkable rule, so the rendering is in `fabeval.publish` and the
    writing is here. An exception for one well-behaved caller is how a rule
    like that stops being one.
    """
    import importlib

    for name in ("build_notebook", "publish_readme"):
        assert (REPO / f"{name}.py").exists(), name
    assert callable(importlib.import_module("publish_readme").main)
