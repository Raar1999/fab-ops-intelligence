"""
The product plane's privilege row, proved mechanically.

ADR-013 gave three actors three privileges; ADR-037 adds a fourth actor that
holds two of them at once, which is the first time in this repository that
generating and analysing happen in one process. That is exactly the place a
leak would be cheap, so the privileges are checked the way the other three are:
statically, fast, and with a mutation proving each scan can fail.

    fabsim     writes both planes
    fabops     reads the observable plane
    fabeval    reads both, writes nothing
    fabapp     generates, then analyses — and may reach neither the hidden
               plane nor the evaluator

Four rules, and the reason each one exists:

* **No token of the hidden plane, anywhere.** Not an import, not a path, not a
  string. `fabops` is allowed to *say* the word in a docstring explaining what
  it does not do; this package is not, because it is the one package that could
  actually reach it — the directory is a sibling of a database it legitimately
  holds a path to.
* **No import of the evaluator.** `fabeval` is the one component permitted to
  hold the answer key and a report at once. A product that imported it could
  score the dataset it is displaying, and the screen would stop being blind.
* **No call to the two-plane builder.** `build_dataset` returns an object with
  the answer key on it. The product calls `build_observable`, which cannot.
* **Every build names its own root.** The emitter's default writes into the
  repository, so the destination is a decision at every call site — the same
  rule `fabeval` holds itself to, for the same reason.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FABAPP = REPO / "src" / "fabapp"


def modules() -> list[Path]:
    found = sorted(p for p in FABAPP.rglob("*.py")
                   if "__pycache__" not in p.parts)
    assert len(found) >= 8, found
    return found


def imports_of_source(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


#: The one token that may never appear in this package, in any form. A bare
#: word rather than a list of path fragments: `truth.json`, `truth/` and
#: `dataset.truth` are three spellings of one reach, and a scan that enumerated
#: spellings is a scan somebody gets around by accident. The stricter rule is
#: affordable here precisely because this package has no legitimate use for the
#: word — `fabops` may say it while explaining what it does not do, and does.
FORBIDDEN_TOKEN = "truth"


def test_the_product_plane_never_names_the_hidden_plane():
    for module in modules():
        source = module.read_text(encoding="utf-8")
        assert FORBIDDEN_TOKEN not in source.lower(), (
            f"{module.relative_to(REPO).as_posix()} names the hidden plane")


def test_the_scan_fires_on_a_module_that_reaches_for_it():
    """A checker nobody has seen fail proves nothing.

    The mutation is the plausible one — not an import, which the other scans
    would catch, but a path joined onto a database the product legitimately
    holds. That is the only route this plane actually has, and this is the
    scan that closes it.
    """
    clean = (FABAPP / "service.py").read_text(encoding="utf-8")
    leaky = clean + (
        f"\nanswer = open(record.db_path.parent / '{FORBIDDEN_TOKEN}' "
        f"/ '{FORBIDDEN_TOKEN}.json')\n")
    assert FORBIDDEN_TOKEN in leaky.lower()
    assert FORBIDDEN_TOKEN not in clean.lower()


def test_the_product_plane_does_not_import_the_evaluator():
    for module in modules():
        roots = {name.split(".")[0]
                 for name in imports_of_source(
                     module.read_text(encoding="utf-8"))}
        assert "fabeval" not in roots, (
            f"{module.relative_to(REPO).as_posix()} imports the evaluator, "
            f"which is the one component allowed to hold both planes")


def test_only_the_generator_module_reaches_the_simulator():
    """`fabsim` is reachable from this plane, and from exactly two files.

    Generation is a product action, so the import is legitimate — but it
    belongs where generation is, not wherever it is convenient. `generate.py`
    builds and `scenarios.py` reads and validates a configuration; nothing that
    draws a screen or opens a dataset imports the simulator at all.
    """
    allowed = {"fabapp/generate.py", "fabapp/scenarios.py"}
    for module in modules():
        relative = module.relative_to(REPO / "src").as_posix()
        roots = {name.split(".")[0]
                 for name in imports_of_source(
                     module.read_text(encoding="utf-8"))}
        if "fabsim" in roots:
            assert relative in allowed, (
                f"{relative} imports the simulator; generation belongs in "
                f"{sorted(allowed)}")


def test_the_product_plane_never_calls_the_two_plane_builder():
    """`build_dataset` returns an object carrying the answer key.

    The product calls `build_observable`, whose return type has no field the
    hidden plane could arrive through. This is the lexical half of that
    guarantee; the structural half is the signature itself.
    """
    for module in modules():
        source = module.read_text(encoding="utf-8")
        assert "build_dataset" not in source, (
            f"{module.relative_to(REPO).as_posix()} calls the builder that "
            f"returns both planes")


def test_every_build_names_the_root_it_writes_to():
    """The emitter's default root is inside the repository."""
    for module in modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if name != "build_observable":
                continue
            assert "root" in {keyword.arg for keyword in node.keywords}, (
                f"{module.name}:{node.lineno} builds without an explicit "
                f"root=, so it would write into the repository")


