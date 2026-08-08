"""
edge_uniformity.py — M1, the chamber equipment fault (`CAUSAL_MECHANISM_MODEL.md`
§9), and the mechanism behind scenarios B and G.

A chamber develops radial process non-uniformity: the latent `edge_uniformity`
departs from its healthy wander and stays departed until something repairs the
hardware. The drive is a level — the profile decides how quickly the level
arrives — because non-uniformity is a *state* of the chamber, not a rate.

What this mechanism does **not** do, and what makes the demo's successor
answer-blind: it produces no edge defects, no edge label, no CD deviation, no
alarm and no die kill. It raises one hidden number. Everything the audit
called "the edge story" is later, independent, and noisy — the edge-zone
defect elevation of scenario B is a consequence of geometry acting on this
latent, never of this file knowing what an edge defect is.

`edge_uniformity` is signed: a chamber may run edge-fast or edge-slow, and
healthy chambers wander either way around zero. The activation pushes one way,
which is what gives the later radial model a direction to work with.
"""
from __future__ import annotations

from fabsim.mechanisms.base import (
    MechanismContext,
    TrajectoryMechanism,
    profile_envelope,
)

__all__ = ["ChamberEdgeUniformity"]


class ChamberEdgeUniformity(TrajectoryMechanism):
    """`edge_uniformity` ↑, sustained; hardware, so a PM does not clear it."""

    name = "chamber_edge_uniformity"
    latent = "edge_uniformity"

    def contribute(self, context: MechanismContext) -> tuple[float, ...]:
        magnitude = context.magnitude
        return tuple(magnitude * value
                     for value in profile_envelope(context))
