"""
Tests for the static half of `docs/design/DIAGNOSIS_CONTRACT.md` §6.

This module was written before the engine existed, when the contract was still
a design gate: what could be tested then was the *checker* — the five
source-level rules the engine would have to pass — with a violating module to
prove the rules fire and a conforming one to prove they are not vacuous.

**The engine has since landed** (`src/fabops/diagnosis/`, ADR-029), so the last
test no longer skips: it runs every rule against the real package. The mutation
pair is kept rather than retired, because a checker that has never been seen to
fail proves nothing about the module it now passes.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fabsim.mechanisms import MECHANISM_NAMES

SRC = Path(__file__).resolve().parents[1] / "src"
CONTRACT = (Path(__file__).resolve().parents[1]
            / "docs" / "design" / "DIAGNOSIS_CONTRACT.md")

#: §6.4. The mechanism library's own names, plus the latent channel a fault
#: moves. An engine that carries this vocabulary is matching a catalogue
#: rather than reading a database.
FORBIDDEN_VOCABULARY = tuple(MECHANISM_NAMES) + ("edge_uniformity",)

#: §6.2. Anything that would put the hidden plane one path join away.
FORBIDDEN_PATHS = ("truth.json", "truth/", "scenarios/", "/truth")

#: §6.5. A parameter through which truth could arrive at all.
FORBIDDEN_PARAMETERS = frozenset(
    {"truth", "dataset", "realization", "scenario", "expectation"})


def static_violations(paths):
    """The five §6 checks, as (rule, file, detail) triples.

    Source-level, because that is the level the rules are written at: the
    point is that reaching truth requires deliberate circumvention rather
    than an accident of signature.
    """
    out: list[tuple[int, str, str]] = []
    for path in sorted(paths):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        name = path.name

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                if root in {"fabsim", "fabeval"}:
                    out.append((1, name, f"imports {root}"))

        for literal in ast.walk(tree):
            if not isinstance(literal, ast.Constant):
                continue
            if not isinstance(literal.value, str):
                continue
            text = literal.value
            for needle in FORBIDDEN_PATHS:
                if needle in text:
                    out.append((2, name, f"path reference {text!r}"))
            # §6.3: an entity literal is a hardcoded tool or chamber name.
            # `ETCH-02` is the shape: letters, a hyphen, two digits.
            if _looks_like_an_entity(text):
                out.append((3, name, f"entity literal {text!r}"))
            for word in FORBIDDEN_VOCABULARY:
                if word in text:
                    out.append((4, name, f"mechanism vocabulary {word!r}"))

        for word in FORBIDDEN_VOCABULARY:
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == word:
                    out.append((4, name, f"mechanism vocabulary {word!r}"))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = [a.arg for a in
                     args.posonlyargs + args.args + args.kwonlyargs]
            for arg in names:
                if arg in FORBIDDEN_PARAMETERS:
                    out.append((5, name, f"{node.name}({arg}=...)"))
            if node.name == "diagnose" and names[:1] not in (["db_path"],):
                out.append((5, name, f"diagnose{tuple(names)} is not "
                                     f"diagnose(db_path)"))
    return out


def _looks_like_an_entity(text: str) -> bool:
    head, _, tail = text.partition("-")
    return (bool(head) and head.isalpha() and head.isupper()
            and len(tail) == 2 and tail.isdigit())


VIOLATING = '''
"""A module that breaks every rule, so the checker can be shown to fire."""
from fabsim.mechanisms import MECHANISMS

TRUTH = "truth/truth.json"
SUSPECT = "ETCH-02"


def diagnose(dataset, truth, scenario="chamber_edge_uniformity"):
    return {"suspect": SUSPECT}
'''

CONFORMING = '''
"""A module that breaks none of them."""
import sqlite3
from pathlib import Path


def diagnose(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT wafer_id, yield_pct FROM wafer_yield")
        return {"candidates": sorted(rows, key=lambda r: r[1])}
'''


@pytest.fixture
def module(tmp_path):
    def write(source: str) -> list[Path]:
        path = tmp_path / "engine.py"
        path.write_text(source, encoding="utf-8")
        return [path]
    return write


# ------------------------------------------------------------- the checker


def test_every_rule_fires_on_a_module_that_breaks_it(module):
    """The mutation test. A checker nobody has seen fail proves nothing."""
    violations = static_violations(module(VIOLATING))
    fired = {rule for rule, _file, _detail in violations}
    assert fired == {1, 2, 3, 4, 5}, sorted(violations)


def test_nothing_fires_on_a_conforming_module(module):
    """And a checker that fires on everything is equally useless."""
    assert static_violations(module(CONFORMING)) == []


@pytest.mark.parametrize("text,expected", [
    ("ETCH-02", True), ("CMP-01", True),
    ("edge_cd", False), ("2026-08", False), ("ETCH-2", False),
])
def test_the_entity_literal_rule_is_narrow_enough_to_be_usable(text, expected):
    assert _looks_like_an_entity(text) is expected


# ----------------------------------------------------------- the repository


def test_the_legacy_demo_is_why_the_engine_must_be_new(module):
    """`fabops.config` hardcodes the answer, and that is the point.

    `DEMO_SUSPECT_TOOL = "ETCH-02"` is the audited v1 defect ADR-003 exists to
    retire; the contract grandfathers it only until the engine lands. Pinning
    that it still trips rule 3 keeps the two facts attached: the engine cannot
    be grown out of this module, it replaces it.
    """
    violations = static_violations([SRC / "fabops" / "config.py"])
    assert any(rule == 3 for rule, _f, _d in violations), violations


def test_the_engine_when_it_exists_passes_every_static_check():
    """Arms itself. Until then the contract is a design gate, not code."""
    engine = SRC / "fabops" / "diagnosis"
    if not engine.exists():
        pytest.skip("no diagnosis engine yet — see DIAGNOSIS_CONTRACT.md")
    assert static_violations(engine.rglob("*.py")) == []


def test_the_contract_document_is_present():
    """The design gate's deliverable. Referenced by ADR-025 and by A6."""
    assert CONTRACT.exists()
    text = CONTRACT.read_text(encoding="utf-8")
    assert "def diagnose(db_path: Path) -> Investigation" in text
    assert "insufficient_evidence" in text