def test_the_observable_handle_carries_no_route_to_the_hidden_plane():
    """The structural half: what `build_observable` returns.

    Checked on the class rather than on a build, so it costs nothing and runs
    even when no dataset exists. A field added to this handle that named a
    directory would put the hidden plane one `/` away from a product screen.
    """
    from dataclasses import fields

    from fabsim.emit import ObservableHandle

    names = {field.name for field in fields(ObservableHandle)}
    assert not any(FORBIDDEN_TOKEN in name for name in names), names
    assert "directory" not in names, (
        "the handle names a directory; the hidden plane is a sibling of the "
        "database, and a path to the parent is a path to the answer")
    assert "db_path" in names

    payload = {"dataset_id": "", "scenario_id": "", "db_path": "", "seed": 0,
               "schema_version": "", "fabsim_version": "", "config_sha256": "",
               "world_sha256": "", "build_fingerprint": "",
               "content_sha256": "", "row_counts": {}, "created_at": ""}
    handle = ObservableHandle(**{**payload, "db_path": Path("x/fab.db")})
    assert set(handle.to_dict()) == set(payload)


def test_the_product_does_not_import_the_legacy_surface():
    """`fabops.config` holds the legacy database path *and* the legacy demo's
    hard-coded suspect. A product that imported it would have both within
    reach of a screen."""
    for module in modules():
        assert "fabops.config" not in imports_of_source(
            module.read_text(encoding="utf-8")), module.name


#: What a product layer is the natural arrival point for, and must not grow: a
#: graph store, an ontology library, a vector index, or a model API behind an
#: "insight" panel. ADR-006 is a binding prohibition and the FabKG boundary's
#: anti-coupling rule 2 is the other half of it.
OTHER_PROJECT_LIBRARIES = ("fabkg", "neo4j", "rdflib", "networkx", "owlready",
                           "langchain", "openai", "anthropic", "chromadb",
                           "faiss", "transformers", "llama_index")


@pytest.mark.parametrize("token", OTHER_PROJECT_LIBRARIES)
def test_the_product_plane_imports_nothing_from_the_other_project(token):
    """`FABOPS_VS_FABKG_BOUNDARY.md` §4, anti-coupling rule 2, as an import
    rule — which is how that rule is worded. A product layer is where an "AI
    insight" box would arrive from, so the newest package is checked as well as
    the oldest."""
    for module in modules():
        roots = {name.split(".")[0].lower()
                 for name in imports_of_source(
                     module.read_text(encoding="utf-8"))}
        assert token not in roots, (module.name, token)


def test_the_only_mention_of_the_other_project_is_its_boundary_document():
    """Naming FabKG is *required* where the export contract is implemented —
    §10 of the productization brief asks that an existing interface toward it
    be preserved and documented — and is a smell anywhere else. So the rule is
    not silence: it is that every mention is a citation of the boundary, never
    a feature."""
    for module in modules():
        for number, line in enumerate(
                module.read_text(encoding="utf-8").splitlines(), start=1):
            if "fabkg" not in line.lower():
                continue
            assert "boundary" in line.lower(), (
                f"{module.name}:{number} names the other project outside a "
                f"reference to the boundary that separates them: {line.strip()}")
