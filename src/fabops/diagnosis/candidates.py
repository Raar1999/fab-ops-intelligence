"""
candidates.py — the hypothesis space, and the panels the evidence lives on.

Two jobs, in this order:

1. **Enumerate.** Every entity of every declared kind that the run log
   actually records exposure for. Enumeration comes from the data, never from
   a list: an engine carrying a roster of entity names would be answering from
   the roster.
2. **Bin.** Per (kind, channel), reduce each entity's observations to a weekly
   series and subtract the contemporaneous mean of its peers. Weekly because
   that is the grain the world's own severity calibration is stated in, and
   because it is wide against the correlation time of the state the channels
   read. Peer-differencing because a term shared by every entity in a week -
   the fab-wide wander, the lot-to-lot term - is not evidence about any of
   them.

An entity that cannot carry a series is **not** dropped. It is returned with
`assessable = False` and the reason, because a hypothesis that disappears has
been rejected without being asked - which is exactly how a competing
explanation becomes a walkover.
"""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from fabops.diagnosis.channels import NO_ROLE, Observation, is_count_channel
from fabops.diagnosis.model import ENTITY_KINDS

__all__ = [
    "BIN_DAYS",
    "MIN_BINS",
    "MIN_BIN_SUPPORT",
    "MIN_PEERS",
    "CandidateSupport",
    "Panel",
    "build_panels",
    "enumerate_candidates",
]

#: Bin width in days. Seven is the grain the world states its own severity
#: calibration in, and it is wide against the correlation time of the states
#: the channels read, which is what keeps consecutive bins close to
#: independent.
BIN_DAYS = 7.0

#: Fewest observations a bin must carry before its mean is used.
MIN_BIN_SUPPORT = 3

#: Fewest usable bins before an entity has a series at all.
MIN_BINS = 8

#: Fewest contemporaneous peers a bin needs for the peer mean to mean
#: anything.
MIN_PEERS = 2


@dataclass(frozen=True)
class CandidateSupport:
    """What the data can say about one entity, before any statistic."""

    kind: str
    name: str
    observations: int
    channels: tuple[str, ...]
    assessable: bool
    reason: str | None = None


@dataclass(frozen=True)
class Panel:
    """One (kind, role, channel) block: every peer's peer-differenced series.

    The `role` is what makes the members comparable. Equipment is exchangeable
    within a role and not across a fab - a chamber every wafer passes and a
    chamber a third of them reach are not draws from one distribution - and
    the permutation null rests on exactly that exchangeability. Ranking them
    together made the engine fire on a fifth of fault-free worlds at a nominal
    twentieth; separating them brought it to the declared level.
    """

    kind: str
    role: str
    channel: str
    bins: int
    members: tuple[str, ...]
    #: entity -> per-bin (mean or None, support)
    raw: Mapping[str, tuple[tuple[float | None, int], ...]]
    #: entity -> the peer-differenced series, restricted to usable bins
    series: Mapping[str, tuple[float, ...]]
    #: entity -> the bin index each series point came from
    index: Mapping[str, tuple[int, ...]]

    @property
    def stratum(self) -> tuple[str, str]:
        return (self.kind, self.role)

    def usable(self) -> tuple[str, ...]:
        return tuple(e for e in self.members
                     if len(self.series.get(e, ())) >= MIN_BINS)

    def scale(self) -> float:
        """One spread for the channel, pooled over the exchangeable entities.

        Pooled rather than per-entity because a series of a dozen points is a
        poor estimate of its own spread, and because the entities of a
        fault-free world are exchangeable - which is the same assumption the
        permutation null rests on, used here for the same reason.
        """
        deviations: list[float] = []
        degrees = 0
        for entity in self.usable():
            values = self.series[entity]
            mean = sum(values) / len(values)
            deviations += [(v - mean) ** 2 for v in values]
            degrees += len(values) - 1
        if degrees <= 0:
            return 0.0
        return (sum(deviations) / degrees) ** 0.5


def enumerate_candidates(observations: Iterable[Observation]
                         ) -> tuple[CandidateSupport, ...]:
    """Every entity the run log records, with what the data can say about it."""
    seen: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0])
    channels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for observation in observations:
        for kind, name in observation.keys.items():
            if kind not in ENTITY_KINDS or not name:
                continue
            seen[(kind, name)][0] += 1
            channels[(kind, name)].add(observation.channel)
    return tuple(
        CandidateSupport(kind=kind, name=name, observations=count[0],
                         channels=tuple(sorted(channels[(kind, name)])),
                         assessable=True)
        for (kind, name), count in sorted(seen.items()))


