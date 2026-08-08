"""
rng.py — deterministic, named random substreams.

FabSim must produce the same dataset for the same (config, seed, version)
inputs on every machine and in every process (`FABSIM_DESIGN.md` §6). A single
global generator cannot deliver that in practice: any subsystem drawing one
extra number reshuffles every later draw, so adding a term to the yield model
would silently change routing. Instead each subsystem asks for its own
generator by *name*::

    stream(master_seed, "routing", lot_id)
    stream(master_seed, "defects", wafer_id, step_id)

The substream seed is SHA-256 over a domain tag, the master seed and the
key parts, which buys three properties the design asks for by name:

* **reproducible** — the same (master seed, key) always yields the same
  sequence, in this process and in the next one;
* **independent** — different keys yield unrelated sequences;
* **stable under unrelated change** — a new key is a new hash, so introducing
  a stream never perturbs the streams that already existed.

The key encoding is length-prefixed and type-tagged, so no two distinct key
tuples can collide: ``("a", "b")``, ``("ab",)`` and ``("a", 1)`` are three
different streams.

What this module deliberately does not do: read the wall clock, the
environment, the filesystem, or Python's salted ``hash()``; hold mutable
state; or call ``random.seed()``. Importing it cannot perturb anybody else's
randomness, and every derivation is a pure function of its arguments.

Scope note: this is the substream primitive only. Which streams exist, and
what each one draws, belongs to the world/timeline slices that use them.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Union

__all__ = [
    "DOMAIN",
    "KeyPart",
    "MASTER_SEED_MAX",
    "MASTER_SEED_MIN",
    "Substreams",
    "stream",
    "substream_seed",
    "validate_master_seed",
]

#: A substream key is a tuple of these. Strings name the subsystem, integers
#: identify the entity (lot, wafer, run, step) the stream belongs to.
KeyPart = Union[str, int]

#: Domain-separation tag, versioned. It is mixed into every digest so that
#: substream seeds cannot collide with any other hash this project derives,
#: and so that a future change to the derivation is a visible version bump
#: rather than a silent reshuffle of every dataset ever generated.
DOMAIN = "fabsim.rng/v1"

#: Accepted master seeds. Non-negative because the seed is rendered into the
#: dataset id (`scn-<hash>-s042`); bounded because a seed is a build knob, not
#: a payload.
MASTER_SEED_MIN = 0
MASTER_SEED_MAX = 2 ** 64 - 1


def validate_master_seed(seed: object) -> int:
    """Return `seed` if it is a usable master seed, else raise.

    `bool` is rejected explicitly: it is an `int` subclass, and `True` as a
    seed is far more likely to be a mistake than an intention.
    """
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError(
            f"master seed must be an int, got {type(seed).__name__}"
        )
    if not MASTER_SEED_MIN <= seed <= MASTER_SEED_MAX:
        raise ValueError(
            f"master seed must be in [{MASTER_SEED_MIN}, {MASTER_SEED_MAX}], "
            f"got {seed}"
        )
    return seed


def _encode_key(key: tuple[KeyPart, ...]) -> bytes:
    """Encode a key tuple injectively: ``<tag><byte length>:<payload>`` parts.

    The length prefix is what makes the encoding unambiguous — without it
    ``("a", "b")`` and ``("ab",)`` would hash the same.
    """
    if not key:
        raise ValueError(
            "a substream key needs at least one part; unnamed streams are not "
            "allowed because a stream's name is its identity"
        )
    out = bytearray()
    for position, part in enumerate(key):
        if isinstance(part, bool) or not isinstance(part, (str, int)):
            raise TypeError(
                f"substream key part {position} must be str or int, got "
                f"{type(part).__name__}"
            )
        if isinstance(part, int):
            tag, payload = b"i", str(part).encode("ascii")
        else:
            tag, payload = b"s", part.encode("utf-8")
        out += tag + str(len(payload)).encode("ascii") + b":" + payload
    return bytes(out)


def substream_seed(master_seed: int, *key: KeyPart) -> int:
    """Derive the deterministic seed of the substream named by `key`."""
    validated = validate_master_seed(master_seed)
    digest = hashlib.sha256(
        DOMAIN.encode("ascii")
        + b"\x00m"
        + str(validated).encode("ascii")
        + b"\x00k"
        + _encode_key(key)
    ).digest()
    return int.from_bytes(digest, "big")


def stream(master_seed: int, *key: KeyPart) -> random.Random:
    """Return a fresh generator for the substream named by `key`.

    Each call returns a new `random.Random`, so two callers of the same key
    get two independent objects that produce the same sequence — the streams
    are addressed by name, never shared by reference.
    """
    return random.Random(substream_seed(master_seed, *key))


@dataclass(frozen=True)
class Substreams:
    """A master seed bound to the substream derivation.

    Sugar over the module functions for code that threads one seed through
    many subsystems: ``rngs = Substreams(seed); rngs.stream("routing", lot_id)``.
    """

    master_seed: int

    def __post_init__(self) -> None:
        validate_master_seed(self.master_seed)

    def seed_for(self, *key: KeyPart) -> int:
        """The deterministic seed of the substream named by `key`."""
        return substream_seed(self.master_seed, *key)

    def stream(self, *key: KeyPart) -> random.Random:
        """A fresh generator for the substream named by `key`."""
        return stream(self.master_seed, *key)
