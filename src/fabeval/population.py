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

    DEVELOPMENT   a method may be looked at on these
    HELD_OUT      a method may be scored on these, once, after it is frozen
    CALIBRATION   fault-free seeds a null may be measured on

**The split runs on two axes, because one is not enough.** Seeds alone were
what this module declared through Phase 1, and a seed split cannot protect a
method that was chosen while looking at a *scenario*: selecting a statistic on
`chamber_edge_uniformity` at seed 42 and scoring it on the same configuration
at seed 555 still fits the method to that mechanism, that target, that
severity and that onset. Phase 6 therefore declares a scenario split as well,
and `assert_disjoint` checks both.

**Which scenarios are development is settled by history, not by preference.**
The five Phase 1 members were measured across the ADR-025 → ADR-030 gates, and
nothing done later can un-look at them; declaring one of them held out would be
a claim about the past rather than about the experiment. They are development,
permanently. What Phase 6 could choose is where each *new* scenario goes, and
three went to development because the questions Phase 6 must answer cannot be
answered without them — every Phase 1 fault onsets at day 30-40 of 84, so an
anchor rule has nothing to be selected against until the library contains an
early fault and a late one.

**What the boundary does and does not cover.** It separates the held-out
scenarios from the *diagnosis method* — the anchor rule, the per-candidate
statistic, the level. It does not separate them from the evaluator's existing
simulator checks: an L11 expectation states what a scenario's own physics
should show through the reference queries, which are a fixed instrument that
predates the method and cannot be tuned by it. Building a held-out scenario and
confirming that its declared fault actually fired is scenario QA. Running the
engine on it and changing anything afterwards is not, and is what
`HELD_OUT_SCENARIOS` exists to make visible in a diff.

`assert_disjoint` is the whole enforcement, and it is deliberately blunt: a
seed or a scenario that appears in two roles is a bug in the experiment, not a
judgement call.

**This module does not make anything independent.** It records what was used
for what, so that a reader can check the claim rather than trust it, and so
that the next gate inherits the split instead of inventing one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "CALIBRATION_SEEDS",
    "DEVELOPMENT_SCENARIOS",
    "DEVELOPMENT_SEEDS",
    "HELD_OUT_SCENARIOS",
    "HELD_OUT_SEEDS",
    "LIBRARY",
    "MINIMUM_LIBRARY_FOR_A_CLAIM",
    "PHASE_1_SCENARIOS",
    "Population",
    "assert_disjoint",
    "claimable",
    "role_of",
]

#: The five Phase 1 members. Development by history: the ADR-025 to ADR-030
#: gates each measured them, so no later declaration can make them held out.
PHASE_1_SCENARIOS: tuple[str, ...] = (
    "null_baseline",
    "chamber_edge_uniformity",
    "parameter_drift",
    "confounded_chamber_vs_product",
    "fault_repair_recovery",
)

#: Scenarios a method may be selected on. The three beyond Phase 1 are here
#: because the open decisions need them: `early_particle_excursion` (onset at
#: day 12) and `late_gas_flow_step` (onset at day 63) are the only members that
#: can distinguish one anchor rule from another, and `tool_wide_drift` is the
#: only one whose correct answer is a tool rather than a chamber.
DEVELOPMENT_SCENARIOS: tuple[str, ...] = PHASE_1_SCENARIOS + (
    "early_particle_excursion",
    "late_gas_flow_step",
    "tool_wide_drift",
)

#: Scenarios reserved for scoring a frozen method, once. Nothing in `src/` may
#: be changed after these have been read.
#:
#: Each carries a property the development set does not, so the held-out score
#: is a test of generalization rather than a second draw of the same
#: experiment: `benign_correlate` is fault-free but has something to point at,
#: `multi_fault` plants two entities at two onsets, `confounded_late_drift`
#: confounds a parametric drift with a routing window that opens *before* it,
#: and `intermittent_particle_load` is the only duty-cycled fault and the only
#: fault on a polish tool.
HELD_OUT_SCENARIOS: tuple[str, ...] = (
    "benign_correlate",
    "confounded_late_drift",
    "intermittent_particle_load",
    "multi_fault",
)

#: Every scenario the library holds, in a fixed order.
LIBRARY: tuple[str, ...] = tuple(
    sorted(DEVELOPMENT_SCENARIOS + HELD_OUT_SCENARIOS))

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


def _assert_roles_disjoint(axis: str, roles: Mapping[str, Sequence[Any]]
                           ) -> None:
    names = list(roles)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            overlap = set(roles[first]) & set(roles[second])
            if overlap:
                raise AssertionError(
                    f"{axis} {sorted(overlap)} are used both as {first} and as "
                    f"{second}; a population that overlaps its own control "
                    f"cannot support a claim")


def assert_disjoint() -> None:
    """The one enforcement, on both axes.

    A seed in two roles is a broken experiment, and so is a scenario in two
    roles — the second is the one Phase 6 added, because a seed split alone
    lets a method be selected on the very configuration it is then scored on.
    """
    _assert_roles_disjoint("seeds", {
        "development": DEVELOPMENT_SEEDS,
        "held-out": HELD_OUT_SEEDS,
        "calibration": CALIBRATION_SEEDS,
    })
    _assert_roles_disjoint("scenarios", {
        "development": DEVELOPMENT_SCENARIOS,
        "held-out": HELD_OUT_SCENARIOS,
    })


def role_of(scenario: str) -> str:
    """Which population a scenario belongs to. Raises on an unknown slug,
    because a scenario with no declared role is one whose results nobody can
    interpret."""
    if scenario in DEVELOPMENT_SCENARIOS:
        return "development"
    if scenario in HELD_OUT_SCENARIOS:
        return "held-out"
    raise KeyError(
        f"scenario {scenario!r} has no declared population role; add it to "
        f"DEVELOPMENT_SCENARIOS or HELD_OUT_SCENARIOS before measuring on it")


def claimable(scenarios: Sequence[str]) -> tuple[bool, str]:
    """May a number measured on these scenarios be called a capability?

    Returns the verdict and the sentence that has to accompany the number when
    the verdict is no — because the failure mode this guards against is not a
    wrong number, it is a correct number quoted without its population.

    Two conditions, not one. `EXPANSION_ROADMAP` Phase 6 and ADR-029 §5 both
    word the requirement as ">= 10 scenarios **with a declared development /
    held-out split**", and the second half is the half that does the work: ten
    scenarios all of which chose the method are ten scenarios the method is
    fitted to. So a set that reaches the count but contains no held-out member
    is still a measurement, and says so.
    """
    if len(scenarios) < MINIMUM_LIBRARY_FOR_A_CLAIM:
        return False, (
            f"measured on {len(scenarios)} scenario(s); a capability claim "
            f"needs at least {MINIMUM_LIBRARY_FOR_A_CLAIM} "
            f"(EXPANSION_ROADMAP Phase 6), so this is a measurement on a named "
            f"population and not a claim about the system")
    held_out = sorted(set(scenarios) & set(HELD_OUT_SCENARIOS))
    if not held_out:
        return False, (
            f"measured on {len(scenarios)} scenario(s), none of them held out; "
            f"the count is met and the split is not, so this is a measurement "
            f"on the population that chose the method and not a claim about "
            f"the system")
    return True, ""
