# FabSim Design — Answer-Blind Synthetic Fab Scenario Engine

**Status:** Phase 1 design for review. Nothing in this document is implemented.
**Derivation:** every requirement traces to `docs/audit/SYNTHETIC_DATA_AUDIT.md` §3, `docs/audit/DATA_MODEL_AUDIT.md` §3, `docs/audit/RCA_AUDIT.md` §1.4/§2.3, `docs/audit/EXPANSION_ROADMAP.md` Phase 1, and ADR-003/004/010/012.
**Companion documents:** `SCENARIO_SPECIFICATION.md`, `SCHEMA_V2_DESIGN.md`, `TEMPORAL_MODEL.md`, `CAUSAL_MECHANISM_MODEL.md`, `GROUND_TRUTH_CONTRACT.md`, `ANTI_LEAKAGE_DESIGN.md`, `PHASE_1_ACCEPTANCE.md`.

---

## 1. Purpose

FabSim generates synthetic semiconductor fab operations *worlds* in which the generator knows the ground truth, the emitted operational data does not expose it, and a benchmark can later score any diagnostic system against the hidden truth. The one-sentence spec from the audit stands: **generate scenarios the analysis code has never seen, from a config the analysis code cannot read.**

The mandatory separation:

```
                       ┌────────────────────────┐
                       │  SCENARIO CONFIG (JSON)│   hidden input
                       │  + seed + fabsim ver.  │
                       └───────────┬────────────┘
                                   │
                       ┌───────────▼────────────┐
                       │   WORLD GENERATOR      │   src/fabsim (the ONLY code
                       │   (fabsim)             │   that ever sees the config)
                       └─────┬────────────┬─────┘
                             │            │
              OBSERVABLE     │            │     HIDDEN
        ┌────────────────────▼──┐   ┌─────▼──────────────────┐
        │ fab.db (schema v2)    │   │ truth/truth.json       │
        │ + fab_database.sql    │   │ (fabsim.truth/v1)      │
        │ + manifest.json       │   │                        │
        └────────┬──────────────┘   └─────┬──────────────────┘
                 │                        │
        ┌────────▼──────────────┐   ┌─────▼──────────────────┐
        │ DIAGNOSTIC SYSTEM     │   │ BENCHMARK / EVALUATOR  │
        │ (fabops — Phases 2–5) │   │ (eval/ — Phase 6)      │
        └───────────────────────┘   └────────────────────────┘
```

The diagnostic system receives **only** the left branch. The right branch is read exclusively by `fabsim` (writer) and `eval/` (reader). Enforcement is designed in `GROUND_TRUTH_CONTRACT.md` and `ANTI_LEAKAGE_DESIGN.md` and made binding by ADR-013.

## 2. Design principles

1. **Answer-blindness** (ADR-003). Fault identity exists in exactly two places: scenario configs and the truth artifact. No observable table, column, value pattern, filename, or ID encodes it.
2. **Physics-mediated faults only** (ADR-004). A fault influences yield exclusively through the chain *disturbance → latent state → measurable process effect → defect/die-kill mechanism → yield*, with independent noise at every stage. The audited `−0.08 if bad_tool` term is the canonical prohibited pattern.
3. **One event clock.** Every timestamp comes from a single simulated timeline; runs cannot overlap tool downtime; effects follow causes with realistic lags (`TEMPORAL_MODEL.md`).
4. **Determinism per (config, seed, version).** Same inputs → the same dataset content, proven by canonical content hash rather than by SQLite file bytes (`PHASE_1_ACCEPTANCE.md` A1, ADR-014). Different seed → different realization, same scenario semantics.
5. **Keep the verified virtues** of the legacy generator: stdlib-only, self-contained, dual output (`.db` + portable `.sql`), coordinate-level defect geometry, converging multi-channel evidence — generalized from one hard-wired cause to one *configured* cause per scenario.
6. **Right-sized** (ADR-012): one fab, ~20 lots × 25 wafers, ~14-step route, SQLite, seconds-level runtimes. No new runtime dependencies for generation.
7. **The legacy demo survives** (ADR-010): `data/generate_fab_db.py` and its outputs are untouched until `scenarios/demo_etch02.json` reproduces a statistically equivalent ETCH-02 story and every consumer surface has migrated.

