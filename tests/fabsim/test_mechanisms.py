"""
Contract tests for `fabsim.mechanisms`.

Two things are under test. The registry resolves a scenario's declared
mechanisms against a world, rejecting what a world cannot satisfy — the mirror
of `test_routing.py`. And the mechanisms themselves are *shaped* the way the
design says: a profile envelope that is zero before onset, a drive that is a
level for the wander latents and a rate for the accumulating one, and — the
load-bearing one — an interface through which no mechanism can learn which
entity it is acting on.

What a drive series then does to a latent is `test_latent.py`.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from fabsim.latent import LatentGrid
from fabsim.mechanisms import (
    MECHANISM_KINDS,
    MECHANISM_NAMES,
    MECHANISMS,
    PROFILE_ENVELOPES,
    MechanismContext,
    OffsetMechanism,
    TrajectoryMechanism,
    mechanism,
    profile_envelope,
    resolve_distractors,
    resolve_events,
)
from fabsim.scenario import ScenarioConfigError, from_mapping

SCENARIO: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "demo",
    "world": "baseline_fab_v1",
    "horizon_days": 84,
    "lots": 20,
    "default_seed": 42,
}

EVENT: dict[str, Any] = {
    "mechanism": "chamber_edge_uniformity",
    "target": {"tool": "ETCH-02", "chamber": "B"},
    "onset_day": 35,
    "profile": {"type": "ramp", "ramp_days": 7},
    "severity": "moderate",
    "response": {"alarm": True, "repair_delay_days_mean": 4.0,
                 "recovery": "partial"},
}

DISTRACTOR: dict[str, Any] = {
    "mechanism": "benign_offset",
    "target": {"tool": "CVD-01"},
    "magnitude": "small",
}


def event(**overrides: Any) -> dict[str, Any]:
    raw = dict(EVENT)
    raw.update(overrides)
    return raw


def distractor(**overrides: Any) -> dict[str, Any]:
    raw = dict(DISTRACTOR)
    raw.update(overrides)
    return raw


def canonical_events(*events: dict[str, Any]) -> list[dict[str, Any]]:
    """Push events through the scenario loader, as a build would."""
    return from_mapping({**SCENARIO, "events": list(events)}).events


def context(grid: LatentGrid, *, onset_day: float = 10.0,
            profile: dict[str, Any] | None = None, magnitude: float = 1.0,
            defaults: dict[str, Any] | None = None) -> MechanismContext:
    import random

    return MechanismContext(
        grid=grid,
        onset_index=grid.index_at(onset_day * 24 * 60),
        profile=profile or {"type": "step"},
        magnitude=magnitude,
        defaults=defaults or {},
        profile_defaults={"intermittent_period_days": 3.0,
                          "intermittent_duty": 0.45},
        rng=random.Random(0),
    )


@pytest.fixture(scope="module")
def grid() -> LatentGrid:
    return LatentGrid.for_horizon(84 * 24 * 60)


# ----------------------------------------------------------------- the library


def test_the_library_is_the_phase_one_set(world):
    """`CAUSAL_MECHANISM_MODEL.md` §9, and nothing invented alongside it."""
    assert MECHANISM_NAMES == ("benign_offset", "chamber_edge_uniformity",
                               "param_drift", "particle_excursion")
    assert {m.kind for m in MECHANISMS.values()} <= set(MECHANISM_KINDS)


def test_each_mechanism_drives_the_latent_the_design_assigns_it():
    assert mechanism("chamber_edge_uniformity").latent == "edge_uniformity"
    assert mechanism("param_drift").latent == "param_bias"
    assert mechanism("particle_excursion").latent == "particle_load"
    assert mechanism("benign_offset").latent is None


def test_faults_are_trajectories_and_the_distractor_is_not():
    for name in ("chamber_edge_uniformity", "param_drift",
                 "particle_excursion"):
        assert isinstance(mechanism(name), TrajectoryMechanism)
    assert isinstance(mechanism("benign_offset"), OffsetMechanism)


def test_mechanism_vocabularies_agree():
    """The world validates mechanism constants without importing the library
    (which would be a cycle); this keeps the two copies from drifting."""
    from fabsim.world import MECHANISM_DEFAULT_KEYS

    assert MECHANISM_DEFAULT_KEYS == MECHANISM_NAMES


def test_profile_vocabularies_agree():
    from fabsim.scenario import PROFILE_TYPES

    assert tuple(sorted(PROFILE_ENVELOPES)) == tuple(sorted(PROFILE_TYPES))


def test_every_registered_mechanism_has_world_constants(world):
    for name in MECHANISM_NAMES:
        assert isinstance(world.mechanism_policy.defaults_for(name), dict)


# ------------------------------------------------------------ the envelope


def test_an_activation_is_silent_before_its_onset(grid):
    envelope = profile_envelope(context(grid, onset_day=35.0))
    onset = grid.index_at(35.0 * 24 * 60)
    assert set(envelope[:onset]) == {0.0}
    assert envelope[onset] == 1.0


def test_a_step_profile_arrives_at_once_and_stays(grid):
    envelope = profile_envelope(context(grid, onset_day=20.0,
                                        profile={"type": "step"}))
    onset = grid.index_at(20.0 * 24 * 60)
    assert set(envelope[onset:]) == {1.0}


def test_a_ramp_profile_climbs_over_its_ramp_days_and_then_holds(grid):
    envelope = profile_envelope(
        context(grid, onset_day=20.0,
                profile={"type": "ramp", "ramp_days": 7.0}))
    onset = grid.index_at(20.0 * 24 * 60)
    ramped = grid.index_at(27.0 * 24 * 60)

    climb = envelope[onset:ramped]
    assert climb == tuple(sorted(climb))          # monotone
    assert 0.0 < climb[0] < 0.05                  # starts small
    assert envelope[ramped - 1] == pytest.approx(1.0, abs=1e-9)
    assert set(envelope[ramped:]) == {1.0}        # sustained afterwards


def test_a_longer_ramp_is_slower_at_every_point(grid):
    short = profile_envelope(context(
        grid, onset_day=20.0, profile={"type": "ramp", "ramp_days": 3.0}))
    long = profile_envelope(context(
        grid, onset_day=20.0, profile={"type": "ramp", "ramp_days": 14.0}))
    assert all(a >= b for a, b in zip(short, long))
    assert any(a > b for a, b in zip(short, long))


def test_an_intermittent_profile_switches_and_is_not_always_on(grid):
    envelope = profile_envelope(
        context(grid, onset_day=20.0, profile={"type": "intermittent"}))
    after = envelope[grid.index_at(20.0 * 24 * 60):]
    assert set(after) == {0.0, 1.0}
    duty = sum(after) / len(after)
    assert 0.3 < duty < 0.6                       # near the configured 0.45


# -------------------------------------------------------------- the drives


@pytest.mark.parametrize("name", ["chamber_edge_uniformity", "param_drift"])
def test_a_wander_mechanism_drives_a_level(grid, name):
    drive = mechanism(name).contribute(
        context(grid, onset_day=20.0, magnitude=0.05))
    onset = grid.index_at(20.0 * 24 * 60)
    assert set(drive[:onset]) == {0.0}
    assert drive[-1] == pytest.approx(0.05)
    assert max(drive) == pytest.approx(0.05)


def test_the_particle_mechanism_drives_a_rate_not_a_level(grid):
    defaults = {"step_fraction": 0.35, "escalation_days": 6.0}
    drive = mechanism("particle_excursion").contribute(
        context(grid, onset_day=20.0, magnitude=0.05, defaults=defaults))
    onset = grid.index_at(20.0 * 24 * 60)
    assert set(drive[:onset]) == {0.0}
    # The sustained term is a per-day rate that clears the magnitude over the
    # escalation window; the onset point additionally carries the step.
    assert drive[-1] == pytest.approx(0.05 * 0.65 / 6.0)
    assert drive[onset] > drive[onset + 1]


def test_a_bigger_magnitude_drives_proportionally_harder(grid):
    small = mechanism("param_drift").contribute(
        context(grid, onset_day=10.0, magnitude=0.01))
    large = mechanism("param_drift").contribute(
        context(grid, onset_day=10.0, magnitude=0.04))
    assert max(large) == pytest.approx(4.0 * max(small))


def test_the_distractor_scales_with_its_declared_magnitude(world):
    defaults = world.mechanism_policy.defaults_for("benign_offset")
    offset = mechanism("benign_offset")
    sigmas = [offset.offset_sigma(level, defaults)
              for level in ("small", "moderate", "large")]
    assert sigmas == sorted(sigmas)
    assert len(set(sigmas)) == 3


# ---------------------------------------------------------------- resolution


def test_a_chamber_target_resolves_to_one_chamber(world):
    (resolved,) = resolve_events(world, canonical_events(event()))
    chamber = world.chamber_by_name("ETCH-02", "B")
    assert resolved.chamber_ids == (chamber.chamber_id,)
    assert resolved.latent == "edge_uniformity"
    assert resolved.tool_name == "ETCH-02"


def test_a_tool_target_resolves_to_every_chamber_of_the_tool(world):
    (resolved,) = resolve_events(
        world, canonical_events(event(target={"tool": "ETCH-02"})))
    tool = world.tool_by_name("ETCH-02")
    assert resolved.chamber_ids == tool.chamber_ids
    assert len(resolved.chamber_ids) == 3
    assert resolved.chamber_name is None


def test_events_keep_their_declared_order(world):
    resolved = resolve_events(world, canonical_events(
        event(), event(mechanism="param_drift",
                       target={"tool": "ETCH-01"}, onset_day=50)))
    assert [r.mechanism for r in resolved] == ["chamber_edge_uniformity",
                                               "param_drift"]
    assert [r.index for r in resolved] == [0, 1]


def test_no_events_resolve_to_nothing(world):
    assert resolve_events(world) == ()
    assert resolve_events(world, []) == ()


@pytest.mark.parametrize("overrides, path, message", [
    ({"mechanism": "chamber_meltdown"}, "events[0].mechanism",
     "unknown mechanism"),
    ({"target": {"tool": "ETCH-99"}}, "events[0].target.tool", "unknown tool"),
    ({"target": {"tool": "ETCH-02", "chamber": "Z"}},
     "events[0].target.chamber", "no chamber"),
    ({"target": {"tool": "ETCH-01", "chamber": "C"}},
     "events[0].target.chamber", "no chamber"),
    ({"mechanism": "benign_offset"}, "events[0].mechanism",
     "belongs in `distractors`"),
])
def test_an_event_the_world_cannot_satisfy_is_rejected(world, overrides, path,
                                                       message):
    with pytest.raises(ScenarioConfigError) as excinfo:
        resolve_events(world, canonical_events(event(**overrides)))
    assert excinfo.value.path == path
    assert message in str(excinfo.value)


def test_the_rejection_names_the_offending_event(world):
    with pytest.raises(ScenarioConfigError) as excinfo:
        resolve_events(world, canonical_events(
            event(), event(target={"tool": "ETCH-99"})))
    assert excinfo.value.path == "events[1].target.tool"


def test_a_distractor_resolves_and_a_fault_in_its_place_does_not(world):
    config = from_mapping({**SCENARIO, "distractors": [distractor()]})
    (resolved,) = resolve_distractors(world, config.distractors)
    assert resolved.mechanism == "benign_offset"
    assert resolved.chamber_ids == world.tool_by_name("CVD-01").chamber_ids

    faulted = from_mapping({**SCENARIO, "distractors": [
        distractor(mechanism="param_drift")]})
    with pytest.raises(ScenarioConfigError) as excinfo:
        resolve_distractors(world, faulted.distractors)
    assert excinfo.value.path == "distractors[0].mechanism"
    assert "belongs in `events`" in str(excinfo.value)


def test_a_mechanism_whose_latent_the_world_lacks_is_rejected(make_template):
    """A world that declares no `particle_load` cannot host a particle
    excursion, and saying so beats a fault that silently never happened."""
    from fabsim.world import build_world

    raw = make_template()
    dropped = "particle_load"
    raw["observation"]["latents"] = [name for name
                                     in raw["observation"]["latents"]
                                     if name != dropped]
    del raw["latents"][dropped]
    for channel in raw["observation"]["channels"]:
        channel["sensitivities"].pop(dropped, None)
    raw["alarms"]["codes"] = [code for code in raw["alarms"]["codes"]
                              if code["signal"] != dropped]
    thinner = build_world(raw)
    assert dropped not in thinner.observation.latents

    events = canonical_events(event(mechanism="particle_excursion",
                                    target={"tool": "CVD-01"}))
    with pytest.raises(ScenarioConfigError) as excinfo:
        resolve_events(thinner, events)
    assert excinfo.value.path == "events[0].mechanism"
    assert "does not declare" in str(excinfo.value)


# ---------------------------------------------------------- no named targets


def _code_strings(module: Path) -> list[str]:
    """String literals in a module, excluding docstrings (and comments)."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str) and id(node) not in docstrings]


