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
from datetime import datetime
from typing import Any, Mapping, Sequence

__all__ = ["DiagnosisOutcome", "Score", "score_dataset", "score_population"]


@dataclass(frozen=True)
class DiagnosisOutcome:
    """One report, joined to one answer key.

    `planted` is a **tuple**, because a dataset may plant more than one entity.
    That was an assumption the adapter carried silently until the Phase 6
    library contained a two-fault scenario, and the failure mode of leaving it
    was quiet rather than loud: `events[0]` would have scored the first planted
    entity and treated a correct identification of the second as a miss.
    `rank` is the best rank any planted entity reached and `ranks` keeps them
    all, since with two planted entities only one of them can be first and
    reporting only the best would hide the other.
    """

    dataset_id: str
    scenario: str
    faulted: bool
    planted: tuple[str, ...]
    abstained: bool
    p_familywise: float
    top: str | None
    rank: int | None
    of: int
    onset_error_days: float | None
    not_assessable: int
    ranks: tuple[int | None, ...] = ()

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

    @property
    def all_attributed(self) -> bool:
        """Every planted entity in the top `len(planted)` places.

        The generous reading of a multi-fault result — two planted entities
        cannot both be rank 1, so "did the report put both of them at the
        top?" is the question that has an answer.
        """
        if not self.faulted:
            return False
        positions = [r for r in self.ranks if r is not None]
        return (len(positions) == len(self.planted)
                and max(positions) <= len(self.planted))


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
    def top3_rate(self) -> float | None:
        """How often a planted entity reached the top three.

        Reported beside rank-1 because a ranking that puts the answer second is
        a different kind of result from one that does not find it at all, and
        an engine this weak needs the distinction to be visible.
        """
        faulted = [o for o in self.faulted if o.rank]
        if not faulted:
            return None
        return sum(1 for o in faulted if o.rank <= 3) / len(faulted)

    @property
    def multi_attribution_rate(self) -> float | None:
        """On multi-fault datasets, how often *every* planted entity is at the
        top. `None` where the population has no multi-fault member."""
        multi = [o for o in self.faulted if len(o.planted) > 1]
        if not multi:
            return None
        return sum(1 for o in multi if o.all_attributed) / len(multi)

    @property
    def median_onset_error(self) -> float | None:
        errors = [o.onset_error_days for o in self.faulted
                  if o.onset_error_days is not None]
        return st.median(errors) if errors else None

    @property
    def scenarios(self) -> tuple[str, ...]:
        return tuple(sorted({o.scenario for o in self.outcomes if o.scenario}))

    def render(self) -> str:
        from fabeval.population import claimable

        lines = [f"diagnosis on {self.population}",
                 f"  datasets {len(self.outcomes)} "
                 f"({len(self.faulted)} faulted, {len(self.nulls)} fault-free) "
                 f"over {len(self.scenarios)} scenario(s)"]

        def show(label: str, value: float | None, fmt: str = ".3f") -> str:
            return f"  {label:26s} {'n/a' if value is None else format(value, fmt)}"

        lines += [show("false-alarm rate", self.false_alarm_rate),
                  show("detection rate", self.detection_rate),
                  show("attribution rate (rank 1)", self.attribution_rate),
                  show("attribution rate (top 3)", self.top3_rate),
                  show("all planted at the top", self.multi_attribution_rate),
                  show("mean reciprocal rank", self.mean_reciprocal_rank),
                  show("median onset error (d)", self.median_onset_error, ".1f")]

        # The population guard, printed with the number rather than beside it,
        # because a correct number quoted without its population is the exact
        # failure `fabeval.population` exists to prevent.
        allowed, reason = claimable(self.scenarios)
        lines.append(f"  {'capability claim':26s} "
                     f"{'permitted' if allowed else 'NOT PERMITTED'}")
        if reason:
            lines.append(f"  note: {reason}")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def _entity_key(entity: Mapping[str, Any]) -> str:
    return f"{entity['kind']}:{entity['name']}"


def _onset_day(event: Mapping[str, Any],
               time_origin: str | None) -> float | None:
    """Truth's onset in days from the world clock, or nothing.

    Truth records `onset` as an instant. Turning it into the currency a report
    speaks needs the origin, which lives in the observable plane; without it
    this returns None and the metric reports itself as unmeasured, because a
    silent zero would look like a perfect estimate.
    """
    if "onset_day" in event and event["onset_day"] is not None:
        return float(event["onset_day"])
    onset = event.get("onset")
    if onset is None or time_origin is None:
        return None
    delta = datetime.fromisoformat(onset) - datetime.fromisoformat(time_origin)
    return delta.total_seconds() / 86400.0


def score_dataset(report: Mapping[str, Any], truth: Mapping[str, Any],
                  scenario: str = "",
                  time_origin: str | None = None) -> DiagnosisOutcome:
    """Join one report to one answer key, on `dataset_id`.

    `time_origin` is the observable `dataset_meta.time_origin`, and it is what
    makes onset error measurable at all: truth records an onset as a wall-clock
    instant while a report speaks in days from the start of the window, and
    neither artifact alone carries the conversion. `fabeval` may read the
    observable plane, so the caller supplies it; omitted, onset error is
    reported as unmeasured rather than as zero.
    """
    if report["dataset_id"] != truth["dataset_id"]:
        raise ValueError(
            f"a report for {report['dataset_id']!r} cannot be scored against "
            f"the answer key for {truth['dataset_id']!r}")

    events = truth.get("events") or []
    faulted = bool(events)

    planted: list[str] = []
    onset_days: list[float | None] = []
    for event in events:
        target = event["target"]
        chamber = target.get("chamber")
        planted.append(f"chamber:{target['tool']}/{chamber}" if chamber
                       else f"tool:{target['tool']}")
        onset_days.append(_onset_day(event, time_origin))

    assessed = [c for c in report["candidates"] if c["status"] == "assessed"]
    ranked = [_entity_key(c["entity"]) for c in assessed]

    ranks = tuple(ranked.index(name) + 1 if name in ranked else None
                  for name in planted)
    found = [r for r in ranks if r is not None]
    best = min(found) if found else None

    # Onset error is reported for the planted entity the report placed best,
    # because that is the one whose onset estimate a reader would act on.
    onset_error = None
    if best is not None:
        index = ranks.index(best)
        onset_day = onset_days[index]
        if onset_day is not None:
            onsets = assessed[best - 1].get("onsets") or []
            if onsets:
                onset_error = min(abs(o["day"] - float(onset_day))
                                  for o in onsets)

    return DiagnosisOutcome(
        dataset_id=report["dataset_id"], scenario=scenario, faulted=faulted,
        planted=tuple(planted),
        abstained=bool(report["insufficient_evidence"]),
        p_familywise=float(report["abstention"]["p_familywise"]),
        top=ranked[0] if ranked else None, rank=best, of=len(ranked),
        onset_error_days=onset_error,
        not_assessable=sum(1 for c in report["candidates"]
                           if c["status"] == "not_assessable"),
        ranks=ranks)


def score_population(pairs: Sequence[Sequence[Any]], population: str,
                     notes: Sequence[str] = ()) -> Score:
    """Score many (report, answer key, scenario[, time_origin]) rows.

    The fourth element is optional and is the observable `time_origin`; supply
    it and onset error is measured, omit it and the metric says so.
    """
    return Score(population=population,
                 outcomes=tuple(score_dataset(*row) for row in pairs),
                 notes=tuple(notes))
