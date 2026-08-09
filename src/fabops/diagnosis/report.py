"""
report.py — assembling the investigation, including the parts that argue
against it.

Three things here are not presentation:

* **`considered[]`.** The audited predecessor narrated one conclusion and
  checked nothing else. A report that offers a candidate must also say which
  rivals it examined and why they lost - including rivals of a *different
  kind*, because "a chamber" and "a product" are competing explanations of the
  same rows.
* **`not_assessable`.** A hypothesis the data cannot score is carried with the
  reason it could not be scored. Dropping it would let a competing explanation
  be rejected without being asked.
* **`confounders[]`.** For an equipment candidate the standing rival is that
  its exposure mix moved rather than its behaviour. That is checked by
  re-running the same statistic on observations residualized against their own
  product-and-week mean, and both numbers are reported.
"""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from typing import Mapping, Sequence

from fabops.diagnosis.candidates import (BIN_DAYS, CandidateSupport, Panel,
                                         build_panels)
from fabops.diagnosis.channels import Observation
from fabops.diagnosis.decide import (ALPHA, ChannelStanding, Decision,
                                     evidence_of)
from fabops.diagnosis.model import (ASSESSED, NOT_ASSESSABLE, Anchor,
                                    Candidate, Considered, Entity,
                                    Investigation, Onset, Window)

__all__ = ["assemble", "control_for_exposure_mix"]


