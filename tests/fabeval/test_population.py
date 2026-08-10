"""
The population split, and the guard that stops a measurement being quoted as a
capability.

`fabeval.population` records what was used for what. It cannot make anything
independent; what it can do is make a violation visible in a diff, and these
are the assertions that do the looking.

Phase 6 added the second axis. A seed split alone cannot protect a method that
was chosen while looking at a *scenario*, so the split now runs on scenarios
too and both are checked here — including the direction that matters most,
which is that nothing outside this module and the configs themselves names a
held-out scenario.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import fabeval.population as population
from fabeval.population import (CALIBRATION_SEEDS, DEVELOPMENT_SCENARIOS,
                                DEVELOPMENT_SEEDS, HELD_OUT_SCENARIOS,
                                HELD_OUT_SEEDS, LIBRARY,
                                MINIMUM_LIBRARY_FOR_A_CLAIM,
                                PHASE_1_SCENARIOS, Population, assert_disjoint,
                                claimable, role_of)

REPO = Path(__file__).resolve().parents[2]


def test_every_role_is_disjoint_on_both_axes():
    assert_disjoint()


def test_a_seed_in_two_roles_is_caught(monkeypatch):
    """The mutation. A guard nobody has seen fire proves nothing."""
    monkeypatch.setattr(population, "HELD_OUT_SEEDS",
                        population.HELD_OUT_SEEDS + (DEVELOPMENT_SEEDS[0],))
    with pytest.raises(AssertionError, match="cannot support a claim"):
        population.assert_disjoint()


def test_a_scenario_in_two_roles_is_caught(monkeypatch):
    """The axis Phase 6 added, mutated the same way."""
    monkeypatch.setattr(
        population, "HELD_OUT_SCENARIOS",
        population.HELD_OUT_SCENARIOS + (DEVELOPMENT_SCENARIOS[0],))
    with pytest.raises(AssertionError, match="scenarios .* cannot support"):
        population.assert_disjoint()


def test_the_held_out_seeds_are_not_the_published_ones():
    """The library publishes its default seed; scoring on it would be scoring
    on the seed every design gate has already looked at."""
    assert 42 not in HELD_OUT_SEEDS
    assert set(DEVELOPMENT_SEEDS).isdisjoint(HELD_OUT_SEEDS)


def test_the_calibration_population_is_large_enough_to_be_a_rate():
    assert len(CALIBRATION_SEEDS) >= 100


def test_the_phase_1_five_are_permanently_development():
    """Settled by history rather than by preference.

    ADR-025 through ADR-030 each measured these five. Moving one into the
    held-out set would be a claim about the past, and this is the assertion
    that refuses it.
    """
    assert set(PHASE_1_SCENARIOS) <= set(DEVELOPMENT_SCENARIOS)
    assert set(PHASE_1_SCENARIOS).isdisjoint(HELD_OUT_SCENARIOS)


def test_the_declaration_and_the_configs_on_disk_agree():
    """A scenario file with no declared role is one nobody can interpret, and
    a declared scenario with no file is a population that cannot be built."""
    on_disk = {path.stem for path in (REPO / "scenarios").glob("*.json")}
    assert on_disk == set(LIBRARY), (
        f"only on disk: {sorted(on_disk - set(LIBRARY))}; "
        f"only declared: {sorted(set(LIBRARY) - on_disk)}")
    for scenario in LIBRARY:
        assert role_of(scenario) in ("development", "held-out")


def test_an_undeclared_scenario_has_no_role():
    with pytest.raises(KeyError, match="no declared population role"):
        role_of("something_nobody_declared")


def test_the_library_now_supports_a_capability_claim():
    """Deliberately changed at the Phase 6 gate.

    Its predecessor asserted the opposite and said in its own docstring that
    "when the library reaches the roadmap's threshold this test fails, and the
    right response is to change it deliberately rather than to discover that
    someone has been quoting a capability for a while." This is that
    deliberate change: twelve scenarios with a declared split, which is what
    `EXPANSION_ROADMAP` Phase 6 and ADR-029 §5 both ask for.
    """
    assert len(LIBRARY) >= MINIMUM_LIBRARY_FOR_A_CLAIM
    allowed, reason = claimable(LIBRARY)
    assert allowed and reason == ""


def test_the_count_alone_does_not_support_a_claim():
    """The half that does the work.

    Ten scenarios all of which chose the method are ten scenarios the method is
    fitted to, so `claimable` requires the split as well as the count — and the
    development set, which is large enough on its own terms, must still be
    refused.
    """
    invented = tuple(f"scenario-{i}" for i in
                     range(MINIMUM_LIBRARY_FOR_A_CLAIM))
    allowed, reason = claimable(invented)
    assert not allowed
    assert "none of them held out" in reason

    allowed, reason = claimable(DEVELOPMENT_SCENARIOS)
    assert not allowed


def test_the_held_out_scenarios_are_named_nowhere_that_could_tune_a_method():
    """The direction that actually protects the split.

    A held-out slug may appear in its own configuration, in the maintainers'
    index, in the module that declares the split, and in the L11 expectation
    table. Anywhere else in `src/` — a threshold, a default, a special case in
    the engine or the harness — means something was written while looking at
    data reserved for scoring.

    `fabeval.fixtures` is allowed for the reason `population`'s own docstring
    gives: the boundary is between the held-out scenarios and the diagnosis
    *method*, not between them and the evaluator's simulator checks. An L11
    expectation states what a scenario's physics should show through the
    reference queries, which are a fixed instrument predating the method and
    unable to be tuned by it — and `ANTI_LEAKAGE_DESIGN.md` process rule 1
    requires every new scenario to ship with one.
    """
    allowed = {
        (REPO / "src" / "fabeval" / "population.py").resolve(),
        (REPO / "src" / "fabeval" / "fixtures.py").resolve(),
    }
    offenders: list[str] = []
    for path in sorted((REPO / "src").rglob("*.py")):
        if path.resolve() in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for scenario in HELD_OUT_SCENARIOS:
            if scenario in text:
                offenders.append(f"{path.relative_to(REPO).as_posix()}: "
                                 f"{scenario}")
    assert not offenders, (
        "held-out scenario names reached code that could be tuned against "
        "them:\n  " + "\n  ".join(offenders))


def test_a_population_describes_itself():
    described = Population(name="the twelve-scenario library",
                           scenarios=LIBRARY, seeds=DEVELOPMENT_SEEDS,
                           role="development")
    assert described.size == len(LIBRARY) * len(DEVELOPMENT_SEEDS)
    assert "development" in described.describe()
