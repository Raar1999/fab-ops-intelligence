"""
recommend.py — the knowledge table, its loader, and the templating.

The loader is stricter than the boundary contract asks for in one respect and
looser in none. `FABOPS_VS_FABKG_BOUNDARY.md` §4 says an externally supplied
table is "loaded if present and schema-valid, ignored otherwise"; ignoring it
*silently* would mean a fab could run for months on a replacement table that
never loaded, so a rejection is reported through the return value and lands in
the report's provenance. Present-and-invalid is still not fatal, which is what
the contract requires.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

__all__ = [
    "ACTIONS_VERSION",
    "Action",
    "DEFAULT_KNOWLEDGE_PATH",
    "KNOWLEDGE_SCHEMA",
    "KnowledgeTable",
    "load_knowledge",
    "recommend",
]

ACTIONS_VERSION = "1.0.0"
KNOWLEDGE_SCHEMA = "fabops.knowledge/v1"
DEFAULT_KNOWLEDGE_PATH = Path(__file__).with_name("knowledge.json")


@dataclass(frozen=True)
class Action:
    """One thing to do, and why it is on the list."""

    kind: str            # check | containment | caution | context
    text: str
    because: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "action": self.text, "because": self.because}


@dataclass(frozen=True)
class KnowledgeTable:
    version: str
    source: str
    families: tuple[str, ...]
    always: tuple[str, ...]
    insufficient_evidence: tuple[str, ...]
    per_family: Mapping[str, Mapping[str, object]]
    combinations: tuple[Mapping[str, object], ...]
    rejected: str = ""

    def to_provenance(self) -> dict[str, object]:
        return {"schema": KNOWLEDGE_SCHEMA, "version": self.version,
                "source": self.source,
                **({"rejected_override": self.rejected}
                   if self.rejected else {})}


def _validate(payload: Mapping[str, object]) -> KnowledgeTable:
    if payload.get("schema") != KNOWLEDGE_SCHEMA:
        raise ValueError(f"not a {KNOWLEDGE_SCHEMA} document: "
                         f"schema is {payload.get('schema')!r}")
    families = tuple(payload.get("families") or ())
    if not families:
        raise ValueError("the table declares no evidence families")
    per_family = payload.get("per_family") or {}
    unknown = sorted(set(per_family) - set(families))
    if unknown:
        raise ValueError(f"per_family names undeclared families: {unknown}")
    for name, entry in per_family.items():
        if not isinstance(entry.get("checks"), list) or not entry["checks"]:
            raise ValueError(f"family {name!r} declares no checks")
    combinations = tuple(payload.get("combinations") or ())
    for entry in combinations:
        missing = sorted(set(entry.get("families") or ()) - set(families))
        if missing:
            raise ValueError(f"a combination names undeclared families: "
                             f"{missing}")
    return KnowledgeTable(
        version=str(payload.get("version", "")),
        source=str(payload.get("source", "")),
        families=families,
        always=tuple(payload.get("always") or ()),
        insufficient_evidence=tuple(payload.get("insufficient_evidence") or ()),
        per_family=per_family,
        combinations=combinations)


def load_knowledge(path: Path | str | None = None) -> KnowledgeTable:
    """The built-in table, or a schema-valid replacement.

    An absent override is the ordinary case and is not an error. An override
    that is present and *invalid* falls back to the built-in table and records
    why on the result, so a fab that thought it had replaced the table can find
    out from the report rather than from a silence.
    """
    built_in = _validate(json.loads(
        DEFAULT_KNOWLEDGE_PATH.read_text(encoding="utf-8")))
    if path is None:
        return built_in
    candidate = Path(path)
    if not candidate.exists():
        return built_in
    try:
        return _validate(json.loads(candidate.read_text(encoding="utf-8")))
    except (ValueError, KeyError, AttributeError, TypeError,
            json.JSONDecodeError) as error:
        return KnowledgeTable(
            version=built_in.version, source=built_in.source,
            families=built_in.families, always=built_in.always,
            insufficient_evidence=built_in.insufficient_evidence,
            per_family=built_in.per_family,
            combinations=built_in.combinations,
            rejected=f"{candidate.name}: {error}")


def _matching_combination(table: KnowledgeTable, families: Sequence[str]):
    """The most specific combination entry the evidence fully covers."""
    present = set(families)
    matches = [entry for entry in table.combinations
               if set(entry.get("families") or ()) <= present
               and len(entry.get("families") or ()) >= 2]
    matches.sort(key=lambda entry: (-len(entry["families"]),
                                    tuple(sorted(entry["families"]))))
    return matches[0] if matches else None


def recommend(families: Iterable[str], *, subject: str | None = None,
              distinguishable: bool | None = None,
              knowledge: KnowledgeTable | None = None) -> list[Action]:
    """Turn an evidence signature into an ordered list of things to do.

    `families` are the evidence families that led for the subject. An empty
    signature — which is what an abstaining investigation produces — yields the
    "insufficient evidence" list and no subject-specific action, deliberately.
    """
    table = knowledge or load_knowledge()
    ordered = [family for family in table.families if family in set(families)]

    actions: list[Action] = []
    seen: set[str] = set()

    def add(kind: str, text: str, because: str) -> None:
        if text in seen:
            return
        seen.add(text)
        actions.append(Action(kind=kind, text=text, because=because))

    if not ordered or subject is None:
        for text in table.insufficient_evidence:
            add("context", text, "no candidate separated from its peers")
        return actions

    for text in table.always:
        add("context", text, "applies to every investigation")

    combination = _matching_combination(table, ordered)
    if combination is not None:
        label = " + ".join(sorted(combination["families"]))
        for text in combination.get("checks", ()):
            add("check", text, f"{label} evidence converged on {subject}")

    for family in ordered:
        entry = table.per_family.get(family)
        if not entry:
            continue
        because = f"{family} evidence led for {subject}"
        for text in entry.get("checks", ()):
            add("check", text, because)
        caution = entry.get("caution")
        if caution:
            add("caution", str(caution), because)

    if distinguishable is False:
        add("caution",
            "The impact estimate is inside the spread this fab's peers show "
            "with nothing wrong; treat the die figure as an upper bound on "
            "what containment could recover, not as a loss.",
            "the subject's standing is within benign variation")

    for family in ordered:
        entry = table.per_family.get(family) or {}
        for text in entry.get("containment", ()):
            add("containment", text, f"{family} evidence led for {subject}")

    return actions