def control_for_exposure_mix(observations: Sequence[Observation],
                             bin_days: float = BIN_DAYS
                             ) -> tuple[Observation, ...]:
    """Residualize each observation against its own (product, bin) mean.

    What survives is variation that is not explained by which products ran
    that week. This is the control for the standing rival hypothesis, and it
    is computed from the observable run log alone.
    """
    cells: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for observation in observations:
        product = observation.keys.get("product")
        if product is None:
            continue
        cells[(observation.channel, product,
               int(observation.day // bin_days))].append(observation.value)
    centre = {key: sum(values) / len(values) for key, values in cells.items()}
    out: list[Observation] = []
    for observation in observations:
        product = observation.keys.get("product")
        if product is None:
            out.append(observation)
            continue
        key = (observation.channel, product,
               int(observation.day // bin_days))
        out.append(Observation(observation.channel, observation.day,
                               observation.value - centre[key],
                               observation.keys))
    return tuple(out)


def _onsets(standings: Sequence[ChannelStanding],
            bin_days: float) -> tuple[Onset, ...]:
    """One onset per distinct anchor the candidate's leading channels used.

    Reported as an interval of one bin either side, because the anchor is a
    bin boundary and pretending to a sharper estimate would be pretending.
    A candidate whose evidence spans two anchors keeps both: an arc that
    begins and is then intervened on is not one change point.
    """
    best: dict[float, tuple[float, str]] = {}
    for standing in standings:
        if standing.value <= 0:
            continue
        day = standing.anchor.day
        if day not in best or standing.p < best[day][0]:
            best[day] = (standing.p, standing.anchor.channel)
    return tuple(
        Onset(day=day, interval=(day - bin_days, day + bin_days),
              anchor_channel=channel)
        for day, (_p, channel) in sorted(best.items()))


def _narrative(entity: Entity, standings: Sequence[ChannelStanding],
               families: Mapping[str, float], p_candidate: float) -> str:
    """Generated from the evidence, never free-form."""
    if not standings:
        return f"{entity.name} carries no scorable evidence."
    leading = [s for s in standings if s.rank == 1]
    parts = ", ".join(
        f"{family} p={value:.3f}" for family, value in sorted(families.items()))
    return (f"{entity.kind} {entity.name} leads {len(leading)} of "
            f"{len(standings)} scored channels across {len(families)} "
            f"evidence families ({parts}); permutation p={p_candidate:.4f} "
            f"against relabelled peers of the same kind and role.")


def _confounders(entity: Entity, standings: Sequence[ChannelStanding],
                 controlled: Mapping[str, Mapping[str, float]]
                 ) -> tuple[Mapping[str, object], ...]:
    if not standings or entity.kind not in ("chamber", "tool"):
        return ()
    leading = standings[0]
    after = controlled.get(entity.name, {}).get(leading.channel)
    if after is None:
        return ()
    return ({
        "hypothesis": "the exposure mix moved rather than the entity",
        "control": "observations residualized against their own product and "
                   "week before the same statistic was recomputed",
        "channel": leading.channel,
        "value": round(leading.value, 6),
        "value_controlled": round(after, 6),
        "retained": round(after / leading.value, 4) if leading.value else None,
    },)


def assemble(*, dataset_id: str, horizon_days: float, anchors: Sequence[Anchor],
             decision: Decision, supports: Sequence[CandidateSupport],
             controlled: Mapping[str, Mapping[str, float]],
             statistic: str, engine: str, alpha: float = ALPHA,
             bin_days: float = BIN_DAYS) -> Investigation:
    """Turn the decision plane's output into the artifact."""
    family_wise = decision.p_familywise
    insufficient = not decision.strata or family_wise > alpha

    ranked: list[tuple[float, float, str, Candidate]] = []
    for stratum in decision.strata:
        for name in stratum.members:
            key = (stratum.kind, name)
            standings = stratum.standings.get(name, ())
            entity = Entity(kind=stratum.kind, id=name, name=name)
            p_value = decision.p_candidate[key]
            ranked.append((
                p_value, -decision.score[key], f"{stratum.kind}:{name}",
                Candidate(
                    entity=entity, status=ASSESSED,
                    score=round(decision.score[key], 6), p_value=p_value,
                    onsets=_onsets(standings, bin_days),
                    evidence=evidence_of(standings, statistic),
                    confounders=_confounders(entity, standings, controlled),
                    narrative=_narrative(entity, standings,
                                         stratum.family_p.get(name, {}),
                                         p_value))))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    candidates = [row[3] for row in ranked]

    scored = {(c.entity.kind, c.entity.name) for c in candidates}
    unassessable = [
        Candidate(entity=Entity(kind=s.kind, id=s.name, name=s.name),
                  status=NOT_ASSESSABLE, score=0.0, p_value=None,
                  reason=s.reason or "not scorable from the observable plane")
        for s in supports
        if not s.assessable and (s.kind, s.name) not in scored]
    unassessable.sort(key=lambda c: (c.entity.kind, c.entity.name))
    candidates.extend(unassessable)

    leader = next((c for c in candidates if c.status == ASSESSED), None)
    considered: list[Considered] = []
    if leader is not None:
        best_by_kind: dict[str, Candidate] = {}
        for candidate in candidates:
            if candidate.status != ASSESSED:
                continue
            best_by_kind.setdefault(candidate.entity.kind, candidate)
        for kind, candidate in sorted(best_by_kind.items()):
            if candidate is leader:
                continue
            considered.append(Considered(
                entity=candidate.entity,
                verdict="ranked below the leading candidate",
                detail=(f"best {kind} hypothesis at permutation "
                        f"p={candidate.p_value:.4f} against the leader's "
                        f"p={leader.p_value:.4f}")))
        runners = [c for c in candidates
                   if c.status == ASSESSED
                   and c.entity.kind == leader.entity.kind and c is not leader][:3]
        for candidate in runners:
            considered.append(Considered(
                entity=candidate.entity,
                verdict="same kind, weaker evidence",
                detail=(f"permutation p={candidate.p_value:.4f}, "
                        f"score {candidate.score:.2f} against the leader's "
                        f"{leader.score:.2f}")))
    for candidate in unassessable:
        considered.append(Considered(
            entity=candidate.entity, verdict=NOT_ASSESSABLE,
            detail=candidate.reason or ""))

    return Investigation(
        dataset_id=dataset_id, generated_by=engine,
        window=Window(start_day=0.0, end_day=horizon_days, bin_days=bin_days),
        anchors=tuple(anchors), candidates=tuple(candidates),
        considered=tuple(considered), insufficient_evidence=insufficient,
        abstention={
            "p_familywise": round(family_wise, 6),
            "alpha": alpha,
            "strata": [s.label for s in decision.strata],
            "permutations": decision.permutations,
            "reason": ("no candidate is more extreme than relabelling the "
                       "candidates produces"
                       if insufficient else
                       "at least one candidate is more extreme than "
                       "relabelling the candidates produces"),
        },
        settings={"statistic": statistic, "bin_days": bin_days,
                  "alpha": alpha, "anchors": len(anchors)})