def test_no_mechanism_names_a_tool_or_a_chamber():
    """Rule D6 over the library: prose may cite ETCH-02, code may not."""
    package = Path(__file__).resolve().parents[2] / "src" / "fabsim"
    pattern = re.compile(
        r"(ETCH|CVD|LITHO|CMP|PVD|FURN|IMP|MET|INSP|TEST)-\d")
    for module in sorted((package / "mechanisms").glob("*.py")) + [
            package / "latent.py"]:
        hits = [text for text in _code_strings(module)
                if pattern.search(text)]
        assert hits == [], module


def test_a_mechanism_is_never_told_which_chamber_it_acts_on():
    """The strongest form of the rule: the entity is not in scope.

    `contribute` receives a `MechanismContext` and nothing else, and the
    context carries no chamber, no tool, no world and no timeline — so
    entity-specific behaviour is not merely forbidden, it is unwritable.
    """
    fields = set(MechanismContext.__dataclass_fields__)
    assert fields == {"grid", "onset_index", "profile", "magnitude",
                      "defaults", "profile_defaults", "rng"}
    forbidden = {"chamber", "chamber_id", "tool", "tool_id", "world",
                 "timeline", "realization", "latent_state", "yield"}
    assert not (fields & forbidden)

    for name in ("chamber_edge_uniformity", "param_drift",
                 "particle_excursion"):
        signature = inspect.signature(type(mechanism(name)).contribute)
        assert list(signature.parameters) == ["self", "context"]
