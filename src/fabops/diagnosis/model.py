"""
model.py — the shapes `DIAGNOSIS_CONTRACT.md` §3 declares.

Data only: no database, no statistics, no decisions. Everything is frozen, so
a report cannot be edited after it is produced, and everything serializes to
plain JSON, because `fabops.investigation/v1` is a file-based export contract
(ADR-008) and a dataclass that cannot round-trip through JSON is not one.

`Entity` deliberately carries a `kind` rather than being a chamber. The
hypothesis space of an investigation is every dimension a wafer's history is
recorded against, and an engine that could only name equipment would answer
"which chamber?" to questions whose answer is a product or a recipe.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "ASSESSED",
    "CANDIDATE_STATUSES",
    "ENTITY_KINDS",
    "INVESTIGATION_SCHEMA",
    "NOT_ASSESSABLE",
    "Anchor",
    "Candidate",
    "Considered",
    "Entity",
    "Evidence",
    "Investigation",
    "Onset",
    "Window",
]

#: The export contract this artifact speaks (ADR-008), reused rather than
#: minted so the FabKG boundary inherits its versioning.
INVESTIGATION_SCHEMA = "fabops.investigation/v1"

#: The hypothesis dimensions, closed. Every one is a column a wafer's history
#: is recorded against in schema v2.
ENTITY_KINDS = ("chamber", "tool", "product", "recipe", "step", "operator")

ASSESSED = "assessed"
NOT_ASSESSABLE = "not_assessable"
CANDIDATE_STATUSES = (ASSESSED, NOT_ASSESSABLE)


@dataclass(frozen=True)
class Entity:
    """One hypothesis: a named thing a wafer's history is recorded against."""

    kind: str
    id: int | str
    name: str

    def __post_init__(self) -> None:
        if self.kind not in ENTITY_KINDS:
            raise ValueError(f"unknown entity kind {self.kind!r}; "
                             f"expected one of {ENTITY_KINDS}")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class Window:
    """The period examined, in days from the dataset's own time origin."""

    start_day: float
    end_day: float
    bin_days: float


@dataclass(frozen=True)
class Anchor:
    """A shared change point.

    Every candidate is scored at every anchor, and no candidate influences
    which anchors exist — that is the property `DIAGNOSIS_CONTRACT.md` §5.3
    makes structural, and it is why this record names the channel that
    proposed it but never an entity.
    """

    day: float
    channel: str
    statistic: float


@dataclass(frozen=True)
class Evidence:
    """One falsifiable claim: an analyst can re-run exactly this."""

    family: str
    channel: str
    statistic: str
    value: float
    comparison: str
    support: int
    rank: int
    of: int


@dataclass(frozen=True)
class Onset:
    """When the change began, as an interval, or nothing.

    Estimated separately from the evidence statistic. A point estimate read
    off the evidence maximum was measured at 19-33% of the horizon and is not
    reported as though it were better than that.
    """

    day: float
    interval: tuple[float, float]
    anchor_channel: str


@dataclass(frozen=True)
class Candidate:
    """One ranked hypothesis, with everything needed to falsify it."""

    entity: Entity
    status: str
    score: float
    p_value: float | None
    onsets: tuple[Onset, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    confounders: tuple[Mapping[str, Any], ...] = ()
    narrative: str = ""
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in CANDIDATE_STATUSES:
            raise ValueError(f"unknown candidate status {self.status!r}")
        if self.status == NOT_ASSESSABLE and not self.reason:
            raise ValueError("a not_assessable candidate must say why; "
                             "silently dropping it is what turns a competing "
                             "hypothesis into a walkover")


@dataclass(frozen=True)
class Considered:
    """A hypothesis that was examined and not offered, and why."""

    entity: Entity
    verdict: str
    detail: str


@dataclass(frozen=True)
class Investigation:
    """The artifact. `fabops.investigation/v1`."""

    dataset_id: str
    generated_by: str
    window: Window
    anchors: tuple[Anchor, ...]
    candidates: tuple[Candidate, ...]
    considered: tuple[Considered, ...]
    insufficient_evidence: bool
    abstention: Mapping[str, Any]
    schema: str = INVESTIGATION_SCHEMA
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        offered = [c for c in self.candidates if c.status == ASSESSED]
        if offered and not self.considered:
            raise ValueError(
                "considered[] is mandatory and non-empty whenever a candidate "
                "is offered; a conclusion that checked nothing else is the "
                "audited failure this contract exists to prevent")

    @property
    def top(self) -> Candidate | None:
        for candidate in self.candidates:
            if candidate.status == ASSESSED:
                return candidate
        return None

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able data, keys in a stable order."""
        return {
            "schema": self.schema,
            "dataset_id": self.dataset_id,
            "generated_by": self.generated_by,
            "window": asdict(self.window),
            "settings": dict(self.settings),
            "anchors": [asdict(a) for a in self.anchors],
            "insufficient_evidence": self.insufficient_evidence,
            "abstention": dict(self.abstention),
            "candidates": [_candidate_dict(c) for c in self.candidates],
            "considered": [
                {"entity": asdict(c.entity), "verdict": c.verdict,
                 "detail": c.detail} for c in self.considered],
        }


def _candidate_dict(candidate: Candidate) -> dict[str, Any]:
    return {
        "entity": asdict(candidate.entity),
        "status": candidate.status,
        "score": candidate.score,
        "p_value": candidate.p_value,
        "reason": candidate.reason,
        "onsets": [{"day": o.day, "interval": list(o.interval),
                    "anchor_channel": o.anchor_channel}
                   for o in candidate.onsets],
        "evidence": [asdict(e) for e in candidate.evidence],
        "confounders": [dict(c) for c in candidate.confounders],
        "narrative": candidate.narrative,
    }
