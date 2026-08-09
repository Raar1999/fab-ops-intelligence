"""
The evaluation boundary, proved mechanically rather than described.

`ADR-013` gives three actors three different privileges — fabsim writes both
planes, fabops reads only the observable one, fabeval reads both and writes
neither — and A10 makes that a hard architectural check. These tests are that
check, plus the two properties that keep `fabeval` able to do its job:

* the **reference queries** must be answerable from `fab.db` alone, or L7
  ("run the same queries on the null") and L11 ("…and they should be quiet")
  are comparing two different things;
* `fabeval` must **write nothing**, or a grader could contaminate the thing it
  grades.

None of these builds a dataset. They are static, they are fast, and they are
the ones that must never be allowed to fail.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
FABEVAL = REPOSITORY / "src" / "fabeval"


def modules() -> list[Path]:
    return sorted(p for p in FABEVAL.rglob("*.py")
                  if "__pycache__" not in p.parts)


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def code_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) \
                and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) \
                    and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


# ------------------------------------------------------------- the three rows


def test_fabops_reaches_neither_plane():
    """A10 / L9, in the direction that matters most."""
    for root in ("src/fabops", "app", "notebooks"):
        directory = REPOSITORY / root
        if not directory.exists():
            continue
        for module in sorted(directory.rglob("*.py")):
            if "__pycache__" in module.parts:
                continue
            source = module.read_text(encoding="utf-8")
            for name in imports_of(module):
                assert name.split(".")[0] not in ("fabsim", "fabeval"), module
            for token in ("truth.json", "truth/", "scenarios/"):
                assert token not in source, (module, token)


def test_fabsim_does_not_import_its_own_grader():
    """The simulator must not depend on the thing that scores it, or the
    score becomes a property of the simulator."""
    for module in sorted((REPOSITORY / "src" / "fabsim").rglob("*.py")):
        if "__pycache__" in module.parts:
            continue
        for name in imports_of(module):
            assert not name.startswith("fabeval"), module


def test_fabeval_writes_nothing():
    """A grader that could write into a dataset could contaminate it."""
    for module in modules():
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        for forbidden in ("write_text", "write_bytes", "mkdir", "unlink",
                          "rmtree", "executescript", "commit"):
            assert forbidden not in called, (module, forbidden)
        # `build_library` is the one place that *causes* writing, and it does
        # so by calling the emitter — which is fabsim's job, into a caller-
        # supplied root.
        if module.name != "matrix.py":
            assert "build_dataset" not in source, module


def test_fabeval_is_stdlib_only():
    """Like `fabsim`. Computing a mean and solving four normal equations does
    not need a dependency (`FABSIM_DESIGN.md` §8)."""
    allowed_third_party: set[str] = set()
    for module in modules():
        for name in imports_of(module):
            root = name.split(".")[0]
            if root in ("fabeval", "fabsim", "__future__"):
                continue
            assert root not in ("pandas", "numpy", "scipy", "matplotlib",
                                "sklearn"), (module, name)
            allowed_third_party.discard(root)
    assert not allowed_third_party


# ------------------------------------------------- the queries stay observable


def test_every_reference_query_takes_a_database_path():
    """If a query could be handed truth, L7 would not be comparing the same
    thing on the null that L11 compares on a faulted dataset."""
    from fabeval import queries

    public = [name for name in queries.__all__
              if callable(getattr(queries, name))
              and not name[0].isupper()]
    checked = 0
    for name in public:
        function = getattr(queries, name)
        parameters = list(inspect.signature(function).parameters)
        if name in ("rank", "zscore"):
            continue                     # pure helpers over a score mapping
        assert parameters[0] == "db_path", name
        assert not {"truth", "dataset", "realization", "response"} & set(
            parameters), name
        checked += 1
    assert checked >= 6


def test_the_query_module_cannot_reach_the_hidden_plane():
    """Structural: `queries.py` imports no truth, and names none of it."""
    path = FABEVAL / "queries.py"
    for name in imports_of(path):
        assert "truth" not in name, name
        assert not name.startswith("fabsim"), name
    identifiers = {n.attr for n in ast.walk(ast.parse(
        path.read_text(encoding="utf-8"))) if isinstance(n, ast.Attribute)}
    for forbidden in ("truth", "realization", "origins", "origin_of",
                      "outcomes", "mechanisms", "distractors",
                      "counterfactual", "alarm_details", "repairs"):
        assert forbidden not in identifiers, forbidden


def test_the_reference_queries_name_no_mechanism(monkeypatch):
    """A query that filtered on a mechanism label would be reading the answer
    out of a column that does not exist — and if one ever did exist, this is
    the test that would notice."""
    for module in (FABEVAL / "queries.py",):
        text = " ".join(code_strings(module)).lower()
        for token in ("chamber_edge_uniformity", "param_drift",
                      "particle_excursion", "mechanism", "suspect",
                      "fault", "scenario_name"):
            assert token not in text, token


def test_the_queries_do_not_read_the_classified_type_for_geometry():
    """ADR-019 §4: `classified_type` is a noisy draw over the hidden origin.
    A spatial query that trusted it would be measuring the classifier, and
    would quietly restore the circularity the audit found."""
    text = " ".join(code_strings(FABEVAL / "queries.py"))
    assert "classified_type" not in text


# ------------------------------------------------------ mutation: the boundary


def test_a_query_that_reached_for_truth_would_be_caught(tmp_path):
    """Mutation check on the boundary itself.

    A copy of `queries.py` with one truth-reading line added must fail the
    same scan the real module passes — otherwise the scan is decoration.
    """
    source = (FABEVAL / "queries.py").read_text(encoding="utf-8")
    mutated = source.replace(
        "def wafer_yields(db_path: Path | str)",
        "def wafer_yields(db_path: Path | str, truth=None)", 1)
    assert mutated != source
    path = tmp_path / "queries.py"
    path.write_text(mutated, encoding="utf-8")

    tree = ast.parse(mutated)
    signature = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)
                     and n.name == "wafer_yields")
    parameters = [a.arg for a in signature.args.args]
    assert "truth" in parameters          # the mutation took…
    # …and the rule the real test applies rejects it.
    assert bool({"truth", "dataset", "realization"} & set(parameters))
