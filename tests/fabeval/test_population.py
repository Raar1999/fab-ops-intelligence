"""
The population split, and the guard that stops a measurement being quoted as a
capability.

`fabeval.population` records which seeds were used for what. It cannot make
anything independent; what it can do is make a violation visible in a diff, and
these are the assertions that do the looking.
"""
from __future__ import annotations

import pytest

from fabeval.population import (CALIBRATION_SEEDS, DEVELOPMENT_SEEDS,
                                HELD_OUT_SEEDS, MINIMUM_LIBRARY_FOR_A_CLAIM,
                                Population, assert_disjoint, claimable)
from fabeval.matrix import LIBRARY


def test_the_three_roles_share_no_seed():
    assert_disjoint()


def test_a_seed_in_two_roles_is_caught(monkeypatch):
    """The mutation. A guard nobody has seen fire proves nothing."""
    import fabeval.population as population

    monkeypatch.setattr(population, "HELD_OUT_SEEDS",
                        population.HELD_OUT_SEEDS + (DEVELOPMENT_SEEDS[0],))
    with pytest.raises(AssertionError, match="cannot support a claim"):
        population.assert_disjoint()


def test_the_held_out_seeds_are_not_the_published_ones():
    """The library publishes its default seed; scoring on it would be scoring
    on the seed every design gate has already looked at."""
    assert 42 not in HELD_OUT_SEEDS
    assert set(DEVELOPMENT_SEEDS).isdisjoint(HELD_OUT_SEEDS)


def test_the_calibration_population_is_large_enough_to_be_a_rate():
    assert len(CALIBRATION_SEEDS) >= 100


def test_the_current_library_cannot_support_a_capability_claim():
    """The honest state of the project, asserted so it cannot drift silently.

    When the library reaches the roadmap's threshold this test fails, and the
    right response is to change it deliberately rather than to discover that
    someone has been quoting a capability for a while.
    """
    allowed, reason = claimable(LIBRARY)
    assert not allowed
    assert str(MINIMUM_LIBRARY_FOR_A_CLAIM) in reason
    assert "not a claim about the system" in reason


def test_a_large_enough_library_would_support_one():
    allowed, reason = claimable(tuple(f"scenario-{i}" for i in
                                      range(MINIMUM_LIBRARY_FOR_A_CLAIM)))
    assert allowed and reason == ""


def test_a_population_describes_itself():
    population = Population(name="the five-scenario library",
                            scenarios=LIBRARY, seeds=DEVELOPMENT_SEEDS,
                            role="development")
    assert population.size == len(LIBRARY) * len(DEVELOPMENT_SEEDS)
    assert "development" in population.describe()
