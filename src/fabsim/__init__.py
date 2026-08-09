"""
fabsim — the answer-blind synthetic fab scenario engine.

FabSim generates synthetic semiconductor fab operations *worlds* in which the
generator knows the ground truth and the emitted operational data does not
expose it (`docs/design/FABSIM_DESIGN.md`). It is a separate package from
`fabops` on purpose: fabsim writes datasets, fabops reads them, and neither
imports the other (ADR-013). Nothing under `fabsim/` may import `fabops`, and
fabsim depends on the Python standard library only.

Phase 1 is built in slices. Implemented so far — the deterministic foundation
and the stage the physics will later play on:

* `fabsim.rng`      named, hash-derived random substreams
* `fabsim.scenario` the `fabsim.scenario/v1` configuration contract, its
                    canonical form, and the dataset identity model
* `fabsim.world`    the `fabsim.world/v1` template contract and the static fab
                    it declares: products, route, recipes, tools, chambers,
                    the measures/covers relations, and the generic observation,
                    alarm and die-grid configuration later slices consume
* `fabsim.routing`  scenario routing conditions resolved against a world, and
                    what a share-based dedication means (ADR-015)
* `fabsim.timeline` the one event clock: lot release, availability-driven
                    routing, chamber occupancy, maintenance, state ribbons
* `fabsim.mechanisms` the fault/event library: what each mechanism drives, and
                    the registry that binds a scenario's events to a world
* `fabsim.latent`   the hidden plane: per-chamber latent state over time, the
                    benign offsets every chamber carries, and the internal
                    `Realization` that records what actually happened
* `fabsim.response` the fab's reaction: generic alarm rules on each chamber's
                    own control limits, background false alarms, escalation
                    into work orders, response-driven maintenance that blocks
                    production, and one recovery machine for every maintenance
                    event — scheduled, background or requested alike
* `fabsim.observation` the process observation plane: FDC summaries grounded
                    in recipe setpoints and zonal metrology referenced to
                    product targets, produced from realized latent state
                    through the world's declared sensitivity matrix
* `fabsim.defects`  the defect plane: Poisson intensity as a mixture of
                    physical origins, per-origin geometry in real wafer
                    coordinates, and a noisy classifier over the hidden origin
* `fabsim.die`      the die grid and the yield plane: a real lattice on a real
                    disc, three competing risks per die — background killer
                    density, a defect that physically reached it, a parametric
                    miss at its own radius — one Bernoulli each, a tester bin
                    drawn as a symptom, and a wafer yield that is the count of
                    survivors and nothing else

The observable emitters and the truth artifact are later slices. Nothing in
this package reads or writes a dataset yet.

`fabsim.latent` is the only slice that reads a scenario's `events`: everything
downstream reads *latent state*, so a fault's effect on a measurement is
mediated by construction rather than by discipline (ADR-004), and the
mediation is verified by subtraction rather than argued (ADR-018). The
timeline slice remains blind to faults — at a fixed seed `simulate_scenario`
gives a null and a faulted scenario the same schedule — while the full
pipeline's schedule does differ once a repair takes a chamber out of service,
which is honest observable data (ADR-017 §5).

The die-grid contract of Step 3.0 is now resolved: `fabsim.die` lays the
lattice out from the product's own wafer and die size and intersects it with
the defect coordinates 3D produced, which is why those had to be real
coordinates rather than zone labels. `fabsim.die.probe` is handed a timeline,
the measurements and the reported defects — three *observable* collections —
and never the hidden `Realization`, so the audited `bad_tool → yield` chain
has no expressible form there (ADR-021).
"""
from __future__ import annotations

__all__ = ["SCHEMA_VERSION", "__version__"]

#: Generator version (semver, `FABSIM_DESIGN.md` §7). Any change that can
#: alter emitted bytes for a fixed (config, seed) bumps at least the minor
#: version. The version is one of the five inputs of the reproducibility
#: contract, so bumping it changes every dataset's build fingerprint.
#:
#: 0.2.0 — Step 3A: the latent plane. New generation behaviour and a new
#: versioned integration grid (`fabsim.latent.LATENT_GRID_MINUTES`), both of
#: which change what a given (config, seed) realizes.
#: 0.3.0 — Step 3B: the fab response. Alarms, response-driven maintenance and
#: generic recovery at *every* maintenance event, which moves latent
#: trajectories and adds windows to the calendar.
#: 0.4.0 — Step 3C: the process observation plane. New emitted quantities and
#: a new per-latent `radial_weight` in the world contract.
#: 0.5.0 — Step 3D: the defect plane. New emitted quantities, a new `defects`
#: block and a new per-product `defect_scale` in the world contract.
#: 0.6.0 — Step 3E: the die grid and the yield plane. New emitted quantities, a
#: new `die_kill` block and a new per-product `killer_density_per_mm2` in the
#: world contract. It also carries the ADR-020 recovery correction, which
#: moved every 3A–3D realization and shipped against 0.5.0 without a bump of
#: its own; 0.6.0 is where that change becomes visible in the fingerprint.
__version__ = "0.6.0"

#: Observable schema version, recorded in `dataset_meta` and the manifest.
#: The schema v2 DDL is a later Phase 1 slice; the constant exists now because
#: the dataset identity model pins it (`SCHEMA_V2_DESIGN.md`, ADR-013).
SCHEMA_VERSION = "2.0"
