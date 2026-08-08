"""
base.py — what a mechanism is, and the strict boundary of what one may see.

A mechanism is the *only* place in FabSim where a scenario's intent becomes
physics, so it is the place where answer-blindness is easiest to lose. The
interface is therefore built around what a mechanism is **denied**::

    MechanismContext
      grid          the shared clock, as latent grid points
      onset_index   when this activation begins
      profile       step | ramp(ramp_days) | intermittent
      magnitude     already severity-calibrated, in latent units
      defaults      this mechanism's world-template constants
      rng           one named deterministic substream

Nothing else reaches it. In particular a mechanism cannot see:

* **which chamber it is acting on.** `contribute` returns one drive series for
  the whole activation, and the latent engine applies it to every chamber the
  target resolves to. A mechanism physically cannot write ``if chamber ==
  ...`` because the chamber is not in scope — the strongest available form of
  anti-leakage rule D6, and stronger than a lint that merely forbids the
  literal.
* **the latent it perturbs.** The design sketches `contribute(latent, t)`;
  this implementation passes no latent state, which is a deliberate
  tightening. A mechanism that cannot read the state it drives cannot react to
  another mechanism's effect, cannot notice the benign offset it shares a
  chamber with, and cannot behave differently in a null world than in a
  faulted one. It emits an open-loop drive; the engine integrates it.
* **anything observable.** No runs, no measurements, no defects, no yield, no
  database, no truth from another mechanism, and nothing from the future.

What a mechanism returns is a **drive series**: one number per grid point, in
the units the target latent's family expects (a level for `ar1`, an added
growth rate for `accumulation`). It never returns an observation, a label, a
defect class, an alarm code or a yield term — those planes are downstream and
independent, which is what ADR-004 requires.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "MECHANISM_KINDS",
    "PROFILE_ENVELOPES",
    "Mechanism",
    "MechanismContext",
    "OffsetMechanism",
    "TrajectoryMechanism",
    "profile_envelope",
]

#: What a mechanism does to the latent plane. `trajectory` mechanisms drive a
#: latent over time (the faults); `offset` mechanisms only scale the permanent
#: benign offset every chamber already carries (the distractor).
MECHANISM_KINDS = ("trajectory", "offset")

#: Profile shapes the scenario contract may state. Kept here as a literal
#: rather than imported from `fabsim.scenario`: the mechanism library is
#: physics and must not depend on the configuration contract.
#: `test_profile_vocabularies_agree` holds the two copies together.
PROFILE_ENVELOPES = ("step", "ramp", "intermittent")


@dataclass(frozen=True)
class MechanismContext:
    """Everything one mechanism activation is allowed to know."""

    #: The shared latent grid — the project's one clock, sampled.
    grid: Any
    #: First grid point at which the activation is in force.
    onset_index: int
    #: Canonical profile object from the scenario (`type`, maybe `ramp_days`).
    profile: Mapping[str, Any]
    #: Severity-calibrated amplitude, in units of the target latent's own
    #: healthy weekly σ. The calibration happens outside: a mechanism must not
    #: be able to choose how big it is.
    magnitude: float
    #: World-template constants for this mechanism (rule D6: constants live in
    #: the template, never keyed to an entity in code).
    defaults: Mapping[str, Any]
    #: Shared profile constants (`intermittent_period_days`, `duty`).
    profile_defaults: Mapping[str, float]
    #: One named deterministic substream, already derived for this activation.
    rng: Any


def profile_envelope(context: MechanismContext) -> tuple[float, ...]:
    """The activation's shape over the grid, in [0, 1], zero before onset.

    Shared by every trajectory mechanism because the *shape* of an activation
    is a property of the scenario's profile, not of the physics it drives —
    which is also why `profile: step` turns `param_drift` into the deferred
    scenario D without a line of new mechanism code.
    """
    grid = context.grid
    profile_type = context.profile["type"]
    envelope = [0.0] * grid.points

    if profile_type == "ramp":
        ramp_points = max(1.0, context.profile["ramp_days"] * grid.points_per_day)
        for index in range(context.onset_index, grid.points):
            envelope[index] = min(1.0, (index - context.onset_index + 1)
                                  / ramp_points)
        return tuple(envelope)

    if profile_type == "intermittent":
        period = max(1.0, context.profile_defaults["intermittent_period_days"]
                     * grid.points_per_day)
        duty = context.profile_defaults["intermittent_duty"] * period
        # A phase per activation, so intermittent faults do not all switch on
        # the same tick as their onset — an alignment that would be a timing
        # fingerprint rather than physics.
        phase = context.rng.random() * period
        for index in range(context.onset_index, grid.points):
            position = (index - context.onset_index + phase) % period
            envelope[index] = 1.0 if position < duty else 0.0
        return tuple(envelope)

    for index in range(context.onset_index, grid.points):
        envelope[index] = 1.0
    return tuple(envelope)


class Mechanism:
    """One entry in the mechanism library.

    Subclasses set `name`, `latent` and `kind` and implement the one method
    their kind requires. Instances are stateless singletons: a mechanism holds
    no per-activation state, so two activations of one mechanism cannot leak
    into each other.
    """

    #: Scenario vocabulary (`SCENARIO_SPECIFICATION.md` §2.1).
    name: str = ""
    #: Which latent this mechanism drives; `None` for offset mechanisms.
    latent: str | None = None
    kind: str = "trajectory"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Mechanism {self.name}>"


class TrajectoryMechanism(Mechanism):
    """A mechanism that drives one latent over time."""

    kind = "trajectory"

    def contribute(self, context: MechanismContext) -> tuple[float, ...]:
        """The drive series this activation adds to its target latent."""
        raise NotImplementedError


class OffsetMechanism(Mechanism):
    """A mechanism that only scales the permanent offset every chamber has.

    It has no trajectory and no onset: that is the entire point of it
    (`CAUSAL_MECHANISM_MODEL.md` §9 — "no temporal change"). What separates it
    from a fault is its *shape in time*, not a label, which is what the later
    diagnosis engine has to discover rather than be told.
    """

    kind = "offset"
    latent = None

    def offset_sigma(self, magnitude: str,
                     defaults: Mapping[str, Any]) -> float:
        """Extra offset spread, in units of the latent's healthy weekly σ."""
        raise NotImplementedError
