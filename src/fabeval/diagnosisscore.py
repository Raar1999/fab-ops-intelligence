"""
diagnosisscore.py — the one adapter that joins a report to the answer key.

`DIAGNOSIS_CONTRACT.md` §5: how well a report does is the evaluator's question,
and this is where it is asked. The join is on `dataset_id` and on nothing else,
in this direction only:

    Investigation (from fab.db alone)  +  truth.json  ->  a score

The engine never sees any of this. `fabeval` imports `fabops.diagnosis` to
*call* it in a benchmark run, and `fabops.diagnosis` imports nothing from here
— a test pins the one-way arrow, because an evaluator that could reach into
the engine would be able to tune the thing it grades.

**What is scored, and what deliberately is not.** Attribution (did the report
rank the planted entity first, and where did it rank it), abstention (did a
fault-free world get `insufficient_evidence`), and onset error. **Not** the
mechanism: the observable plane carries no channel that identifies which
mechanism acted (ADR-019 §4, ADR-021 §6), so scoring an engine on naming one
would be scoring it on something it cannot see.

No number produced here is a capability claim until the library reaches the
population `EXPANSION_ROADMAP` Phase 6 asks for; `Score.population` carries the
name of what it was measured on so a reader cannot mistake one for the other.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = ["DiagnosisOutcome", "Score", "score_dataset", "score_population"]


@dataclass(frozen=True)
class DiagnosisOutcome:
    """One report, joined to one answer key."""

    dataset_id: str
    scenario: str
    faulted: bool
    planted: str | None
    abstained: bool
    p_familywise: float
    top: str | None
    rank: int | None
    of: int
    onset_error_days: float | None
    not_assessable: int

    @property
    def correct_abstention(self) -> bool:
        return self.abstained and not self.faulted

    @property
    def false_alarm(self) -> bool:
        return not self.abstained and not self.faulted

    @property
    def detected(self) -> bool:
        return self.faulted and not self.abstained

    @property
    def attributed(self) -> bool:
        return bool(self.faulted and self.rank == 1)


@dataclass(frozen=True)
class Score:
    """What a population of reports measured. Never a capability claim."""

    population: str
    outcomes: tuple[DiagnosisOutcome, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def nulls(self) -> tuple[DiagnosisOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.faulted)

    @property
    def faulted(self) -> tuple[DiagnosisOutcome, ...]:
        return tuple(o for o in self.outcomes if o.faulted)

    @property
    def false_alarm_rate(self) -> float | None:
        nulls = self.nulls
        if not nulls:
            return None
        return sum(1 for o in nulls if o.false_alarm) / len(nulls)

    @property
    def detection_rate(self) -> float | None:
        faulted = self.faulted
        if not faulted:
            return None
        return sum(1 for o in faulted if o.detected) / len(faulted)

    @property
    def attribution_rate(self) -> float | None:
        faulted = [o for o in self.faulted if o.rank]
        if not faulted:
            return None
        return sum(1 for o in faulted if o.rank == 1) / len(faulted)

    @property
    def mean_reciprocal_rank(self) -> float | None:
        ranks = [o.rank for o in self.faulted if o.rank]
        if not ranks:
            return None
        return st.mean(1.0 / r for r in ranks)

    @property
    def median_onset_error(self) -> float | None:
        errors = [o.onset_error_days for o in self.faulted
                  if o.onset_error_days is not None]
        return st.median(errors) if errors else None

    def render(self) -> str:
        lines = [f"diagnosis on {self.population}",
                 f"  datasets {len(self.outcomes)} "
                 f"({len(self.faulted)} faulted, {len(self.nulls)} fault-free)"]

        def show(label: str, value: float | None, fmt: str = ".3f") -> str:
            return f"  {label:26s} {'n/a' if value is None else format(value, fmt)}"

        lines += [show("false-alarm rate", self.false_alarm_rate),
                  show("detection rate", self.detection_rate),
                  show("attribution rate (rank 1)", self.attribution_rate),
                  show("mean reciprocal rank", self.mean_reciprocal_rank),
                  show("median onset error (d)", self.median_onset_error, ".1f")]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def _entity_key(entity: Mapping[str, Any]) -> str:
    return f"{entity['kind']}:{entity['name']}"


def score_dataset(report: Mapping[str, Any], truth: Mapping[str, Any],
                  scenario: str = "") -> DiagnosisOutcome:
    """Join one report to one answer key, on `dataset_id`."""
    if report["dataset_id"] != truth["dataset_id"]:
        raise ValueError(
            f"a report for {report['dataset_id']!r} cannot be scored against "
            f"the answer key for {truth['dataset_id']!r}")

    events = truth.get("events") or []
    faulted = bool(events)
    planted = None
    onset_day = None
    if faulted:
        target = events[0]["target"]
        chamber = target.get("chamber")
        planted = (f"chamber:{target['tool']}/{chamber}" if chamber
                   else f"tool:{target['tool']}")
        onset_day = events[0].get("onset_day")

    assessed = [c for c in report["candidates"] if c["status"] == "assessed"]
    ranked = [_entity_key(c["entity"]) for c in assessed]
    rank = ranked.index(planted) + 1 if planted in ranked else None

    onset_error = None
    if faulted and onset_day is not None and planted in ranked:
        onsets = assessed[ranked.index(planted)].get("onsets") or []
        if onsets:
            onset_error = min(abs(o["day"] - float(onset_day)) for o in onsets)

    return DiagnosisOutcome(
        dataset_id=report["dataset_id"], scenario=scenario, faulted=faulted,
        planted=planted, abstained=bool(report["insufficient_evidence"]),
        p_familywise=float(report["abstention"]["p_familywise"]),
        top=ranked[0] if ranked else None, rank=rank, of=len(ranked),
        onset_error_days=onset_error,
        not_assessable=sum(1 for c in report["candidates"]
                           if c["status"] == "not_assessable"))


def score_population(pairs: Sequence[tuple[Mapping[str, Any],
                                           Mapping[str, Any], str]],
                     population: str,
                     notes: Sequence[str] = ()) -> Score:
    """Score many (report, answer key, scenario) triples as one population."""
    return Score(population=population,
                 outcomes=tuple(score_dataset(report, truth, scenario)
                                for report, truth, scenario in pairs),
                 notes=tuple(notes))