def build_panels(observations: Sequence[Observation], horizon_days: float,
                 kinds: Sequence[str] = ENTITY_KINDS,
                 bin_days: float = BIN_DAYS,
                 roles: Mapping[str, Mapping[str, str]] | None = None
                 ) -> tuple[Panel, ...]:
    """Every (kind, role, channel) panel the data supports."""
    nbins = max(1, int(round(horizon_days / bin_days)))
    roles = roles or {}
    grouped: dict[tuple[str, str, str],
                  list[tuple[str, int, float]]] = defaultdict(list)
    for observation in observations:
        index = int(observation.day // bin_days)
        if not 0 <= index < nbins:
            continue
        for kind in kinds:
            name = observation.keys.get(kind)
            if not name:
                continue
            role = roles.get(kind, {}).get(name, NO_ROLE)
            grouped[(kind, role, observation.channel)].append(
                (name, index, observation.value))

    panels: list[Panel] = []
    for (kind, role, channel), rows in sorted(grouped.items()):
        buckets: dict[str, list[list[float]]] = defaultdict(
            lambda: [[] for _ in range(nbins)])
        for name, index, value in rows:
            buckets[name][index].append(value)
        if len(buckets) < MIN_PEERS + 1:
            continue
        counting = is_count_channel(channel)
        raw: dict[str, tuple[tuple[float | None, int], ...]] = {}
        for name, per_bin in buckets.items():
            reduced: list[tuple[float | None, int]] = []
            for values in per_bin:
                if counting:
                    reduced.append((float(len(values)), len(values)))
                elif len(values) >= MIN_BIN_SUPPORT:
                    reduced.append((sum(values) / len(values), len(values)))
                else:
                    reduced.append((None, len(values)))
            raw[name] = tuple(reduced)

        series: dict[str, tuple[float, ...]] = {}
        index_of: dict[str, tuple[int, ...]] = {}
        for name in raw:
            peers = [other for other in raw if other != name]
            values: list[float] = []
            indices: list[int] = []
            for k in range(nbins):
                own = raw[name][k][0]
                if own is None:
                    continue
                peer_values = [raw[p][k][0] for p in peers
                               if raw[p][k][0] is not None]
                if len(peer_values) < MIN_PEERS:
                    continue
                values.append(own - sum(peer_values) / len(peer_values))
                indices.append(k)
            series[name] = tuple(values)
            index_of[name] = tuple(indices)

        panels.append(Panel(kind=kind, role=role, channel=channel, bins=nbins,
                            members=tuple(sorted(raw)), raw=raw,
                            series=series, index=index_of))
    return tuple(panels)


def assess(supports: Sequence[CandidateSupport],
           panels: Sequence[Panel]) -> tuple[CandidateSupport, ...]:
    """Mark which enumerated entities the panels can actually score.

    The reason is kept because it is the difference between "this hypothesis
    lost" and "this hypothesis was never asked", and a report that cannot tell
    them apart is not a comparison.
    """
    scorable: dict[tuple[str, str], int] = defaultdict(int)
    best_bins: dict[tuple[str, str], int] = defaultdict(int)
    for panel in panels:
        for entity in panel.members:
            count = len(panel.series.get(entity, ()))
            key = (panel.kind, entity)
            best_bins[key] = max(best_bins[key], count)
            if count >= MIN_BINS:
                scorable[key] += 1

    out: list[CandidateSupport] = []
    for support in supports:
        key = (support.kind, support.name)
        if scorable.get(key):
            out.append(support)
            continue
        reason = (
            f"no channel gives this {support.kind} at least {MIN_BINS} "
            f"comparable {BIN_DAYS:.0f}-day bins with at least "
            f"{MIN_BIN_SUPPORT} observations and {MIN_PEERS} contemporaneous "
            f"peers of the same role; the best any channel reaches is "
            f"{best_bins.get(key, 0)}")
        out.append(CandidateSupport(
            kind=support.kind, name=support.name,
            observations=support.observations, channels=support.channels,
            assessable=False, reason=reason))
    return tuple(out)
