"""
sweep.py — A6's severity sweep, and the natural-variation floor it is read against.

`PHASE_1_ACCEPTANCE.md` A6 asks two things of the reference queries: that they
*recover* each scenario's intended evidence at moderate severity, and that at
subtle severity they "sit near the natural-variation floor (difficulty axis
exists)". Neither is answerable from one dataset. The first needs a comparison
against what a *healthy* world's queries already report, and the second needs
the same scenario at more than one severity.

So this module measures both:

* `severity_sweep` runs the reference queries over one scenario at
  `subtle` / `moderate` / `obvious` and reports the planted chamber's standing
  on each channel alongside its **realized** shift — never its configured
  label, because severity is a target and the realization is what a benchmark
  can score (ADR-016 §4).
* `natural_variation_floor` runs the identical queries over null worlds and
  reports the worst standing *any* chamber reaches with nothing wrong. That is
  the floor. Without it "the planted chamber scored 2.6σ" is a number with no
  scale: on the null world some chamber always ranks first, and the question
  is whether it ranks first by *more*.

The floor is measured rather than assumed for the same reason the severity
reference is: a threshold chosen by the author is a threshold that measures
the author. Nothing here builds datasets — the caller supplies them, so the
benchmark decides what it can afford to build and this module only reads.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from fabeval.queries import (
    alarm_counts,
    chamber_edge_cd_deviation,
    chamber_edge_defect_share,
    chamber_yield_split,
    rank,
    zscore,
)

__all__ = [
    "CHANNELS",
    "MINIMUM_FLOOR_SEEDS",
    "FloorReading",
    "SweepReading",
    "channel_scores",
    "natural_variation_floor",
    "severity_sweep",
]

#: The reference channels A6 names for the equipment-fault scenarios:
#: "chamber-grain yield split + edge-zone defect elevation + edge-CD shift".
#: Alarms are carried alongside because the criterion's temporal-alignment
#: clause is read off them.
CHANNELS = ("edge_cd", "edge_defect_share", "alarms", "yield_split")


def channel_scores(dataset: Any, channel: str) -> dict[str, float]:
    """One reference query, as a per-chamber score where higher is worse.

    `yield_split` is negated so every channel points the same way and their
    σ readings are comparable; a yield *deficit* is a negative delta.
    """
    if channel == "edge_cd":
        scores = chamber_edge_cd_deviation(dataset.db_path)
    elif channel == "edge_defect_share":
        scores = chamber_edge_defect_share(dataset.db_path)
    elif channel == "alarms":
        scores = alarm_counts(dataset.db_path)
    elif channel == "yield_split":
        return {label: -score.value
                for label, score in chamber_yield_split(dataset.db_path).items()}
    else:
        raise KeyError(f"unknown reference channel {channel!r}")
    return {label: score.value for label, score in scores.items()}


@dataclass(frozen=True)
class SweepReading:
    """One severity of one scenario, as the reference queries see it."""

    severity: str
    realized_sigma: float
    #: channel -> (leave-one-out σ of the planted chamber, its rank, of how many)
    standing: Mapping[str, tuple[float, int, int]]

    def sigma(self, channel: str) -> float:
        return self.standing[channel][0]


@dataclass(frozen=True)
class FloorReading:
    """What the same queries report on worlds with nothing wrong in them.

    `worst` is the largest |σ| *any* chamber reaches — the honest floor, since
    a diagnosis engine looking at a null does not know which chamber to
    excuse.
    """

    channel: str
    per_seed: tuple[float, ...]
    worst: float
    mean: float


def severity_sweep(datasets: Mapping[str, Any], label: str,
                   channels: Sequence[str] = CHANNELS) -> list[SweepReading]:
    """Read one scenario's planted chamber across severities.

    `datasets` maps a severity name to a built dataset of the *same* scenario
    at that severity. Ordered subtle → moderate → obvious on output, whatever
    order the mapping arrived in.
    """
    order = {"subtle": 0, "moderate": 1, "obvious": 2}
    readings: list[SweepReading] = []
    for severity in sorted(datasets, key=lambda s: order.get(s, 99)):
        dataset = datasets[severity]
        (event,) = dataset.truth["events"]
        standing: dict[str, tuple[float, int, int]] = {}
        for channel in channels:
            scores = channel_scores(dataset, channel)
            if label not in scores:
                continue
            standing[channel] = (zscore(scores, label),
                                 rank(scores, label), len(scores))
        readings.append(SweepReading(
            severity=severity,
            realized_sigma=event["severity_realized"]["aggregate_shift_sigma"],
            standing=standing))
    return readings


#: Fewest null realizations a floor may be read from. One null world is one
#: draw: on the baseline the worst chamber's edge-CD standing is 2.31 sigma at
#: seed 42 and 2.84 at seed 2024, so a floor taken from a single seed can let
#: a moderate fault look separated when it is not.
MINIMUM_FLOOR_SEEDS = 3


def natural_variation_floor(nulls: Sequence[Any],
                            channels: Sequence[str] = CHANNELS
                            ) -> dict[str, FloorReading]:
    """The worst standing any chamber reaches on a world with no fault.

    Raises if given too few realizations rather than returning a floor that
    would flatter whatever it is compared against.
    """
    if len(nulls) < MINIMUM_FLOOR_SEEDS:
        raise ValueError(
            f"a natural-variation floor needs at least "
            f"{MINIMUM_FLOOR_SEEDS} null realizations, got {len(nulls)}")
    out: dict[str, FloorReading] = {}
    for channel in channels:
        per_seed: list[float] = []
        for dataset in nulls:
            scores = channel_scores(dataset, channel)
            if len(scores) < 3:
                continue
            per_seed.append(max(abs(zscore(scores, label))
                                for label in scores))
        if per_seed:
            out[channel] = FloorReading(channel=channel,
                                        per_seed=tuple(per_seed),
                                        worst=max(per_seed),
                                        mean=st.mean(per_seed))
    return out


def summarize(readings: Sequence[SweepReading],
              floor: Mapping[str, FloorReading]) -> dict[str, Any]:
    """The two questions A6 asks, answered from the readings.

    * **difficulty axis** — does the realized shift rise with the configured
      severity, and does at least one channel rise with it? A6's own words
      are "difficulty axis exists"; it does not require every observable to
      be monotone, and requiring that would fail on channels the design
      already documents as non-monotone (alarm counts saturate against the
      refractory window; a signed latent's defect channel can go either way,
      ADR-018).
    * **separation at moderate** — does the planted chamber, at moderate,
      exceed the floor on any channel? This is the half A6 words as
      "recovers each scenario's intended evidence at moderate severity", and
      it is the strict reading: ranking first is not separation, because on a
      null world some chamber always ranks first.
    """
    by_severity = {r.severity: r for r in readings}
    realized = [r.realized_sigma for r in readings]
    rising = all(a < b for a, b in zip(realized, realized[1:]))

    monotone: list[str] = []
    for channel in CHANNELS:
        values = [r.standing[channel][0] for r in readings
                  if channel in r.standing]
        if len(values) == len(readings) and all(a < b for a, b
                                                in zip(values, values[1:])):
            monotone.append(channel)

    separated: list[str] = []
    moderate = by_severity.get("moderate")
    if moderate is not None:
        for channel, (sigma, _rank, _n) in moderate.standing.items():
            if channel in floor and sigma > floor[channel].worst:
                separated.append(channel)

    subtle = by_severity.get("subtle")
    at_floor = True
    if subtle is not None:
        at_floor = all(sigma <= floor[channel].worst
                       for channel, (sigma, _r, _n) in subtle.standing.items()
                       if channel in floor)

    return {
        "realized_rises_with_severity": rising,
        "realized": realized,
        "monotone_channels": monotone,
        "separated_at_moderate": separated,
        "subtle_at_or_below_floor": at_floor,
        "floor": {c: round(f.worst, 2) for c, f in floor.items()},
    }
