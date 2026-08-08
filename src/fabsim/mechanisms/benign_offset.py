"""
benign_offset.py — M4, the standing distractor (`CAUSAL_MECHANISM_MODEL.md`
§9), and the reason a diagnosis engine has something honest to be wrong about.

This mechanism is **not a fault**, and the shape of this file is the argument.
It has no onset, no profile, no trajectory and no reset: it adds spread to the
permanent offset that *every* chamber in the fab already carries. A scenario
that declares a `benign_offset` distractor does not create an offset on that
chamber — it makes an offset that exists everywhere larger there.

That distinction is the whole of anti-leakage requirement F11. If offsets
existed only where a distractor was declared, "this chamber has an offset"
would be a categorical value with support only on named entities — leakage
class T3 — and a null dataset would be visibly cleaner than a faulted one.
Instead every chamber is offset, the declared ones are merely offset more, and
the magnitudes reach into the subtle-severity band so the two are not
separable by size.

What separates a benign offset from a fault is therefore only its **shape in
time**: an offset is there on day 1 and unchanged on day 84, and a PM does not
move it, while a fault has an onset and evolves. The later diagnosis engine has
to earn that distinction from temporal evidence. Nothing in the observable
plane will label it, and nothing here writes a yield term, a defect class or a
measurement — an offset is a hidden number that happens to be constant.
"""
from __future__ import annotations

from typing import Any, Mapping

from fabsim.mechanisms.base import OffsetMechanism

__all__ = ["BenignOffset"]


class BenignOffset(OffsetMechanism):
    """Permanent, stable extra offset; no onset, no evolution, no reset."""

    name = "benign_offset"

    def offset_sigma(self, magnitude: str,
                     defaults: Mapping[str, Any]) -> float:
        """Extra offset spread, in units of the latent's healthy weekly σ.

        Drawn from the same Gaussian family as the standing tool and chamber
        offsets, so a declared distractor widens a distribution rather than
        introducing one.
        """
        return float(defaults["magnitudes"][magnitude])
