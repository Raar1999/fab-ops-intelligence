"""
mechanisms — the fault/event library, and the registry that resolves a
scenario's declared mechanisms against a world.

The library is code, versioned as code (`FABSIM_DESIGN.md` §11): what a
mechanism *does* lives here, while every number it does it with lives in the
world template (rule D6). Adding a mechanism means adding one small module and
one registry entry; it touches no emitter, no schema and no scenario contract,
which is what keeps the deferred library (D, E, F, H, J) a configuration
question rather than an architectural one.

Resolution is the mirror of `fabsim.routing`: `fabsim.scenario` validates that
an event is *shaped* right, and this module validates that it is *true of a
world* — the mechanism exists, the tool exists, the chamber belongs to that
tool, and the latent it drives is one the world declares. A scenario naming a
chamber that does not exist would otherwise become a fault that silently never
happened.

Events and distractors are deliberately not interchangeable::

    events[]       trajectory mechanisms   have an onset and evolve
    distractors[]  offset mechanisms       permanent, no onset, no evolution

Stating one where the other belongs is rejected. The two are different *kinds*
of thing, and the difference — a shape in time, not a label — is precisely
what the later diagnosis engine must learn to tell apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from fabsim.mechanisms.base import (
    MECHANISM_KINDS,
    PROFILE_ENVELOPES,
    Mechanism,
    MechanismContext,
    OffsetMechanism,
    TrajectoryMechanism,
    profile_envelope,
)
from fabsim.mechanisms.benign_offset import BenignOffset
from fabsim.mechanisms.edge_uniformity import ChamberEdgeUniformity
from fabsim.mechanisms.param_drift import ParamDrift
from fabsim.mechanisms.particle_load import ParticleExcursion
from fabsim.scenario import ScenarioConfigError
from fabsim.world import World

__all__ = [
    "MECHANISMS",
    "MECHANISM_KINDS",
    "MECHANISM_NAMES",
    "PROFILE_ENVELOPES",
    "Mechanism",
    "MechanismContext",
    "OffsetMechanism",
    "ResolvedDistractor",
    "ResolvedEvent",
    "TrajectoryMechanism",
    "mechanism",
    "profile_envelope",
    "resolve_distractors",
    "resolve_events",
]

#: The Phase 1 library (`CAUSAL_MECHANISM_MODEL.md` §9), in a fixed order so
#: that iterating the registry is deterministic.
MECHANISMS: dict[str, Mechanism] = {
    m.name: m for m in (
        ChamberEdgeUniformity(),
        ParamDrift(),
        ParticleExcursion(),
        BenignOffset(),
    )
}

#: Registered names, sorted. Mirrored by `fabsim.world` so a world template can
#: validate its mechanism defaults without importing this package (which would
#: be a cycle); `test_mechanism_vocabularies_agree` holds the copies together.
MECHANISM_NAMES = tuple(sorted(MECHANISMS))


def mechanism(name: str) -> Mechanism:
    """The registered mechanism called `name`."""
    return MECHANISMS[name]


# ------------------------------------------------------------- resolved forms


@dataclass(frozen=True)
class ResolvedEvent:
    """One mechanism activation, bound to a world.

    `chamber_ids` is what the target actually resolves to: a chamber-scoped
    target is one chamber, a tool-wide target is every chamber of the tool.
    The mechanism itself never sees this — the latent engine applies one drive
    series to each of them.
    """

    index: int
    mechanism: str
    latent: str
    tool_id: int
    tool_name: str
    chamber_name: str | None
    chamber_ids: tuple[int, ...]
    onset_day: float
    profile: Mapping[str, Any]
    severity: str
    response: Mapping[str, Any]


@dataclass(frozen=True)
class ResolvedDistractor:
    """One declared benign structure, bound to a world."""

    index: int
    mechanism: str
    tool_id: int
    tool_name: str
    chamber_name: str | None
    chamber_ids: tuple[int, ...]
    magnitude: str


# ---------------------------------------------------------------- resolution


def _resolve_target(world: World, target: Mapping[str, Any],
                    path: str) -> tuple[int, str, str | None, tuple[int, ...]]:
    tool_name = target["tool"]
    tools_by_name = {t.tool_name: t for t in world.tools}
    if tool_name not in tools_by_name:
        raise ScenarioConfigError(
            f"unknown tool {tool_name!r} in world {world.template_name!r}",
            f"{path}.tool")
    tool = tools_by_name[tool_name]

    chamber_name = target.get("chamber")
    if chamber_name is None:
        return tool.tool_id, tool_name, None, tuple(tool.chamber_ids)

    chambers = {c.chamber_name: c for c in world.chambers_of(tool.tool_id)}
    if chamber_name not in chambers:
        raise ScenarioConfigError(
            f"tool {tool_name!r} has no chamber {chamber_name!r}; it has "
            + ", ".join(sorted(chambers)), f"{path}.chamber")
    return (tool.tool_id, tool_name, chamber_name,
            (chambers[chamber_name].chamber_id,))


def _registered(name: str, path: str) -> Mechanism:
    if name not in MECHANISMS:
        raise ScenarioConfigError(
            f"unknown mechanism {name!r}; the library holds "
            + ", ".join(MECHANISM_NAMES), path)
    return MECHANISMS[name]


def resolve_events(world: World,
                   events: Sequence[Mapping[str, Any]] = ()
                   ) -> tuple[ResolvedEvent, ...]:
    """Bind canonical scenario events to a world, in declaration order."""
    resolved: list[ResolvedEvent] = []
    for index, event in enumerate(events):
        path = f"events[{index}]"
        found = _registered(event["mechanism"], f"{path}.mechanism")
        if found.kind != "trajectory":
            raise ScenarioConfigError(
                f"{found.name!r} is a {found.kind} mechanism and has no onset; "
                "it belongs in `distractors`", f"{path}.mechanism")
        if found.latent not in world.observation.latents:
            raise ScenarioConfigError(
                f"{found.name!r} drives latent {found.latent!r}, which world "
                f"{world.template_name!r} does not declare",
                f"{path}.mechanism")
        tool_id, tool_name, chamber_name, chamber_ids = _resolve_target(
            world, event["target"], f"{path}.target")
        resolved.append(ResolvedEvent(
            index=index,
            mechanism=found.name,
            latent=found.latent,
            tool_id=tool_id,
            tool_name=tool_name,
            chamber_name=chamber_name,
            chamber_ids=chamber_ids,
            onset_day=event["onset_day"],
            profile=dict(event["profile"]),
            severity=event["severity"],
            response=dict(event["response"]),
        ))
    return tuple(resolved)


def resolve_distractors(world: World,
                        distractors: Sequence[Mapping[str, Any]] = ()
                        ) -> tuple[ResolvedDistractor, ...]:
    """Bind canonical scenario distractors to a world, in declaration order."""
    resolved: list[ResolvedDistractor] = []
    for index, distractor in enumerate(distractors):
        path = f"distractors[{index}]"
        found = _registered(distractor["mechanism"], f"{path}.mechanism")
        if found.kind != "offset":
            raise ScenarioConfigError(
                f"{found.name!r} is a {found.kind} mechanism and has an onset; "
                "it belongs in `events`", f"{path}.mechanism")
        tool_id, tool_name, chamber_name, chamber_ids = _resolve_target(
            world, distractor["target"], f"{path}.target")
        resolved.append(ResolvedDistractor(
            index=index,
            mechanism=found.name,
            tool_id=tool_id,
            tool_name=tool_name,
            chamber_name=chamber_name,
            chamber_ids=chamber_ids,
            magnitude=distractor["magnitude"],
        ))
    return tuple(resolved)
