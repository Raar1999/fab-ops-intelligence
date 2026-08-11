"""
service.py — opening a dataset, exporting an artifact, and proving the chain.

Four actions the product performs and `fabops` does not, because each is a
decision about the *application* rather than about the fab:

* **`open_dataset`** refuses to analyse a dataset the registry called unusable,
  and says which of the four statuses it got. `fabops` cannot do this: it is
  handed a path and its job is to answer about whatever is there.
* **`decision_for` with a subject**, and `subject_candidates` deciding which
  subjects can be offered. The engine abstains on everything this project can
  build, so it names no subject, so impact and containment are empty on every
  screen; an engineer asking *assume it is this one, what would acting cost?*
  is the supported way to fill them, and the artifact records that the subject
  was supplied rather than concluded.
* **`artifact_text`** renders `fabops.report/v1` for download from a decision
  that has already been built, so a screen exports what it is showing. That
  file is the export the FabKG boundary defines
  (`FABOPS_VS_FABKG_BOUNDARY.md` §4), produced by the same code as
  `fabops-report` and owning no directory.
* **`workflow_check`** runs the whole product chain without a browser —
  discover, open, diagnose, explain — and returns what each stage produced. It
  is what `fabops-app --check` runs and what the end-to-end test asserts on,
  so "the workflow works" is a command anybody can type rather than a claim.

**The analysis path takes a database path and nothing else.** Everything the
product knows about a dataset that is *not* in the database — which scenario a
user picked, which slug the identity resolves to — stops at this module's
door. `open_dataset` accepts a `DatasetRecord` for its status and passes
`record.db_path`, one string, onward. That is the whole of the anti-leakage
contribution this file makes, and it is the reason the file is as short as it
is: everything else here would have been a place to put a second opinion.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fabops.report import IMPACT_KINDS, build_report
from fabops.report.workspace import WORKSPACE_PAGES, load_workspace

from fabapp import APP
from fabapp.explain import Outcome, explain_investigation
from fabapp.registry import READY, DatasetRecord, discover, inspect

__all__ = [
    "IMPACT_KINDS",
    "PAGE_PAYLOADS",
    "WORKSPACE_PAGES",
    "DatasetNotUsable",
    "WorkflowCheck",
    "artifact_text",
    "decision_for",
    "investigation_artifact",
    "open_dataset",
    "outcome_of",
    "subject_candidates",
    "workflow_check",
]


class DatasetNotUsable(RuntimeError):
    """The product declined to analyse a dataset, with the registry's reason."""

    def __init__(self, record: DatasetRecord) -> None:
        self.record = record
        super().__init__(
            f"{record.db_path} cannot be opened ({record.status}): "
            f"{record.detail}")


def open_dataset(record: DatasetRecord | str | Path) -> dict[str, Any]:
    """Everything the workspace pages need, for one dataset, in one pass.

    Accepts a record or a path; a path is inspected first, because the check
    is the point. `load_workspace` runs the monitors, the engine and the
    decision artifact exactly once between them.
    """
    if not isinstance(record, DatasetRecord):
        record = inspect(record)
    if record.status != READY:
        raise DatasetNotUsable(record)
    payload = load_workspace(record.db_path)
    payload["product"] = APP
    return payload


def outcome_of(payload: Mapping[str, Any]) -> Outcome:
    """The report-level verdict for a loaded workspace payload."""
    return explain_investigation(payload["investigation"]["investigation"])


def decision_for(db_path: Path | str, *,
                 subject: str | None = None) -> dict[str, Any]:
    """The decision artifact as plain data.

    `subject` is the one thing a user may supply that the engine did not
    conclude, and the artifact records that it was supplied — the distinction
    `fabops.report` draws and this function does not blur.

    It matters more than it looks. At its declared level the engine abstains on
    every dataset this project can build, so it names no subject, so impact,
    containment and the recommended checks are `null` on every screen. Without
    a way for an engineer to say *"assume it is this one — what would acting on
    it cost?"*, a third of the decision-support layer would be unreachable from
    the product and permanently blank. Supplying a subject is not the engine
    concluding one, and the artifact is explicit about which it was.
    """
    return build_report(db_path, subject=subject).to_dict()