## 3. Package architecture

`fabsim` is a **separate package** from `fabops`, in the same repository, with a strict one-way relationship: fabsim writes datasets; fabops reads them. Neither imports the other (lint-enforced, see `ANTI_LEAKAGE_DESIGN.md` L9).

```
src/fabsim/
├── __init__.py            # __version__ = generator version (semver; see §7)
├── cli.py                 # fabsim-build <config.json> --seed N [--out DIR]
├── scenario.py            # config load, schema validation, canonicalization,
│                          #   scenario/dataset identity derivation  [implemented]
├── rng.py                 # seed → named deterministic substreams (see §6)
│                          #                                        [implemented]
├── world.py               # static entities: products, flows, steps, recipes,
│                          #   tools, chambers, operators (from world template),
│                          #   the measures/covers relations, and the generic
│                          #   observation/alarm/die-grid contracts  [implemented]
├── routing.py             # scenario routing conditions resolved against a
│                          #   world; share-based dedication (ADR-015) [implemented]
├── response.py            # alarms, escalation, response-driven maintenance
│                          #   and generic recovery (ADR-017)        [implemented]
├── defects.py             # defect intensity, per-origin geometry and the
│                          #   noisy classifier (ADR-019; replaces
│                          #   models/defects.py)                    [implemented]
├── observation.py         # FDC summaries + zonal metrology from latent state,
│                          #   recipe setpoints and the variation stack
│                          #   (ADR-018; replaces models/parameters.py)
│                          #                                        [implemented]
├── timeline.py            # the event clock: lot release, wafer step progression,
│                          #   tool/chamber occupancy, maintenance windows
├── latent.py              # per-chamber latent state evolution over time, the
│                          #   benign offsets, and the internal Realization
│                          #                                        [implemented]
├── mechanisms/            # fault/event library (each: latent target + drive)
│   ├── base.py            #   Mechanism interface: contribute(context) + registry
│   ├── edge_uniformity.py #   M1 chamber edge non-uniformity (equipment fault)
│   ├── param_drift.py     #   M2 slow setpoint-delivery drift (process drift)
│   ├── particle_load.py   #   M3 between-PM particle accumulation (baseline + recovery)
│   └── benign_offset.py   #   M4 permanent harmless tool/chamber offset (distractor)
│                          #                                        [implemented]
├── models/
│   ├── parameters.py      # observation model: FDC summaries + metrology values
│   ├── defects.py         # Poisson counts, spatial components, classifier channel
│   ├── yieldmodel.py      # die grid, kill probabilities, bins, wafer yield
│   └── maintenance.py     # PM schedule, breakdown hazard, repair/recovery
├── emit/
│   ├── observable.py      # schema v2 SQLite + portable .sql dump
│   ├── truth.py           # truth artifact (fabsim.truth/v1)
│   └── manifest.py        # manifest.json (provenance, hashes)
└── selftest.py            # post-generation invariant + anti-leakage checks
```

Module count is deliberately small; `mechanisms/` is the only place designed for growth.

## 4. Generation pipeline

One build = one dataset. Stages, in order:

