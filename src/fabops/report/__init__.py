"""
fabops.report — the decision-support artifact: conclusion, impact, actions.

    build_report(db_path) -> Report          # the engine chooses the subject
    build_report(db_path, subject="TOOL/X")  # an engineer chooses it

This is the last stage of `PROJECT_VISION`'s sentence — *what is happening, why,
what is affected, and what should the engineer do next*. The first two are
`fabops.diagnosis`; the last two are `fabops.impact` and `fabops.actions`; this
module is what joins them into one document and writes it down.

**Why this is `fabops.report/v1` and not a bigger `fabops.investigation/v1`.**
Two governing documents describe an exported artifact under one name. The
Phase 0 boundary sketch (`FABOPS_VS_FABKG_BOUNDARY.md` §4) draws it with
`excursion`, `hypotheses`, `conclusion`, `impact`, `actions` and `provenance`;
the later and far more specific `DIAGNOSIS_CONTRACT.md` §3 declares
`fabops.investigation/v1` to be exactly what `diagnose(db_path)` returns, and
ADR-029 §7 closed that decision. Where a Phase 0 planning document and a later
contract disagree, the contract governs — the same ordering ADR-029 §6 used to
settle the roadmap's `investigate <excursion>` sketch.

So the engine's artifact is left byte-identical and this one **wraps** it. The
alternative — adding `impact` and `actions` to the investigation — would make
one schema name mean two things, because `diagnose` cannot compute either: it
is handed a database path and impact needs a *subject*, which is a conclusion
rather than an input. A report produced by the engine and one produced by this
module would then differ while claiming the same version. ADR-034 records the
decision.

**The subject is never invented.** When the investigation abstains — which at
the declared level is what it does on every dataset this project can build —
there is no subject, `impact` and `containment` are `null`, and the actions
say why. A subject may still be supplied by a human, and the artifact records
that it was supplied rather than concluded, because those are different claims
and a reader six months later cannot tell them apart from a name alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabops.actions import Action, load_knowledge, recommend
from fabops.diagnosis import ENGINE, diagnose
from fabops.impact import (IMPACT_VERSION, ImpactEstimate, estimate_loss,
                           lot_exposure)
from fabops.semantic import LAYER_VERSION, open_layer

__all__ = [
    "REPORT",
    "REPORT_SCHEMA",
    "REPORT_VERSION",
    "Report",
    "build_report",
]

REPORT_VERSION = "1.0.0"
REPORT = f"fabops.report/{REPORT_VERSION}"
REPORT_SCHEMA = "fabops.report/v1"

#: How many lots the containment list carries. The full ranking stays available
#: through `fabops.impact.lot_exposure`; a report a human acts on is a report
#: that names the material worth acting on first.
CONTAINMENT_LOTS = 10

#: The entity kinds impact can be computed for — the ones that are observable
#: exposure in the run log. A product or an operator can be a *candidate* and
#: cannot be a containment subject, and the report says so rather than
#: silently producing nothing.
IMPACT_KINDS = ("chamber", "tool")


@dataclass
class Report:
    dataset_id: str
    investigation: Mapping[str, Any]
    subject: Mapping[str, Any] | None
    impact: Mapping[str, Any] | None
    containment: Mapping[str, Any] | None
    actions: Sequence[Action]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REPORT_SCHEMA,
            "generated_by": REPORT,
            "dataset_id": self.dataset_id,
            "subject": self.subject,
            "investigation": self.investigation,
            "impact": self.impact,
            "containment": self.containment,
            "actions": [action.to_dict() for action in self.actions],
            "provenance": dict(self.provenance),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)


def _subject_kind(label: str) -> str:
    """A chamber is `TOOL/CHAMBER`; anything else is a tool."""
    return "chamber" if "/" in label else "tool"


def _leading_families(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    """The families in which the subject led its peers.

    Rank 1 is the bar because a family that merely *has* evidence is not a
    family that pointed anywhere: every candidate has evidence in every family
    it could be scored in.
    """
    families: list[str] = []
    for entry in candidate.get("evidence", ()):
        if entry.get("rank") == 1 and entry.get("family") not in families:
            families.append(str(entry["family"]))
    return tuple(families)


def _busiest_step(connection, kind: str, entity: str) -> str | None:
    """The step at which the subject saw the most material.

    Impact is a per-step question — a chamber that runs two different steps has
    two different cohorts — and this picks the one with the most exposure
    rather than making the caller know the route.

    **By exposure, never by outcome.** Picking the step where the deficit came
    out largest would be a per-subject maximization, which is the selection
    ADR-029 §2 measured and rejected for the engine's anchors: a benign subject
    wins a maximization more often than a faulted one does. Exposure is decided
    by routing before any yield exists, so it cannot be chosen to flatter a
    result. A caller who knows which step they care about passes it.
    """
    column = "chamber_label" if kind == "chamber" else "tool_name"
    row = connection.execute(
        f"SELECT step_name, COUNT(DISTINCT wafer_id) AS wafers "
        f"FROM fact_wafer_step WHERE {column} = ? "
        f"GROUP BY step_name ORDER BY wafers DESC, step_name LIMIT 1",
        (entity,)).fetchone()
    return None if row is None else str(row[0])


def build_report(db_path: Path | str, *, subject: str | None = None,
                 step_name: str | None = None,
                 knowledge_path: Path | str | None = None) -> Report:
    """Investigate one dataset and write the decision-support artifact."""
    investigation = diagnose(db_path)
    artifact = investigation.to_dict()
    table = load_knowledge(knowledge_path)

    subject_record: dict[str, Any] | None = None
    leading: tuple[str, ...] = ()
    if subject is not None:
        subject_record = {"kind": _subject_kind(subject), "id": subject,
                          "source": "operator",
                          "note": "supplied by the caller; this report "
                                  "quantifies the consequence of acting on it "
                                  "and does not claim the evidence names it"}
        for candidate in artifact.get("candidates", ()):
            if candidate.get("entity", {}).get("id") == subject:
                leading = _leading_families(candidate)
                break
    elif not investigation.insufficient_evidence:
        for candidate in artifact.get("candidates", ()):
            entity = candidate.get("entity", {})
            if (candidate.get("status") == "assessed"
                    and entity.get("kind") in IMPACT_KINDS):
                subject_record = {"kind": str(entity["kind"]),
                                  "id": str(entity["id"]),
                                  "source": "engine",
                                  "p_value": candidate.get("p_value")}
                leading = _leading_families(candidate)
                break

    impact: dict[str, Any] | None = None
    containment: dict[str, Any] | None = None
    estimate: ImpactEstimate | None = None

    if subject_record is not None:
        connection = open_layer(db_path)
        try:
            kind, entity = subject_record["kind"], subject_record["id"]
            step = step_name or _busiest_step(connection, kind, entity)
            subject_record["step_name"] = step
            estimate = estimate_loss(connection, kind, entity, step)
            impact = estimate.to_dict()
            exposures = lot_exposure(connection, kind, entity, step)
            containment = {
                "step_name": step,
                "lots_ranked_by_exposure": [row.to_dict() for row
                                            in exposures[:CONTAINMENT_LOTS]],
                "lots_total": len(exposures),
                "note": ("lots are ranked by the share of their own wafers "
                         "that met the subject, which is material at risk "
                         "rather than material already measured"),
            }
        finally:
            connection.close()

    actions = recommend(
        leading, subject=None if subject_record is None
        else str(subject_record["id"]),
        distinguishable=None if estimate is None else estimate.distinguishable,
        knowledge=table)

    return Report(
        dataset_id=str(artifact.get("dataset_id", "")),
        investigation=artifact, subject=subject_record, impact=impact,
        containment=containment, actions=actions,
        provenance={
            "engine": ENGINE,
            "semantic_layer": f"fabops.semantic/{LAYER_VERSION}",
            "impact": f"fabops.impact/{IMPACT_VERSION}",
            "knowledge": table.to_provenance(),
            "evidence_families_leading": list(leading),
        })
