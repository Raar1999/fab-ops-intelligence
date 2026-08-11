"""
explain.py — the engine's own reasoning, rearranged for a human to read.

    explain_investigation(artifact) -> Outcome
    explain_candidate(artifact, candidate) -> Explanation

Every check this module produces is a **restatement of a field that is already
in `fabops.investigation/v1`**, together with the number from that field which
decides it. Nothing is inferred, nothing is scored, no threshold is invented and
no sentence is written that the artifact does not support. Each `Check` carries
the JSON path it came from so a reader can go and look, which is the difference
between explaining a result and narrating one.

That constraint is what stops this file becoming the thing the audit found in
the predecessor: a surface whose prose told the reader what to conclude. The
engine abstains on every dataset this project can build, and the honest form of
that on a screen is a list of criteria with ticks and crosses against the
engine's own numbers — including, prominently, the crosses.

**Two groups, because they answer different questions.**

*Why was this candidate considered?* — the conditions that had to hold for the
engine to be able to say anything about it at all. A candidate can pass every
one of these and still not be named.

*What would be needed to attribute?* — the conditions the engine requires
before it offers a candidate rather than abstaining. These are where the
crosses usually are, and the module refuses to soften them: the family-wise
check reads the same `insufficient_evidence` flag that `fabops-diagnose`
prints, so a screen cannot disagree with the command line.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "ATTRIBUTION_CRITERIA",
    "CONSIDERATION_CRITERIA",
    "Check",
    "Explanation",
    "Outcome",
    "explain_candidate",
    "explain_investigation",
    "families_of",
    "leading_families",
]

#: The two groups' criteria, named once so a test can assert the screens and
#: this module agree about what is being checked.
CONSIDERATION_CRITERIA = ("sufficient peer structure",
                          "temporal departure located",
                          "relevant evidence family",
                          "exposure mix controlled")
ATTRIBUTION_CRITERIA = ("leads its peers in a family",
                        "cross-family agreement",
                        "candidate-level significance",
                        "family-wise significance")


@dataclass(frozen=True)
class Check:
    """One criterion, its verdict, and the number in the artifact behind it."""

    claim: str
    met: bool
    evidence: str
    #: Where in `fabops.investigation/v1` the evidence was read from.
    source: str

    @property
    def mark(self) -> str:
        return "PASS" if self.met else "NOT MET"

    def to_dict(self) -> dict[str, Any]:
        return {"claim": self.claim, "met": self.met,
                "evidence": self.evidence, "source": self.source}


@dataclass(frozen=True)
class Explanation:
    """Why one candidate was considered, and what attribution would need."""

    entity: str
    kind: str
    status: str
    considered: tuple[Check, ...]
    attribution: tuple[Check, ...]

    @property
    def attributable(self) -> bool:
        return all(check.met for check in self.attribution)

    def to_dict(self) -> dict[str, Any]:
        return {"entity": self.entity, "kind": self.kind,
                "status": self.status,
                "considered": [c.to_dict() for c in self.considered],
                "attribution": [c.to_dict() for c in self.attribution]}


@dataclass(frozen=True)
class Outcome:
    """The report-level answer, and the arithmetic that produced it."""

    insufficient_evidence: bool
    headline: str
    reason: str
    p_familywise: float | None
    alpha: float | None
    permutations: int | None
    strata: tuple[str, ...]
    anchors: tuple[float, ...]
    window: Mapping[str, Any]
    assessed: int
    not_assessable: int
    #: Every distinct reason the engine gave for refusing to score a candidate,
    #: with how many candidates each covers. A boundary that applies to half
    #: the fab should be visible as such rather than repeated in a long table.
    not_assessable_reasons: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "insufficient_evidence": self.insufficient_evidence,
            "headline": self.headline, "reason": self.reason,
            "p_familywise": self.p_familywise, "alpha": self.alpha,
            "permutations": self.permutations, "strata": list(self.strata),
            "anchors": list(self.anchors), "window": dict(self.window),
            "assessed": self.assessed, "not_assessable": self.not_assessable,
            "not_assessable_reasons": [
                {"reason": reason, "candidates": count}
                for reason, count in self.not_assessable_reasons],
        }


def families_of(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    """Every evidence family this candidate was scored in, in a fixed order."""
    return tuple(sorted({str(entry["family"])
                         for entry in candidate.get("evidence", ())}))


def leading_families(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    """The families the candidate ranked *first* in.

    Rank 1 is the bar, and it is the same bar `fabops.report` applies when it
    decides which engineering checks a subject earns — reused rather than
    restated so the screen and the artifact cannot disagree.
    """
    return tuple(sorted({str(entry["family"])
                         for entry in candidate.get("evidence", ())
                         if entry.get("rank") == 1}))


def explain_investigation(artifact: Mapping[str, Any]) -> Outcome:
    """The report-level outcome, read off the artifact."""
    abstention = dict(artifact.get("abstention", {}))
    candidates = list(artifact.get("candidates", ()))
    assessed = [c for c in candidates if c.get("status") == "assessed"]
    refused = [c for c in candidates if c.get("status") == "not_assessable"]

    reasons: dict[str, int] = {}
    for candidate in refused:
        reason = str(candidate.get("reason") or "no reason recorded")
        reasons[reason] = reasons.get(reason, 0) + 1

    insufficient = bool(artifact.get("insufficient_evidence"))
    p_familywise = abstention.get("p_familywise")
    alpha = abstention.get("alpha")
    if insufficient:
        headline = "Insufficient evidence — no candidate is offered."
    else:
        leader = assessed[0]["entity"]["id"] if assessed else "a candidate"
        headline = f"A leading candidate is offered: {leader}."

    return Outcome(
        insufficient_evidence=insufficient,
        headline=headline,
        reason=str(abstention.get("reason", "")),
        p_familywise=_as_float(p_familywise),
        alpha=_as_float(alpha),
        permutations=_as_int(abstention.get("permutations")),
        strata=tuple(str(s) for s in abstention.get("strata", ())),
        anchors=tuple(_as_float(a.get("day")) or 0.0
                      for a in artifact.get("anchors", ())),
        window=dict(artifact.get("window", {})),
        assessed=len(assessed),
        not_assessable=len(refused),
        not_assessable_reasons=tuple(sorted(reasons.items(),
                                            key=lambda row: (-row[1], row[0]))),
    )


def explain_candidate(artifact: Mapping[str, Any],
                      candidate: Mapping[str, Any]) -> Explanation:
    """Why this candidate was considered, and what naming it would require."""
    entity = candidate.get("entity", {})
    evidence: Sequence[Mapping[str, Any]] = candidate.get("evidence", ()) or ()
    onsets: Sequence[Mapping[str, Any]] = candidate.get("onsets", ()) or ()
    confounders: Sequence[Mapping[str, Any]] = \
        candidate.get("confounders", ()) or ()
    families = families_of(candidate)
    leading = leading_families(candidate)
    abstention = dict(artifact.get("abstention", {}))
    alpha = _as_float(abstention.get("alpha"))
    assessed = candidate.get("status") == "assessed"

    peers = max((int(entry.get("of", 0)) for entry in evidence), default=0)
    considered = (
        Check(
            claim="sufficient peer structure",
            met=assessed and peers >= 3,
            evidence=(f"scored against {peers - 1} same-role peers on "
                      f"{len(evidence)} channel(s)" if assessed and peers
                      else str(candidate.get("reason")
                               or "the engine did not score this candidate")),
            source="candidates[].status, candidates[].evidence[].of"),
        Check(
            claim="temporal departure located",
            met=bool(onsets),
            evidence=("; ".join(
                f"change point at day {float(o['day']):.0f} "
                f"(interval {float(o['interval'][0]):.0f}-"
                f"{float(o['interval'][1]):.0f} d, proposed by "
                f"{o.get('anchor_channel')})" for o in onsets)
                if onsets else
                "no anchor at which this candidate departed upwards"),
            source="candidates[].onsets[]"),
        Check(
            claim="relevant evidence family",
            met=bool(families),
            evidence=(f"{len(families)} family/families scored: "
                      f"{', '.join(families)}" if families else
                      "no evidence family could be scored for this candidate"),
            source="candidates[].evidence[].family"),
        Check(
            claim="exposure mix controlled",
            met=bool(confounders),
            evidence=_confounder_sentence(confounders),
            source="candidates[].confounders[]"),
    )

    best = min((entry for entry in evidence),
               key=lambda entry: (int(entry.get("rank", 10 ** 6)),
                                  str(entry.get("channel", ""))),
               default=None)
    p_value = _as_float(candidate.get("p_value"))
    p_familywise = _as_float(abstention.get("p_familywise"))

    attribution = (
        Check(
            claim="leads its peers in a family",
            met=bool(leading),
            evidence=(f"rank 1 on {', '.join(leading)}" if leading else
                      (f"best standing is rank {best.get('rank')} of "
                       f"{best.get('of')} on {best.get('channel')}"
                       if best is not None else "no standing to rank")),
            source="candidates[].evidence[].rank"),
        Check(
            claim="cross-family agreement",
            met=len(leading) >= 2,
            evidence=(f"leads {len(leading)} of {len(families)} scored "
                      f"families; the engine combines families by Fisher, so "
                      f"one family alone cannot clear the level"),
            source="candidates[].evidence[].family, .rank"),
        Check(
            claim="candidate-level significance",
            met=(p_value is not None and alpha is not None
                 and p_value <= alpha),
            evidence=(f"permutation p = {p_value:.4f} against alpha = {alpha}"
                      if p_value is not None and alpha is not None else
                      "this candidate carries no permutation p-value"),
            source="candidates[].p_value, abstention.alpha"),
        Check(
            claim="family-wise significance",
            met=not bool(artifact.get("insufficient_evidence")),
            evidence=(f"family-wise p = {p_familywise} against alpha = {alpha}"
                      f" over {abstention.get('permutations')} permutations"
                      if p_familywise is not None else
                      "no family-wise statement was produced"),
            source="insufficient_evidence, abstention.p_familywise"),
    )

    return Explanation(
        entity=str(entity.get("id", "")), kind=str(entity.get("kind", "")),
        status=str(candidate.get("status", "")),
        considered=considered, attribution=attribution)


def _confounder_sentence(confounders: Sequence[Mapping[str, Any]]) -> str:
    if not confounders:
        return ("no exposure-mix control applies to this candidate kind; the "
                "control is computed for equipment candidates")
    parts = []
    for entry in confounders:
        retained = entry.get("retained")
        parts.append(
            f"{entry.get('channel')}: {entry.get('value')} -> "
            f"{entry.get('value_controlled')} after residualizing against "
            f"product and week"
            + (f" ({float(retained):.0%} retained)"
               if isinstance(retained, (int, float)) else ""))
    return "; ".join(parts)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
