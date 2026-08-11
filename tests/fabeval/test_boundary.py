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
import json
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
FABEVAL = REPOSITORY / "src" / "fabeval"


def modules() -> list[Path]:
    return sorted(p for p in FABEVAL.rglob("*.py")
                  if "__pycache__" not in p.parts)


def imports_of_source(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def imports_of(path: Path) -> set[str]:
    return imports_of_source(path.read_text(encoding="utf-8"))


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


def test_no_code_surface_reaches_a_plane_it_is_not_entitled_to():
    """A10 / L9, in the direction that matters most.

    Every *code surface*, not every `.py`. Both the reader and the **table of
    what each root may reach** are imported from `fabeval.leakage` rather than
    reimplemented here, so this test and the shipped check cannot drift into
    two definitions of the rule — which is exactly how the notebook came to be
    listed as covered while never being read.

    Four roots now, not three. `src/fabapp` is the product plane and its row is
    a different rule: it may import the simulator, because generating a dataset
    is what a product does, and it may name the configuration directory,
    because choosing a scenario is the user's own act. It may not import the
    grader and may not name the hidden plane at all (ADR-037).
    """
    from fabeval.leakage import CODE_PLANE_ROOTS, code_surfaces

    assert {root for root, _imports, _tokens in CODE_PLANE_ROOTS} == {
        "src/fabops", "app", "notebooks", "src/fabapp"}

    scanned: list[str] = []
    for root, forbidden_imports, forbidden_tokens in CODE_PLANE_ROOTS:
        directory = REPOSITORY / root
        if not directory.exists():
            continue
        for label, source in code_surfaces(directory):
            scanned.append(label)
            for name in imports_of_source(source):
                assert name.split(".")[0] not in forbidden_imports, label
            for token in forbidden_tokens:
                assert token not in source.lower(), (label, token)
    assert len(scanned) >= 6, scanned


def test_the_product_plane_is_scanned_and_is_the_only_root_that_may_generate():
    """The row that was added, asserted rather than assumed.

    Two failure modes this closes. A product root that was listed and matched
    no file would be a root that cannot fail — the notebook's defect, again. And
    a permission granted to the product plane must not silently widen to the
    analysis plane, so the rows are compared rather than each read alone.
    """
    from fabeval.leakage import CODE_PLANE_ROOTS, code_surfaces

    rows = {root: (imports, tokens)
            for root, imports, tokens in CODE_PLANE_ROOTS}

    assert "fabsim" not in rows["src/fabapp"][0]
    assert "fabeval" in rows["src/fabapp"][0]
    assert rows["src/fabapp"][1] == ("truth",)
    for root in ("src/fabops", "app", "notebooks"):
        assert "fabsim" in rows[root][0], root

    labels = [label for label, _ in
              code_surfaces(REPOSITORY / "src" / "fabapp")]
    assert len(labels) >= 8, labels


def test_the_notebook_is_one_of_the_surfaces_that_gets_scanned():
    """The gap this closes: `notebooks/` was named as a root and then, because
    the scan globbed `*.py` into a directory holding one `.ipynb`, contributed
    nothing at all. A root that matches no file is a root that cannot fail."""
    from fabeval.leakage import code_surfaces

    labels = [label for label, _ in code_surfaces(REPOSITORY / "notebooks")]
    assert labels, "notebooks/ contributes no code surface to L9"
    assert any(label.endswith(".ipynb") for label in labels), labels


def test_l9_fires_on_a_notebook_that_reaches_for_the_hidden_plane(tmp_path):
    """The mutation. A checker nobody has seen fail proves nothing."""
    from fabeval.leakage import code_surfaces

    (tmp_path / "leaky.ipynb").write_text(json.dumps({"cells": [
        {"cell_type": "markdown", "source": ["# ordinary prose\n"]},
        {"cell_type": "code", "source": [
            "from fabsim.emit import build_dataset\n",
            "answer = open('data/scenarios/x/truth/truth.json')\n"]},
    ]}), encoding="utf-8")

    surfaces = code_surfaces(tmp_path)
    assert [label for label, _ in surfaces] == ["leaky.ipynb"]
    _label, source = surfaces[0]

    assert "ordinary prose" not in source, "markdown is not a code surface"
    assert any(name.split(".")[0] == "fabsim"
               for name in imports_of_source(source)), source
    assert "truth/" in source


def test_l9_does_not_fire_on_a_path_that_only_appears_in_a_stored_output(
        tmp_path):
    """And it must not fire on one.

    An executed notebook carries the output of every cell it ran. A dataset
    path printed by a past run is a record of that run, not an import, and a
    check that read stored outputs would flag every notebook this project ships
    executed — which is how a check gets switched off.
    """
    from fabeval.leakage import notebook_source

    path = tmp_path / "executed.ipynb"
    path.write_text(json.dumps({"cells": [{
        "cell_type": "code",
        "source": ["print('done')\n"],
        "outputs": [{"output_type": "stream", "name": "stdout",
                     "text": ["wrote data/scenarios/x/truth/truth.json\n"]}],
    }]}), encoding="utf-8")

    source = notebook_source(path)
    assert source.strip() == "print('done')"
    assert "truth/" not in source


def test_fabsim_does_not_import_its_own_grader():
    """The simulator must not depend on the thing that scores it, or the
    score becomes a property of the simulator."""
    for module in sorted((REPOSITORY / "src" / "fabsim").rglob("*.py")):
        if "__pycache__" in module.parts:
            continue
        for name in imports_of(module):
            assert not name.startswith("fabeval"), module


#: The two modules allowed to *cause* writing, and only by calling the
#: emitter into a root the caller supplies. `matrix.py` builds the Phase 1
#: acceptance library; `benchmark.py` builds a Phase 6 population, which is
#: the same act at a different scale. The list is an allowlist rather than a
#: rule about content so that a third writer has to arrive in a diff somebody
#: reads.
MAY_CALL_THE_EMITTER = {"matrix.py", "benchmark.py"}


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
        if module.name not in MAY_CALL_THE_EMITTER:
            assert "build_dataset" not in source, module


def test_the_emitter_is_never_called_without_a_caller_supplied_root():
    """The property the allowlist above is a proxy for.

    `build_dataset`'s `root` defaults to `data/scenarios/`, so a call that
    omits it writes into the repository — which is how a grader would come to
    own datasets it also grades. Requiring the keyword makes the destination
    the caller's decision at every call site, and it is checkable rather than
    remembered.
    """
    for module in modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if name != "build_dataset":
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            assert "root" in keywords, (
                f"{module.name}:{node.lineno} calls build_dataset without an "
                f"explicit root=, so it would write into the repository")


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