1. **Load & validate** scenario config; resolve world template; derive `scenario_id` (content hash) and `dataset_id` (`scenario_id` + seed). See `SCENARIO_SPECIFICATION.md` §5.
2. **Build world**: instantiate static entities (products, flow, steps, recipes, tools, chambers, operators) with per-entity benign variation (tool/chamber offsets drawn once per dataset).
3. **Simulate timeline** (`TEMPORAL_MODEL.md`): advance the clock; release lots; route wafers step-by-step through qualified tools/chambers honoring occupancy and downtime; evolve latent states; fire configured mechanisms at their onsets; trigger alarms, breakdowns, PMs, repairs, recovery.
4. **Derive observables** (`CAUSAL_MECHANISM_MODEL.md`): for each run, compute FDC parameter summaries from latent state + variation stack + noise; generate metrology values; at inspection steps, draw defect populations from intensity models and pass them through the noisy classifier; at final test, resolve the die grid and wafer yield.
5. **Emit observable dataset**: schema v2 SQLite + `.sql` dump + `manifest.json`.
6. **Emit hidden truth**: `truth/truth.json` per `GROUND_TRUTH_CONTRACT.md` (realized truth — actual affected runs/wafers, latent trajectories — not just the config's intent).
7. **Self-test**: run generation invariants (reconciliation, clock, FK) and the fast anti-leakage checks; fail the build on violation.

## 5. Dataset layout on disk

```
data/scenarios/<dataset_id>/
├── fab.db                 # observable — the ONLY input to fabops
├── fab_database.sql       # observable — portable dump (legacy virtue kept)
├── manifest.json          # observable — provenance: dataset_id, scenario_id,
│                          #   seed, fabsim version, schema version, content hashes.
│                          #   Contains NO scenario name and NO fault information.
└── truth/
    └── truth.json         # HIDDEN — read only by fabsim (writer) and eval/
```

`dataset_id` is opaque (e.g., `scn-3f9a1c-s042`); human-meaningful scenario names live only in configs and in the truth artifact. The legacy `data/fab.db` / `data/fab_database.sql` remain exactly where they are, untouched, until migration completes (ADR-010).

## 6. Deterministic randomness

- One **master seed** per dataset (CLI argument or config default).
- No global `random.seed`. Each subsystem gets its own `random.Random` instance keyed by a stable name: `stream(master_seed, "routing", lot_id)`, `stream(master_seed, "defects", wafer_id, step_id)`, etc., with the substream seed derived via SHA-256 of the key tuple. This gives (a) reproducibility, (b) *stability under unrelated changes* — adding a parameter to the yield model must not reshuffle routing draws.
- Deterministic iteration everywhere: entities processed in primary-key order; no set/dict iteration order dependence; no wall-clock reads inside generation (the manifest's `created_at` is the only wall-clock value and is excluded from content hashes).
- Content-stability is an acceptance test (`PHASE_1_ACCEPTANCE.md` A1).

Implemented in `rng.py`: `stream(master_seed, *key)` returns a fresh `random.Random` seeded from SHA-256 over a versioned domain tag, the master seed, and a length-prefixed, type-tagged encoding of the key parts. The length prefix is what makes the encoding injective — without it `("a", "b")` and `("ab",)` would be the same stream. The module holds no mutable state and never calls `random.seed()`, so importing it cannot perturb anybody else's randomness, and Python's salted `hash()` is never used.

## 7. Versioning

- **Generator version**: `fabsim.__version__`, semver. Any change that can alter emitted bytes for a fixed (config, seed) bumps at least the minor version. Recorded in manifest and truth.
- **Schema version**: `2.0` for the Phase 1 observable schema; recorded in the DB's `dataset_meta` table and the manifest. Additive changes bump the minor; breaking changes bump the major.
- **Truth schema version**: `fabsim.truth/v1`, versioned independently (`GROUND_TRUTH_CONTRACT.md` §5).
- **Scenario config version**: `fabsim.scenario/v1` header field (spelled `"fabsim": "scenario/v1"` in the file), validated on load; any other value is rejected. Additive optional fields with empty defaults stay at v1 while no config has been emitted (ADR-015 §4); once configs and datasets exist, any change that could alter an existing config's meaning or identity requires v2.
- **World template version**: `fabsim.world/v1` header field, plus a content digest `world_sha256` recorded alongside it. The version says which contract the file speaks; the digest says which *world* it is, and both the manifest and the build fingerprint carry it.

The benchmark can therefore state precisely: *result R came from scenario X (config hash), world W (world hash), seed Z, fabsim vY, schema 2.0* — the reproducibility requirement of the gate.

## 8. Dependency policy

Generation remains **stdlib-only** (sqlite3, random, math, hashlib, json, datetime, dataclasses). **Config files are JSON** (ADR-014, settled at implementation review): a hand-written YAML subset parser would be a second, weaker JSON with its own bugs, and `json` is already in the standard library and already the format of the manifest and the truth artifact. The design always treated config syntax as cosmetic; this resolves it in favour of the option that adds no code and no dependency. No numpy/scipy/pandas inside `fabsim`. (Numpy-quality Gaussian/Poisson draws are not required; stdlib `random` plus an inverse-transform Poisson is sufficient at this scale.)

## 8.1 Step 3 sequence and gate boundaries

Stage 4 of the pipeline (the physics) is built in ordered slices, each gated on the one before:

| Slice | Builds | Status |
|---|---|---|
| **3.0 Contracts** | the declarations the rest consume: `routing_conditions` + share-based dedication (ADR-015), the `measures`/`covers` relations (gate condition F1), the alarm, die-grid and observation configuration, and `world_sha256` in the build fingerprint | **implemented** |
| **3A Latent plane** | per-chamber latent state on a versioned 60-minute grid, its baseline dynamics and benign offsets, the four-mechanism library, PM reset semantics, and the internal `Realization` (ADR-016) | **implemented** |
| **3B Fab response** | generic alarm rules on the chamber's own control limits, background false alarms, escalation into work orders, response-driven maintenance that blocks production, and one recovery machine for every maintenance event (ADR-017) | **implemented** |
| **3C Process observation** | FDC summaries and zonal metrology from realized latent state through the declared sensitivity matrix, over the full variation stack (ADR-018) | **implemented** |
| **3D Defects** | Poisson intensity as a mixture of physical origins with declared latent sensitivities, per-origin geometry in wafer coordinates, and a noisy classifier over the hidden origin (ADR-019) | **implemented** |
| 3E Die + yield | die grid, kill model, bins, wafer yield | **implemented** |

The boundary 3.0 holds is that it declares *machinery* and never behaviour: the alarm block states thresholds and background rates but fires nothing; the die-grid block states geometry but lays out no grid; the observation block states channels, sensitivities and confusion but computes no value. A contract that named an event, a mechanism, a tool or a chamber would have pre-committed the answer before the mechanism layer existed, so none of them has a field for one.

The boundary 3E holds is not a scan but a signature: `fabsim.die.probe` is handed a timeline, the process observations and the defect population — three observable collections — and never the hidden `Realization`, so the audited `bad_tool → yield` term has no expressible form in the plane that used to carry it. Yield is the count of `PASS` bins over the count of `die_bins` rows, so there is nothing for a penalty to be subtracted from; `target_yield_pct` is a product specification the engine never reads. The chain then stops: no emitter, no database, no truth artifact, no benchmark (ADR-021).

The boundary 3D holds is that the chain stops at a defect. The engine reads latent trajectories, recipes, the route and the clock, and writes inspections and defects; it reaches no die, no bin and no yield — checked by AST over identifiers *and* code strings. The hidden physical origin exists because the geometry needs it, and it lives in a separate record: the observable `Defect` has no field for it, and no `killer_flag`.

The boundary 3C holds is that a measurement is a *projection*. The observation engine reads latent trajectories, recipes and the clock, and writes numbers; it never reaches back to change latent state, and it never reads `Realization.mechanisms`, `.distractors` or `.counterfactual`. Mediation is checked exactly rather than argued: the same timeline is measured twice, once against the realized trajectories and once against their mechanism-free twins, and the difference is asserted to equal the latent departure times the declared sensitivity times the channel scale — and to be exactly zero on every other chamber and before every onset.

The boundary 3B holds is that the chain stops at maintenance and recovery. The response layer reads latent state and writes alarms, maintenance windows and latent recovery — and cannot reach an FDC row, a defect, a die or a yield number, because none of those concepts is in scope for it. It also cannot reach a *mechanism*: the alarm, escalation and repair decision path is checked by AST to contain no mechanism, event, severity or counterfactual identifier, so "generic response rule" is a property of the code rather than a description of it.

The boundary 3A holds is that its only output is **hidden physical state**. A mechanism raises a latent; nothing in the slice can raise a defect, fire an alarm, write a measurement or kill a die, because none of those concepts is reachable from it — `test_the_latent_plane_names_no_observable` and `test_the_latent_plane_imports_nothing_observable` check that structurally rather than by review. The `Realization` it produces is in-memory only: there is no path on disk, no registry and no singleton, so a later observable projection can only be *given* it, never find it (`GROUND_TRUTH_CONTRACT.md` §4). Truth emission remains a later gate.

## 9. What Phase 1 explicitly does NOT build

- No diagnosis engine, monitors, detection, or benchmark metrics (Phases 3–6). `eval/` gets only fixture-level truth *readers* needed by fabsim's own self-tests.
- No dashboard changes, no semantic-layer migration (Phase 2), no ML, no LLM/agent/RAG/KG (ADR-006).
- No full sensor traces, no queue/AMHS/carrier simulation, no multi-fab (rejected, ADR-012).
- Scenario library: only the five initial scenarios of `SCENARIO_SPECIFICATION.md` §4; the rest of the library is specified but deferred.

## 10. Risks and open design questions

| # | Risk / question | Position taken; what review should decide |
|---|---|---|
| R1 | **Calibration difficulty**: making faults detectable-but-not-trivial is a tuning problem. | Severity is defined in units of the natural variation of the aggregated weekly statistic (`CAUSAL_MECHANISM_MODEL.md` §8), and reference-query recoverability is an acceptance test. Accepting: first implementation may need 1–2 tuning iterations; the acceptance tests make that measurable rather than aesthetic. |
| R2 | **Config syntax**: YAML (needs parser decision) vs JSON (uglier, zero-dependency). | **Resolved: JSON** (ADR-014). Zero new code, zero dependency, same format as the manifest and truth artifact; the loader compensates for JSON's ergonomics with strict validation that names the offending field path. |
| R3 | **Timeline model fidelity**: chamber-serial occupancy without queues/dispatching is a simplification. | Accepted deliberately (ADR-012). It is sufficient to make downtime block production and routing respond to availability — the two properties the audit found violated. |
| R4 | **Truth completeness**: which per-wafer detail belongs in truth vs being derivable? | Truth records *realized* affected sets and exposure degrees (not just config intent) so the benchmark never re-simulates. See `GROUND_TRUTH_CONTRACT.md` §3. |
| R5 | **Statistical equivalence of the demo**: "statistically equivalent ETCH-02 story" needs a definition. | Defined in `PHASE_1_ACCEPTANCE.md` A9 as a checklist of qualitative findings (worst tool on 3 channels, edge-ring spatial signature, effect-size windows), not exact numbers. |
| R6 | **Leakage via tuning**: iterating constants until "the demo works" can quietly re-introduce target-specific constants. | Mitigation: all mechanism constants live in the world template / mechanism defaults, never keyed by a specific tool; anti-leakage suite L10 scans for fault-group-specific constants. |
| R7 | **Die-grid cost**: per-die simulation at ~1,000–3,800 die × 500 wafers is ~1–2M die draws. | Trivially within seconds in Python at this scale; if not, coarsen to a fixed 40×40 grid. Not a blocker. |
| R8 | **SCRATCH mechanism** has no Phase 1 mechanism owner (legacy had it as a cosmetic signature). | Kept in the classifier vocabulary and background defect mix (rare random events) so the class exists; a dedicated CMP-scratch mechanism is deferred to the scenario-library expansion. |

## 11. Not-FabKG confirmation

FabSim is a statistical simulator writing relational rows keyed by lot/wafer/tool/time. It introduces no ontology, no graph store, no semantic layer, no LLM/agent/RAG, no external service. All "knowledge" it contains is mechanism arithmetic inside `mechanisms/`, versioned as code. The FabKG boundary (`docs/audit/FABOPS_VS_FABKG_BOUNDARY.md`) is unaffected: every litmus test (statistics-vs-semantics, expiring facts, SQL-table storage smell) lands on the Fab Ops side.
