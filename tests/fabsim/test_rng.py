"""
Contract tests for `fabsim.rng`.

The properties under test are the ones acceptance criterion A1 rests on:
a substream is a pure function of (master seed, key), streams are mutually
independent, and adding a stream never disturbs the streams that already
existed. Everything else about randomness is the simulation's business, not
this module's.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys

import pytest

from fabsim.rng import (
    DOMAIN,
    MASTER_SEED_MAX,
    MASTER_SEED_MIN,
    Substreams,
    stream,
    substream_seed,
    validate_master_seed,
)

SEED = 20260808


def draws(rng, n: int = 8) -> list[float]:
    return [rng.random() for _ in range(n)]


# --------------------------------------------------------------- determinism


def test_same_seed_same_stream_gives_the_same_sequence():
    assert draws(stream(SEED, "defects", 233, 4)) == draws(
        stream(SEED, "defects", 233, 4))


def test_bound_and_free_forms_agree():
    rngs = Substreams(SEED)
    assert rngs.seed_for("routing", 7) == substream_seed(SEED, "routing", 7)
    assert draws(rngs.stream("routing", 7)) == draws(
        stream(SEED, "routing", 7))


def test_each_call_returns_an_independent_generator():
    """Streams are addressed by name, never shared by reference."""
    first, second = stream(SEED, "yield"), stream(SEED, "yield")
    assert first is not second
    first.random()  # advancing one must not advance the other
    assert draws(second) == draws(stream(SEED, "yield"))


def test_derivation_is_pinned():
    """The derivation is a versioned contract: changing it changes every
    dataset ever generated, so it may only move with the domain tag."""
    assert DOMAIN == "fabsim.rng/v1"
    assert format(substream_seed(42, "routing", 1), "064x") == (
        "186adbfc2e73640166dcb1f953a6212e39eb2f682b2eb83e9ea8095abda57d08")
    assert [round(v, 12) for v in draws(stream(42, "routing", 1), 3)] == [
        0.222067221519, 0.400869674745, 0.595602581587]


# -------------------------------------------------------------- independence


@pytest.mark.parametrize("key_a, key_b", [
    (("defects", 1), ("routing", 1)),          # different subsystem
    (("defects", 1), ("defects", 2)),          # different entity
    (("defects", 1), ("defects", 1, 0)),       # different key length
    (("a", "b"), ("ab",)),                     # no delimiter ambiguity
    (("a1", 2), ("a", "12")),                  # no concatenation ambiguity
    (("1",), (1,)),                            # string 1 is not integer 1
])
def test_different_keys_are_different_streams(key_a, key_b):
    assert substream_seed(SEED, *key_a) != substream_seed(SEED, *key_b)
    assert draws(stream(SEED, *key_a)) != draws(stream(SEED, *key_b))


def test_different_seeds_are_different_streams():
    assert draws(stream(SEED, "defects", 1)) != draws(
        stream(SEED + 1, "defects", 1))


def test_adding_an_unrelated_stream_does_not_reshuffle_existing_streams():
    """The stability property §6 asks for by name: a new subsystem must not
    move the draws of a subsystem that already existed."""
    existing = ("routing", "defects", "yield")
    before = {key: draws(stream(SEED, key, 12)) for key in existing}

    # A later slice introduces new streams and consumes them heavily.
    for key in ("metrology", "alarms", "maintenance"):
        newcomer = stream(SEED, key, 12)
        draws(newcomer, 1000)

    after = {key: draws(stream(SEED, key, 12)) for key in existing}
    assert after == before


def test_module_never_touches_the_global_generator():
    random.seed(0)
    expected = random.random()

    draws(stream(SEED, "defects", 1), 100)
    Substreams(SEED).stream("routing", 1).random()

    random.seed(0)
    assert random.random() == expected


# ------------------------------------------------------- process determinism


_RNG_PROBE = """
from fabsim.rng import stream, substream_seed
for name in ("routing", "defects", "yield", "metrology"):
    print(name, format(substream_seed(20260808, name, 7), "064x"),
          [stream(20260808, name, 7).random() for _ in range(4)])
"""


def _run_probe(script: str, *, cwd, hash_seed: str, extra_env=None) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    env.update(extra_env or {})
    result = subprocess.run([sys.executable, "-c", script], cwd=str(cwd),
                            env=env, capture_output=True, text=True,
                            check=True)
    return result.stdout


def test_repeated_process_execution_gives_the_same_result(tmp_path):
    """Fresh interpreters, different hash salts, different working
    directories — the streams must not notice."""
    first_dir = tmp_path / "run-a"
    second_dir = tmp_path / "run-b"
    first_dir.mkdir()
    second_dir.mkdir()

    first = _run_probe(_RNG_PROBE, cwd=first_dir, hash_seed="0")
    second = _run_probe(_RNG_PROBE, cwd=second_dir, hash_seed="12345",
                        extra_env={"FABSIM_UNRELATED": "value"})

    assert first == second
    assert first.strip()


# ----------------------------------------------------------- input rejection


@pytest.mark.parametrize("bad_seed", [4.2, "42", None, True, False, b"42",
                                      42j])
def test_non_integer_master_seed_is_a_type_error(bad_seed):
    with pytest.raises(TypeError):
        validate_master_seed(bad_seed)
    with pytest.raises(TypeError):
        substream_seed(bad_seed, "defects")
    with pytest.raises(TypeError):
        Substreams(bad_seed)


@pytest.mark.parametrize("bad_seed", [MASTER_SEED_MIN - 1, -42,
                                      MASTER_SEED_MAX + 1])
def test_out_of_range_master_seed_is_a_value_error(bad_seed):
    with pytest.raises(ValueError):
        validate_master_seed(bad_seed)
    with pytest.raises(ValueError):
        substream_seed(bad_seed, "defects")


@pytest.mark.parametrize("seed", [MASTER_SEED_MIN, 42, MASTER_SEED_MAX])
def test_range_boundaries_are_accepted(seed):
    assert validate_master_seed(seed) == seed
    assert 0 <= substream_seed(seed, "defects") < 2 ** 256


def test_empty_key_is_rejected():
    with pytest.raises(ValueError):
        substream_seed(SEED)


@pytest.mark.parametrize("bad_part", [4.2, None, True, b"defects",
                                      ("defects",), ["defects"]])
def test_unsupported_key_parts_are_rejected(bad_part):
    with pytest.raises(TypeError):
        substream_seed(SEED, "defects", bad_part)


def test_rejection_is_itself_deterministic():
    """Same invalid input, same failure — twice, and in that order."""
    messages = []
    for _ in range(2):
        with pytest.raises(ValueError) as excinfo:
            substream_seed(-1, "defects")
        messages.append(str(excinfo.value))
    assert messages[0] == messages[1]
