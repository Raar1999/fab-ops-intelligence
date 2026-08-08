"""
fabsim — the answer-blind synthetic fab scenario engine.

FabSim generates synthetic semiconductor fab operations *worlds* in which the
generator knows the ground truth and the emitted operational data does not
expose it (`docs/design/FABSIM_DESIGN.md`). It is a separate package from
`fabops` on purpose: fabsim writes datasets, fabops reads them, and neither
imports the other (ADR-013). Nothing under `fabsim/` may import `fabops`, and
fabsim depends on the Python standard library only.

Phase 1 is built in slices. Implemented so far — the deterministic
foundation:

* `fabsim.rng`      named, hash-derived random substreams
* `fabsim.scenario` the `fabsim.scenario/v1` configuration contract, its
                    canonical form, and the dataset identity model

World generation, the timeline, mechanisms, the observable emitters and the
truth artifact are later slices; nothing in this package reads or writes a
dataset yet.
"""
from __future__ import annotations

__all__ = ["SCHEMA_VERSION", "__version__"]

#: Generator version (semver, `FABSIM_DESIGN.md` §7). Any change that can
#: alter emitted bytes for a fixed (config, seed) bumps at least the minor
#: version. The version is one of the four inputs of the reproducibility
#: contract, so bumping it changes every dataset's build fingerprint.
__version__ = "0.1.0"

#: Observable schema version, recorded in `dataset_meta` and the manifest.
#: The schema v2 DDL is a later Phase 1 slice; the constant exists now because
#: the dataset identity model pins it (`SCHEMA_V2_DESIGN.md`, ADR-013).
SCHEMA_VERSION = "2.0"
