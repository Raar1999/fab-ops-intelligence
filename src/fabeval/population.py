"""
population.py — which datasets a number was measured on, declared once.

Every claim this project makes about a diagnostic system is a claim about a
*population*, and the way such a claim goes wrong is not usually fraud — it is
that the population which produced the number and the population which chose
the method turn out to be the same one. Four measurement gates in this
repository each found a criterion whose reference distribution had never been
computed; this module exists so that the fifth kind of mistake, fitting to the
evaluation set, is visible in a diff.

Three populations, disjoint by construction and asserted to be:

    DEVELOPMENT   seeds a method may be looked at on
    HELD_OUT      seeds a method may be scored on, once, after it is frozen
    CALIBRATION   fault-free seeds a null may be measured on

`assert_disjoint` is the whole enforcement, and it is deliberately blunt: a
seed that appears in two roles is a bug in the experiment, not a judgement
call.

**This module does not make anything independent.** It records which seeds were
used for what, so that a reader can check the claim rather than trust it, and
so that the next gate inherits the split instead of inventing one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

__all__ = [
    "CALIBRATION_SEEDS",
    "DEVELOPMENT_SEEDS",
    "HELD_OUT_SEEDS",
    "MINIMUM_LIBRARY_FOR_A_CLAIM",
    "Population",
    "assert_disjoint",
    "claimable",
]

#: The seeds the library publishes and every design gate looked at.
DEVELOPMENT_SEEDS: tuple[int, ...] = (42, 101, 2024)

#: Seeds reserved for scoring a frozen method. Nothing in `src/` may be tuned
#: while looking at these.
HELD_OUT_SEEDS: tuple[int, ...] = (555, 777, 31415, 2718, 1618)

#: Fault-free seeds a null distribution may be measured on. Disjoint from both
#: of the above so that a calibration cannot quietly become a fit.
CALIBRATION_SEEDS: tuple[int, ...] = tuple(range(300000, 300200))

#: How many scenarios the library must hold before a diagnosis number may be
#: called a capability rather than a measurement. `EXPANSION_ROADMAP` Phase 6
#: asks for at least ten: per fault class x severity, plus a null, plus a
#: confounded one.
MINIMUM_LIBRARY_FOR_A_CLAIM = 10


@dataclass(frozen=True)
class Population:
    """A named set of datasets a number was measured on."""

    name: str
    scenarios: tuple[str, ...]
    seeds: tuple[int, ...]
    role: str

    @property
    def size(self) -> int:
        return len(self.scenarios) * len(self.seeds)

    def describe(self) -> str:
        return (f"{self.name}: {len(self.scenarios)} scenario(s) x "
                f"{len(self.seeds)} seed(s) = {self.size} dataset(s) "
                f"[{self.role}]")


def assert_disjoint() -> None:
    """The one enforcement. A seed in two roles is a broken experiment."""
    roles: Mapping[str, tuple[int, ...]] = {
        "development": DEVELOPMENT_SEEDS,
        "held-out": HELD_OUT_SEEDS,
        "calibration": CALIBRATION_SEEDS,
    }
    names = list(roles)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            overlap = set(roles[first]) & set(roles[second])
            if overlap:
                raise AssertionError(
                    f"seeds {sorted(overlap)} are used both as {first} and as "
                    f"{second}; a population that overlaps its own control "
                    f"cannot support a claim")


def claimable(scenarios: Sequence[str]) -> tuple[bool, str]:
    """May a number measured on these scenarios be called a capability?

    Returns the verdict and the sentence that has to accompany the number when
    the verdict is no — because the failure mode this guards against is not a
    wrong number, it is a correct number quoted without its population.
    """
    if len(scenarios) >= MINIMUM_LIBRARY_FOR_A_CLAIM:
        return True, ""
    return False, (
        f"measured on {len(scenarios)} scenario(s); a capability claim needs "
        f"at least {MINIMUM_LIBRARY_FOR_A_CLAIM} "
        f"(EXPANSION_ROADMAP Phase 6), so this is a measurement on a named "
        f"population and not a claim about the system")
