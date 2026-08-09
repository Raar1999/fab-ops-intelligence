"""
The version-discipline tripwire: what the generator produces, bound to the
version it claims to be.

`FABSIM_DESIGN.md` §7 states the rule — *any change that can alter emitted
bytes for a fixed (config, seed) bumps at least the minor version* — and until
this file existed, nothing enforced it. It was broken once, and the breach is
worth stating precisely because it is the exact failure the build fingerprint
exists to prevent:

    tree                                     version   fingerprint   content
    188bf43  Step 3D                          0.5.0     068b95ca…     6bcc9296…
    02aed8d  the ADR-020 recovery correction  0.5.0     068b95ca…     5b183b63…

Two semantically different generators, one identity. No dataset had been
emitted from either, so no artifact ever carried the false claim — but the
mechanism that allowed it was still in place, because a version number is a
string somebody has to remember to change and nothing was watching.

Now something is. `REFERENCE_BUILDS` maps a generator version to the digest of
what that generator produces on a fixed reference build, across every plane.
Changing generation without changing the version means overwriting an entry
keyed by a version number, which is a thing a reviewer can see; changing the
version without changing generation is free and harmless. The table is
append-only by review, and the digests are *not* a substitute for the physical
invariant tests in the other modules — those say the physics is right, this
says the physics has not moved without anybody noticing.

This is the input-side companion to acceptance criterion A1: A1's content-hash
oracle compares two runs of one build, and this compares one build against the
version it declares itself to be.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest

from fabsim import SCHEMA_VERSION, __version__
from fabsim.defects import inspect_response
from fabsim.die import probe_response
from fabsim.observation import observe_response
from fabsim.response import respond_scenario
from fabsim.scenario import from_mapping
from fabsim.world import load_world

BASELINE_WORLD = "baseline_fab_v1"

#: The reference build. Deliberately small — this is a tripwire, not a
#: benchmark — but wide enough to exercise every plane: lots are released,
#: wafers are routed, maintenance happens, latents evolve and recover, alarms
#: fire, measurements and defects are produced, and wafers reach the tester.
#: Its shape is part of the digest, so it may not be changed without a new
#: entry either.
REFERENCE: dict[str, Any] = {
    "fabsim": "scenario/v1",
    "name": "reference",
    "world": BASELINE_WORLD,
    "horizon_days": 30,
    "lots": 6,
    "default_seed": 42,
}

#: The reference build's faulted twin. A null-only reference would miss a
#: change confined to mechanism arithmetic, which is most of what the physics
#: slices contain.
REFERENCE_EVENT: dict[str, Any] = {
    "mechanism": "chamber_edge_uniformity",
    "target": {"tool": "ETCH-02", "chamber": "B"},
    "onset_day": 10,
    "profile": {"type": "ramp", "ramp_days": 5},
    "severity": "obvious",
}

#: generator version → digest of the reference build under that generator.
#:
#: **A new entry is mandatory whenever generation changes**, and the key is
#: the version, so recording a new digest means naming the version it belongs
#: to. That coupling is the whole point of the table.
#:
#: `0.5.0` is deliberately absent. It named two different generators — Step 3D
#: and the ADR-020 recovery correction that followed it — and there is no
#: single digest that would be honest about it. The gap is the record of the
#: breach this file exists to prevent (ADR-022).
REFERENCE_BUILDS: dict[str, str] = {
    "0.6.0": "36265211073d2365b9801dee85d597d0e4e8f6f41a0bb16d993abbcf29f2ae68",
}


def _digest() -> str:
    """One digest over every plane of the reference build, null and faulted.

    Every plane, because a change anywhere in the chain changes what a dataset
    would contain: a different latent trajectory is a different measurement is
    a different defect map is a different yield. The world digest and the
    schema version are folded in as well, so a template edit or a schema bump
    lands here too rather than only in the build fingerprint.
    """
    world = load_world(BASELINE_WORLD)
    digest = hashlib.sha256()
    digest.update(f"{SCHEMA_VERSION}\t{world.world_sha256}\n".encode("ascii"))

    for events in ((), (REFERENCE_EVENT,)):
        config = from_mapping({**REFERENCE, "events": list(events)})
        response = respond_scenario(config, world=world)
        observations = observe_response(response)
        defects = inspect_response(response)
        die = probe_response(response, observations, defects)
        identity = config.dataset_identity(world_sha256=world.world_sha256)
        for part in (identity.build_fingerprint,
                     response.realization.content_sha256(),
                     response.content_sha256(),
                     observations.content_sha256(),
                     defects.content_sha256(),
                     die.content_sha256()):
            digest.update(part.encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()


def test_this_version_has_a_recorded_reference_build():
    """A version nobody has measured cannot claim to identify a generator."""
    assert __version__ in REFERENCE_BUILDS, (
        f"fabsim {__version__} has no entry in REFERENCE_BUILDS. If generation "
        "changed, add one; if it did not, the version bump is fine but the "
        "table still has to say so."
    )


def test_the_generator_produces_what_its_version_claims():
    """The tripwire. Fails on *any* change to what the simulator generates.

    When it fails, exactly one of two things is true and the author has to say
    which: generation changed on purpose — bump `fabsim.__version__` and add
    the new digest under the new key — or it changed by accident, which is a
    bug in the change, not in this test. Silently overwriting the digest under
    the *current* version reintroduces the 0.5.0 breach and is the one repair
    that is never correct.
    """
    assert _digest() == REFERENCE_BUILDS[__version__], (
        f"the reference build no longer matches fabsim {__version__}"
    )


def test_the_recorded_history_is_not_quietly_dropped():
    """Old entries stay. They are the record of which generator was which."""
    assert set(REFERENCE_BUILDS) >= {"0.6.0"}
    assert "0.5.0" not in REFERENCE_BUILDS      # named two generators; ADR-022
    for version, digest in REFERENCE_BUILDS.items():
        assert len(version.split(".")) == 3, version
        assert len(digest) == 64 and int(digest, 16) >= 0, version
    assert len(set(REFERENCE_BUILDS.values())) == len(REFERENCE_BUILDS)


def test_the_reference_build_exercises_every_plane():
    """A tripwire over a world where nothing happens would catch nothing."""
    world = load_world(BASELINE_WORLD)
    config = from_mapping({**REFERENCE, "events": [REFERENCE_EVENT]})
    response = respond_scenario(config, world=world)
    observations = observe_response(response)
    defects = inspect_response(response)
    die = probe_response(response, observations, defects)

    assert response.timeline.runs and response.timeline.maintenance
    assert response.realization.trajectories
    assert response.realization.mechanisms          # the fault is realized
    assert response.realization.resets              # maintenance moved latents
    assert response.alarms
    assert observations.run_measurements and observations.metrology
    assert defects.inspections and defects.defects
    assert die.die_bins and die.wafer_yield
    assert any(0.0 < y.yield_pct < 100.0 for y in die.wafer_yield)


def test_the_digest_is_stable_within_one_build():
    """It has to be a property of the generator, not of the run."""
    assert _digest() == _digest()


@pytest.mark.parametrize("version", ["0.5.0", "9.9.9", ""])
def test_an_unrecorded_version_is_a_failure_not_a_pass(version):
    """The table must not silently accept a version it has never seen."""
    assert version not in REFERENCE_BUILDS
