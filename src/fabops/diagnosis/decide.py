"""
decide.py — combination, the null, and the abstention.

Four decisions live here and each one was measured before it was made.

**Measure against contemporaneous peers of the same role, not against an
outside number.** A candidate's standing on a channel is how far it sits from
the other candidates it is exchangeable with, in units of their own spread and
computed leaving itself out. Nothing outside the database is consulted, so
there is no calibration file to ship, to version, or to prove independent of
the thing it judges.

The currency of the *decision* is a **rank**, and its ceiling is a known,
measured property rather than an oversight. Among seven peers a family's
evidence cannot be stronger than 1/8 however far out the candidate is, so a
shift confined to one family cannot on its own clear the family-wise level.
That is the architecture working rather than failing: a single channel was
measured three times over not to be decisive in this world, and convergence
across near-independent families is what the engine is built on. A
magnitude currency was tried and rejected in the same session - an unbounded
standardized value makes small strata dominate the global maximum, because a
leave-one-out spread over three peers is far more volatile than one over six.
The standardized value is kept on every evidence entry, where it is what an
analyst reads and where its scale does not have to be comparable across
strata.

**Combine across families, not across channels, and correct nothing
analytically.** Channels inside one family read the same physics and move
together; families do not - measured on fault-free worlds the strongest
cross-family dependence is 0.445 and every other pair is under 0.16. So a
family contributes its **best channel**, and families are combined by Fisher,
which would be indefensible one level down.

A family's best channel **is** Sidak-corrected over the channels that family
offered, and the reason is not the usual one. Sidak is normally the adjustment
for independent tests, and inside a family the channels are strongly dependent,
so as an error-rate correction it would be the wrong instrument — the
permutation controls the error rate exactly, and an analytic correction on top
of an exact null is a second, worse null. It is kept because it is a monotone
transform that buys **separation**: removing it makes every candidate's family
evidence shrink together, which moves the permuted maximum as much as the
observed one, and the engine measurably stops being able to be moved at all
(ADR-029 §8: the no-Sidak variant abstains at p = 0.073 on the mutation the
shipped one fires at).

*(Corrected at the Phase 6 gate, 2026-08-10. This paragraph previously said the
opposite — that the correction was **not** applied and that applying it left
the report abstaining on a poisoned world — while `_family_evidence` twelve
lines down applied it and said so, and ADR-029 §8's shipped row is
"sidak + own_scale". Two records of one decision inside one module, disagreeing,
and the code was never wrong. It is the same failure `statistics.py` records for
its own registry table, and the same fix: the module that owns a decision states
it once.)*

**Calibrate by permuting the candidate label inside its own stratum, jointly
within a family and independently across families.** Joint within a family
preserves the dependence that exists under the null; independent across
families destroys exactly what convergence is about. And the permutation only
exchanges candidates that *have* that family, so a candidate's family coverage
survives its own null - without that, shuffling a family's column moves the
zeros of coverage-poor candidates onto coverage-rich ones and the observed
score beats its own null by construction.

**One permutation for the whole fab, not a correction stack, and the maximum
is studentized.** The strata are scored in one loop and the family-wise
statement is the global maximum over every candidate of every stratum. A Sidak
product over strata would treat a chamber and its own tool as two independent
questions, which they are not, and measured 0.015 against a nominal 0.05 -
conservative enough to cost most of the power the engine has.

The maximum is taken over **studentized** scores, each candidate standardized
against its own permutation distribution, because the raw score is not
comparable across strata: a rank inside a stratum of eighty-four recipes
reaches 1/85 while a rank inside a stratum of seven chambers stops at 1/8, so a
raw maximum is decided by whichever stratum is largest rather than by whichever
candidate moved. Measured before the fix: poisoning one chamber on three
evidence families moved its own p-value from 0.394 to 0.019 while the
family-wise statement moved from 0.151 to 0.141 - the engine saw it and the
abstention could not. Two passes over the same seeded permutations keep the
result deterministic and the null exact.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from fabops.diagnosis.candidates import Panel
from fabops.diagnosis.channels import family_of
from fabops.diagnosis.model import Anchor, Evidence
from fabops.diagnosis.statistics import DEFAULT_STATISTIC, evaluate

__all__ = [
    "ALPHA",
    "MIN_STRATUM",
    "PERMUTATIONS",
    "PERMUTATION_SEED",
    "ChannelStanding",
    "Decision",
    "Stratum",
    "evidence_of",
    "score",
    "sidak",
]

#: The declared level the abstention is read at.
#:
#: **Phase 6 measured the curve this level sits on (ADR-031) and left the level
#: where it was.** On 660 fault-free worlds from three disjoint seed ranges and
#: the 21 faulted development datasets, at the shipped anchor and statistic:
#:
#:     alpha    realized false alarms    detections (21 development faults)
#:     0.0027           0.0000                        0
#:     0.010            0.0106                        0
#:     0.050            0.0667                        0
#:     0.100            0.1303                        6
#:     0.200            0.2197                        7
#:
#: Three things the table settles, and none of them is comfortable.
#:
#: At the declared level this engine fires on **none** of the development
#: faults. At 0.10 it finds six, at a realized false-alarm rate of one world in
#: eight. And at **0.0027** — the fab's own control-limit convention, the
#: 3-sigma multiple eight of the nine `alarms.codes` in the world template
#: declare, and the level ADR-027 borrowed for the evaluator's per-world
#: *action* limit — it finds nothing, which is the number to quote if a report
#: were ever wired to dispatch a technician rather than to be read by one.
#:
#: **The level stays 0.05, and the reason it does not move to 0.10 is the
#: reason that matters.** Moving it would be choosing a level off the detection
#: column, and a level chosen against the faulted population is precisely what
#: `DIAGNOSIS_CONTRACT.md` §8's second decision forbids ("calibrated against the
#: null worlds, never against the faulted ones"). The principle available is the
#: one ADR-027 already established on the evaluator's side: an *action* limit
#: for a per-world decision, a *screening* level where there is one verdict and
#: power matters. This abstention chooses between offering a ranked candidate
#: list with its evidence and saying "insufficient evidence"; a human takes the
#: action afterwards. It is a screening decision, so it reads at the screening
#: level, and both sides of the boundary now use one vocabulary.
#:
#: What a *fab* should declare is still open, and now for a stated reason
#: rather than an unexamined one: closing it needs the relative cost of a
#: missed excursion and a wasted investigation, this project has no cost model,
#: and inventing one to justify a number is the architecture invention every
#: gate here has refused. The curve above is published so that whoever has one
#: does not have to re-measure it.
ALPHA = 0.05

#: Permutation count. Fixes the resolution of every p-value at 1/(N+1).
PERMUTATIONS = 2000

#: Fixed so the same database gives the same report. The engine draws no other
#: random numbers.
PERMUTATION_SEED = 20260809

#: Fewest members a stratum needs before ranking inside it means anything.
MIN_STRATUM = 3


def sidak(p: float, k: int) -> float:
    """Family-wise adjustment for `k` comparisons of the same question."""
    return 1.0 - (1.0 - min(max(p, 0.0), 1.0)) ** max(k, 1)


@dataclass(frozen=True)
class ChannelStanding:
    """One candidate's standing on one channel, and the anchor behind it."""

    channel: str
    value: float
    rank: int
    of: int
    p: float
    z: float
    support: int
    anchor: Anchor


