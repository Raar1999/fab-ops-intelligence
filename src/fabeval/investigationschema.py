"""
investigationschema.py — the validator for `fabops.investigation/v1`.

The counterpart of `truthschema` for the other artifact, and it lives here for
the same reason: an engine that validated its own output against its own idea
of the contract would be marking its own homework. `fabeval` reads the report
and checks it against `docs/design/DIAGNOSIS_CONTRACT.md` §3.

It **never repairs**. A missing field, a wrong type, a status outside the
vocabulary, an offered candidate with an empty `considered[]`, a
`not_assessable` candidate with no reason, an onset interval that does not
bracket its own day — all are rejections, and a rejection names the field path
rather than saying "invalid".
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

__all__ = [
    "CANDIDATE_STATUSES",
    "ENTITY_KINDS",
    "INVESTIGATION_SCHEMA",
    "InvestigationValidationError",
    "validate_investigation",
]

INVESTIGATION_SCHEMA = "fabops.investigation/v1"
ENTITY_KINDS = frozenset(
    {"chamber", "tool", "product", "recipe", "step", "operator"})
CANDIDATE_STATUSES = frozenset({"assessed", "not_assessable"})
EVIDENCE_FAMILIES = frozenset(
    {"metrology", "fdc", "defects", "yield", "alarms"})


class InvestigationValidationError(ValueError):
    """A report that does not satisfy the contract, and exactly where."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def _require(condition: bool, path: str, reason: str) -> None:
    if not condition:
        raise InvestigationValidationError(path, reason)


def _field(mapping: Any, key: str, path: str, kinds: tuple[type, ...]) -> Any:
    _require(isinstance(mapping, Mapping), path, "expected an object")
    _require(key in mapping, f"{path}.{key}", "required field is missing")
    value = mapping[key]
    _require(isinstance(value, kinds), f"{path}.{key}",
             f"expected {' or '.join(k.__name__ for k in kinds)}, "
             f"got {type(value).__name__}")
    return value


def _entity(entity: Any, path: str) -> None:
    kind = _field(entity, "kind", path, (str,))
    _require(kind in ENTITY_KINDS, f"{path}.kind",
             f"{kind!r} is not one of {sorted(ENTITY_KINDS)}")
    _field(entity, "name", path, (str,))
    _require("id" in entity, f"{path}.id", "required field is missing")


def validate_investigation(report: Mapping[str, Any]) -> None:
    """Raise `InvestigationValidationError` unless the report is well formed."""
    schema = _field(report, "schema", "$", (str,))
    _require(schema == INVESTIGATION_SCHEMA, "$.schema",
             f"expected {INVESTIGATION_SCHEMA!r}, got {schema!r}")
    _field(report, "dataset_id", "$", (str,))
    generated_by = _field(report, "generated_by", "$", (str,))
    _require("/" in generated_by, "$.generated_by",
             "must carry the engine name and its version")

    window = _field(report, "window", "$", (Mapping,))
    for key in ("start_day", "end_day", "bin_days"):
        _field(window, key, "$.window", (int, float))
    _require(window["end_day"] > window["start_day"], "$.window",
             "the window must have positive length")

    anchors = _field(report, "anchors", "$", (list, tuple))
    for index, anchor in enumerate(anchors):
        path = f"$.anchors[{index}]"
        day = _field(anchor, "day", path, (int, float))
        _require(window["start_day"] <= day <= window["end_day"], f"{path}.day",
                 "an anchor must lie inside the window it was used on")
        _field(anchor, "channel", path, (str,))

    _field(report, "insufficient_evidence", "$", (bool,))
    abstention = _field(report, "abstention", "$", (Mapping,))
    p_value = _field(abstention, "p_familywise", "$.abstention", (int, float))
    alpha = _field(abstention, "alpha", "$.abstention", (int, float))
    _require(0.0 <= p_value <= 1.0, "$.abstention.p_familywise",
             "a probability must lie in [0, 1]")
    _require(0.0 < alpha < 1.0, "$.abstention.alpha",
             "a level must lie strictly inside (0, 1)")
    _require(report["insufficient_evidence"] == (p_value > alpha),
             "$.insufficient_evidence",
             "must agree with the family-wise probability and the level")

    candidates = _field(report, "candidates", "$", (list, tuple))
    considered = _field(report, "considered", "$", (list, tuple))

    offered = 0
    for index, candidate in enumerate(candidates):
        path = f"$.candidates[{index}]"
        _entity(_field(candidate, "entity", path, (Mapping,)), f"{path}.entity")
        status = _field(candidate, "status", path, (str,))
        _require(status in CANDIDATE_STATUSES, f"{path}.status",
                 f"{status!r} is not one of {sorted(CANDIDATE_STATUSES)}")
        _field(candidate, "score", path, (int, float))
        if status == "not_assessable":
            reason = candidate.get("reason")
            _require(isinstance(reason, str) and reason.strip(),
                     f"{path}.reason",
                     "a not_assessable candidate must say why it could not be "
                     "scored; a hypothesis that disappears has been rejected "
                     "without being asked")
            continue
        offered += 1
        p_candidate = _field(candidate, "p_value", path, (int, float))
        _require(0.0 <= p_candidate <= 1.0, f"{path}.p_value",
                 "a probability must lie in [0, 1]")
        evidence = _field(candidate, "evidence", path, (list, tuple))
        _require(bool(evidence), f"{path}.evidence",
                 "an offered candidate must carry falsifiable evidence")
        for position, item in enumerate(evidence):
            item_path = f"{path}.evidence[{position}]"
            family = _field(item, "family", item_path, (str,))
            _require(family in EVIDENCE_FAMILIES, f"{item_path}.family",
                     f"{family!r} is not a declared evidence family")
            for key, kinds in (("channel", (str,)), ("statistic", (str,)),
                               ("value", (int, float)),
                               ("comparison", (str,)), ("support", (int,)),
                               ("rank", (int,)), ("of", (int,))):
                _field(item, key, item_path, kinds)
            _require(1 <= item["rank"] <= item["of"], f"{item_path}.rank",
                     "a rank must lie inside the population it ranks against")
        for position, onset in enumerate(candidate.get("onsets", ())):
            onset_path = f"{path}.onsets[{position}]"
            day = _field(onset, "day", onset_path, (int, float))
            interval = _field(onset, "interval", onset_path, (list, tuple))
            _require(len(interval) == 2, f"{onset_path}.interval",
                     "an interval is a pair")
            _require(interval[0] <= day <= interval[1],
                     f"{onset_path}.interval",
                     "an interval must bracket the day it is an interval for")

    if offered:
        _require(bool(considered), "$.considered",
                 "considered[] is mandatory and non-empty whenever a candidate "
                 "is offered")
    for index, item in enumerate(considered):
        path = f"$.considered[{index}]"
        _entity(_field(item, "entity", path, (Mapping,)), f"{path}.entity")
        _field(item, "verdict", path, (str,))
        _field(item, "detail", path, (str,))
