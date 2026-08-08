"""
param_drift.py — M2, the process-drift mechanism (`CAUSAL_MECHANISM_MODEL.md`
§9), and the mechanism behind scenario C.

A delivery subsystem's delivered-versus-set deviation walks off: the latent
`param_bias` acquires a slowly growing level. The drive is a level, like M1's,
and the *slowness* comes from the scenario's `profile: ramp` rather than from
anything here — which is what lets the deferred scenario D (a sudden
excursion) ship later as `profile: step` with no new mechanism code, exactly
as `SCENARIO_SPECIFICATION.md` §3 D promises.

Two properties this mechanism relies on the engine for, and does not itself
implement:

* **the drift is never a clean straight line.** `param_bias` is an AR(1)
  process with φ≈0.98 running on every chamber in the fab, healthy or not; the
  activation moves the level the AR(1) reverts toward. What a later monitor
  sees is a trend buried in wander, not a ruler.
* **a PM partially recentres it.** Recalibration removes most of the current
  deviation (`CAUSAL_MECHANISM_MODEL.md` §6), and the drift then resumes from
  there — which is what makes "did the intervention work?" a real question
  rather than a formality. The reset lives in the latent engine because it
  applies to healthy chambers too.
"""
from __future__ import annotations

from fabsim.mechanisms.base import (
    MechanismContext,
    TrajectoryMechanism,
    profile_envelope,
)

__all__ = ["ParamDrift"]


class ParamDrift(TrajectoryMechanism):
    """`param_bias` walks off its setpoint and is partly recovered by PM."""

    name = "param_drift"
    latent = "param_bias"

    def contribute(self, context: MechanismContext) -> tuple[float, ...]:
        magnitude = context.magnitude
        return tuple(magnitude * value
                     for value in profile_envelope(context))