@dataclass(frozen=True)
class Stratum:
    """One exchangeable block: candidates of one kind sharing one role."""

    kind: str
    role: str
    members: tuple[str, ...]
    families: tuple[str, ...]
    standings: Mapping[str, tuple[ChannelStanding, ...]]
    family_p: Mapping[str, Mapping[str, float]]

    @property
    def label(self) -> str:
        return f"{self.kind}/{self.role}" if self.role else self.kind


@dataclass(frozen=True)
class Decision:
    """What the decision plane concluded about the whole fab."""

    strata: tuple[Stratum, ...]
    score: Mapping[tuple[str, str], float]
    p_candidate: Mapping[tuple[str, str], float]
    p_familywise: float
    permutations: int


def _standings(panels: Sequence[Panel], anchors: Sequence[Anchor],
               statistic: str) -> dict[str, dict[str, ChannelStanding]]:
    """Per candidate, per channel: the standing at the shared anchors.

    Every candidate faces the same anchors, so any selection among them
    inflates all of them alike and the permutation - which recomputes under
    the same anchors - absorbs it.
    """
    out: dict[str, dict[str, ChannelStanding]] = defaultdict(dict)
    for panel in panels:
        usable = panel.usable()
        if len(usable) < MIN_STRATUM:
            continue
        scale = panel.scale()
        if scale <= 0:
            continue
        best: dict[str, tuple[float, Anchor]] = {}
        for entity in usable:
            series, index = panel.series[entity], panel.index[entity]
            top, top_anchor = 0.0, None
            for anchor in anchors:
                value = evaluate(statistic, series, index, anchor.day, scale)
                if top_anchor is None or value > top:
                    top, top_anchor = value, anchor
            if top_anchor is not None:
                best[entity] = (top, top_anchor)
        if len(best) < MIN_STRATUM:
            continue
        ordered = sorted(best, key=lambda e: (-best[e][0], e))
        total = len(ordered)
        for position, entity in enumerate(ordered, start=1):
            value, anchor = best[entity]
            support = sum(n for _v, n in panel.raw[entity])
            out[entity][panel.channel] = ChannelStanding(
                channel=panel.channel, value=value, rank=position, of=total,
                p=position / (total + 1),
                z=_leave_one_out_z(best, entity),
                support=support, anchor=anchor)
    return {entity: dict(channels) for entity, channels in out.items()}