def subject_candidates(artifact: Mapping[str, Any]) -> tuple[str, ...]:
    """Which candidates a user may ask an impact question about.

    Impact is exposure in the run log, so it exists for equipment and not for a
    product, a recipe or an operator — those can be *candidates* and cannot be
    containment subjects, and `fabops.report` says so rather than silently
    producing nothing. Filtering here keeps the screen from offering a choice
    that would come back empty.
    """
    return tuple(
        str(candidate["entity"]["id"])
        for candidate in artifact.get("candidates", ())
        if candidate.get("status") == "assessed"
        and candidate.get("entity", {}).get("kind") in IMPACT_KINDS)


def artifact_text(decision: Mapping[str, Any]) -> tuple[str, str]:
    """`(filename, json text)` for a decision that has already been built.

    Separate from `decision_for` so that a screen exports *what it is showing*
    rather than recomputing a second report which might differ — the engine is
    deterministic, so it would not, but a screen and its download reading the
    same object is one fewer thing to have to argue about.
    """
    name = f"{decision.get('dataset_id') or 'dataset'}.fabops-report.json"
    return name, json.dumps(decision, indent=2, sort_keys=False)


def investigation_artifact(db_path: Path | str, *,
                           subject: str | None = None) -> tuple[str, str]:
    """Build the decision artifact and render it for export, in one step."""
    return artifact_text(decision_for(db_path, subject=subject))


@dataclass(frozen=True)
class WorkflowCheck:
    """What one headless pass through the product produced."""

    datasets_found: int
    datasets_ready: int
    dataset_id: str
    scenario: str | None
    pages: tuple[str, ...]
    outcome: Outcome
    artifact_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "datasets_found": self.datasets_found,
            "datasets_ready": self.datasets_ready,
            "dataset_id": self.dataset_id,
            "scenario": self.scenario,
            "pages": list(self.pages),
            "outcome": self.outcome.to_dict(),
            "artifact_bytes": self.artifact_bytes,
        }

    def describe(self) -> str:
        lines = [
            f"datasets discovered : {self.datasets_found} "
            f"({self.datasets_ready} ready)",
            f"dataset opened      : {self.dataset_id}"
            f"{'' if self.scenario is None else f'  [{self.scenario}]'}",
            f"pages with data     : {len(self.pages)} — "
            f"{', '.join(self.pages)}",
            f"diagnosis           : {self.outcome.headline}",
            f"                      family-wise p = "
            f"{self.outcome.p_familywise} against alpha = {self.outcome.alpha}"
            f", {self.outcome.assessed} assessed / "
            f"{self.outcome.not_assessable} not assessable",
            f"export              : {self.artifact_bytes} bytes of "
            f"fabops.report/v1",
        ]
        return "\n".join(lines)


#: Which payload key carries each page's data. Declared rather than derived so
#: that a page added to `WORKSPACE_PAGES` without a payload fails this check
#: instead of rendering an empty screen.
PAGE_PAYLOADS = {
    "Fab Today": "fab_today",
    "Process": "process",
    "Equipment": "equipment",
    "Yield": "yield",
    "Defect": "defect",
    "Investigation": "investigation",
    "Wafer explorer": "wafers",
}


def workflow_check(dataset: str | Path | None = None, *,
                   root: Path | None = None) -> WorkflowCheck:
    """Run the product chain end to end, headlessly.

    Discovers datasets, opens one, confirms every page has a payload, reads the
    engine's verdict through the same explainer the screens use, and builds the
    export. Raises rather than returning a partial result, because a check that
    reported "mostly worked" would be worse than no check.
    """
    found = discover(root)
    ready = [record for record in found if record.usable]
    if dataset is not None:
        record = inspect(dataset if Path(str(dataset)).suffix
                         else Path(str(dataset)) / "fab.db")
    elif ready:
        record = ready[0]
    else:
        raise DatasetNotUsable(DatasetRecord(
            db_path=Path(root) if root is not None else Path(),
            status="missing",
            detail="no usable dataset was found; create one first"))

    payload = open_dataset(record)
    pages = tuple(page for page in WORKSPACE_PAGES
                  if payload.get(PAGE_PAYLOADS[page]))
    missing = [page for page in WORKSPACE_PAGES if page not in pages]
    if missing:
        raise DatasetNotUsable(
            DatasetRecord(db_path=record.db_path, status="empty",
                          detail=f"no data for page(s): {', '.join(missing)}"))

    _name, text = investigation_artifact(record.db_path)
    return WorkflowCheck(
        datasets_found=len(found), datasets_ready=len(ready),
        dataset_id=record.dataset_id, scenario=record.scenario,
        pages=pages, outcome=outcome_of(payload),
        artifact_bytes=len(text.encode("utf-8")))
