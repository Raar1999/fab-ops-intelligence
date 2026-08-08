"""
particle_load.py — M3, the particle excursion (`CAUSAL_MECHANISM_MODEL.md` §9),
and the mechanism behind scenario I.

`particle_load` is the one latent whose *baseline* is not wander but
accumulation: it climbs between cleans and a PM takes it back to zero, giving
every chamber in the fab the sawtooth of §3. An excursion is therefore not "a
level appears" but "the chamber starts dirtying faster than it should", and
the drive this mechanism returns is an **added growth rate**, not a level —
the accumulation family integrates it.

Shape, per §9's "step + growth":

* a `step_fraction` of the magnitude arrives at once, spread over the first
  grid step, because whatever released the particles released some of them
  immediately;
* the remainder arrives as extra growth over `escalation_days`, so the load
  passes the nominal magnitude at the end of the escalation and keeps
  climbing until something cleans it.

`escalation_days` is short compared with the PM interval, which is what makes
this "the fastest of the three" and what compresses scenario I's whole arc —
onset, escalation, intervention, recovery — into a window an investigator can
bound. Both constants are world-template values, never keyed to an entity.

The mechanism produces no defect count, no PARTICLE_HI alarm and no yield dip.
It raises one hidden number faster than usual.
"""
from __future__ import annotations

from fabsim.mechanisms.base import (
    MechanismContext,
    TrajectoryMechanism,
    profile_envelope,
)

__all__ = ["ParticleExcursion"]


class ParticleExcursion(TrajectoryMechanism):
    """`particle_load` steps up and then accumulates faster than baseline."""

    name = "particle_excursion"
    latent = "particle_load"

    def contribute(self, context: MechanismContext) -> tuple[float, ...]:
        grid = context.grid
        envelope = profile_envelope(context)
        step_fraction = float(context.defaults["step_fraction"])
        escalation_days = float(context.defaults["escalation_days"])

        # Both terms are rates: the engine multiplies by the step length, so
        # the shapes below are independent of the grid resolution.
        step_rate = (context.magnitude * step_fraction) * grid.points_per_day
        growth_rate = (context.magnitude * (1.0 - step_fraction)
                       / escalation_days)

        drive = []
        for index, value in enumerate(envelope):
            immediate = step_rate if index == context.onset_index else 0.0
            drive.append(value * growth_rate + immediate * value)
        return tuple(drive)