def _leave_one_out_z(best: Mapping[str, tuple[float, Anchor]],
                     entity: str) -> float:
    """How far one candidate sits from its peers, in the peers' own spread.

    Leave-one-out on purpose: including the candidate in the spread it is
    measured against shrinks exactly the deviation being measured, which
    flatters a weak standing and hides a strong one.
    """
    others = [v for name, (v, _a) in best.items() if name != entity]
    if len(others) < 2:
        return 0.0
    mean = sum(others) / len(others)
    variance = sum((v - mean) ** 2 for v in others) / len(others)
    spread = math.sqrt(variance) if variance > 0 else 0.0
    if spread <= 0:
        return 0.0
    return (best[entity][0] - mean) / spread


def _family_evidence(channels: Mapping[str, ChannelStanding]
                     ) -> dict[str, float]:
    """Each family's best channel, as a rank among contemporaneous peers,
    Sidak-corrected over the channels that family actually offered.

    The correction is *not* here to control an error rate - the permutation
    does that exactly. It is here because it is the transform that was
    measured: removing it made every candidate's family evidence smaller
    together, which moved the permuted maximum as much as the observed one and
    cost real detection (scenario B at `obvious` went from a family-wise 0.009
    to 0.092). What a monotone transform inside the statistic buys is
    separation, not validity, and this one buys separation.
    """
    grouped: dict[str, list[float]] = defaultdict(list)
    for channel, standing in channels.items():
        grouped[family_of(channel)].append(standing.p)
    return {family: sidak(min(values), len(values))
            for family, values in grouped.items()}


def score(panels: Sequence[Panel], anchors: Sequence[Anchor],
          statistic: str = DEFAULT_STATISTIC,
          permutations: int = PERMUTATIONS,
          seed: int = PERMUTATION_SEED) -> Decision:
    """Rank every stratum and calibrate the fab against one permutation null."""
    by_stratum: dict[tuple[str, str], list[Panel]] = defaultdict(list)
    for panel in panels:
        by_stratum[panel.stratum].append(panel)

    strata: list[Stratum] = []
    columns: list[dict[str, list[float]]] = []
    present: list[dict[str, list[int]]] = []
    for (kind, role), group in sorted(by_stratum.items()):
        standings = _standings(group, anchors, statistic)
        if len(standings) < MIN_STRATUM:
            continue
        family_p = {entity: _family_evidence(channels)
                    for entity, channels in standings.items()}
        members = tuple(sorted(family_p))
        families = tuple(sorted({f for per in family_p.values() for f in per}))
        if not families:
            continue
        strata.append(Stratum(
            kind=kind, role=role, members=members, families=families,
            standings={e: tuple(sorted(c.values(),
                                       key=lambda s: (s.p, -s.z, s.channel)))
                       for e, c in standings.items()},
            family_p=family_p))
        columns.append({
            family: [-2.0 * math.log(max(family_p[e].get(family, 1.0), 1e-12))
                     for e in members]
            for family in families})
        present.append({
            family: [i for i, e in enumerate(members)
                     if family in family_p[e]]
            for family in families})

    if not strata:
        return Decision(strata=(), score={}, p_candidate={},
                        p_familywise=1.0, permutations=permutations)

    observed: dict[tuple[str, str], float] = {}
    per_stratum_observed: list[list[float]] = []
    global_max = -math.inf
    for stratum, column in zip(strata, columns):
        values = [sum(column[f][i] for f in stratum.families)
                  for i in range(len(stratum.members))]
        per_stratum_observed.append(values)
        for entity, value in zip(stratum.members, values):
            observed[(stratum.kind, entity)] = value
            global_max = max(global_max, value)

    # ---- pass one: what each candidate's own permutation distribution is.
    rng = random.Random(seed)
    totals = [[0.0] * len(s.members) for s in strata]
    squares = [[0.0] * len(s.members) for s in strata]
    beyond_each = [[0] * len(s.members) for s in strata]
    shuffled = [{f: list(c[f]) for f in c} for c in columns]

    def _permute() -> list[list[float]]:
        drawn: list[list[float]] = []
        for index, stratum in enumerate(strata):
            column, slots, buffer = (columns[index], present[index],
                                     shuffled[index])
            for family in stratum.families:
                positions = slots[family]
                values = [column[family][i] for i in positions]
                rng.shuffle(values)
                for position, value in zip(positions, values):
                    buffer[family][position] = value
            drawn.append([sum(buffer[f][i] for f in stratum.families)
                          for i in range(len(stratum.members))])
        return drawn

    for _ in range(permutations):
        for index, values in enumerate(_permute()):
            own = per_stratum_observed[index]
            for i, value in enumerate(values):
                totals[index][i] += value
                squares[index][i] += value * value
                if value >= own[i]:
                    beyond_each[index][i] += 1

    centre = [[t / permutations for t in row] for row in totals]
    spread = []
    for index, row in enumerate(squares):
        spread.append([
            max(math.sqrt(max(row[i] / permutations
                              - centre[index][i] ** 2, 0.0)), 1e-9)
            for i in range(len(row))])

    def _studentize(index: int, i: int, value: float) -> float:
        return (value - centre[index][i]) / spread[index][i]

    observed_max = max(
        _studentize(index, i, per_stratum_observed[index][i])
        for index, stratum in enumerate(strata)
        for i in range(len(stratum.members)))

    # ---- pass two: the null of the studentized maximum, same seed, same draws.
    rng = random.Random(seed)
    shuffled = [{f: list(c[f]) for f in c} for c in columns]
    beyond_max = 0
    for _ in range(permutations):
        permuted_max = -math.inf
        for index, values in enumerate(_permute()):
            for i, value in enumerate(values):
                studentized = _studentize(index, i, value)
                if studentized > permuted_max:
                    permuted_max = studentized
        if permuted_max >= observed_max:
            beyond_max += 1

    p_candidate: dict[tuple[str, str], float] = {}
    for index, stratum in enumerate(strata):
        for i, entity in enumerate(stratum.members):
            p_candidate[(stratum.kind, entity)] = (
                (beyond_each[index][i] + 1) / (permutations + 1))

    return Decision(strata=tuple(strata), score=observed,
                    p_candidate=p_candidate,
                    p_familywise=(beyond_max + 1) / (permutations + 1),
                    permutations=permutations)


def evidence_of(standings: Sequence[ChannelStanding],
                statistic: str) -> tuple[Evidence, ...]:
    """The falsifiable entries: what to re-run, and against what."""
    return tuple(
        Evidence(
            family=family_of(s.channel), channel=s.channel, statistic=statistic,
            value=round(s.value, 6),
            comparison=(f"{s.z:+.2f} peer spreads from the other {s.of - 1} "
                        f"candidates of the same kind and role (rank {s.rank} "
                        f"of {s.of}), at the shared anchor on day "
                        f"{s.anchor.day:.0f}"),
            support=s.support, rank=s.rank, of=s.of)
        for s in standings)
