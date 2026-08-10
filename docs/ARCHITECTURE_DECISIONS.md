# Architecture Decision Records

Format: lightweight ADRs. Status values: **Accepted** (binding now) · **Planned** (binding when its roadmap phase begins) · **Rejected** (binding prohibition). Decisions derive from the 2026-08-08 forensic audit (`docs/audit/`).

---

## ADR-001 — Audit before architecture before implementation
**Status: Accepted.** The repository was reverse-engineered and verified (every README number checked against the shipped database; suite executed on a clean environment; generator determinism re-proven) before any design was written. No implementation change ships before its phase in `docs/audit/EXPANSION_ROADMAP.md`. The audit documents are the baseline record; they describe the repo as of this date and are not silently rewritten.

## ADR-002 — SQLite + SQL views remain the analytical core
**Status: Accepted.** The four-layer shape (generator → SQLite → SQL semantic layer → thin Python consumers) survived verification with zero correctness defects and is the right scale for the project's honest scope. No client-server database, no warehouse, no ORM. Consequence: analytical logic stays in reviewable SQL; Python adds statistics SQL can't express (permutation tests, change-point detection, scoring).

## ADR-003 — Answer-blind analytics (the central decision)
**Status: Planned (Phase 1/5); lint-enforceable immediately after Phase 1.** Fault identities live exclusively in `scenarios/` configs (read only by `fabsim`) and in `eval/` expected-outcome fixtures. No module under `src/fabops/`, `app/`, or `tests/` (except eval fixtures) may name a suspect. The current `SUSPECT = "ETCH-02"` constants (4 files, audited) are grandfathered only until the diagnosis engine replaces them. Rationale: RCA_AUDIT shows the project's core weakness is that the conclusion is compiled in; this rule is the fix and is mechanically checkable.

> **Status of the grandfather clause, recorded at the Final Integration gate (2026-08-10). The engine has landed and the clause has *not* expired, for a reason the original wording could not anticipate.** `fabops.diagnosis.diagnose(db_path)` exists (ADR-029) and satisfies every rule of this ADR: it names no entity, and `tests/test_diagnosis_contract.py` asserts that `DEMO_SUSPECT_TOOL` would itself trip the engine's rule 3. But the engine does not *replace* the constant, because the two do not read the same fab: the engine reads a **schema v2** dataset and the constant narrates the **schema v1** legacy database, whose retirement ADR-010 defers until every consumer surface has migrated and whose replacement must be "strictly better on that surface" before the demo is deleted from it. So the clause is now governed by ADR-010 rather than by this ADR's own trigger, and what changed is where it lives, not whether it is allowed.
>
> Two facts keep it honest and both are mechanically checked. The four audited definition sites are now **one** — `fabops.config.DEMO_SUSPECT_TOOL`, consumed only by `charts.py` and `investigation.py`, the two legacy narrative surfaces — and `pyproject.toml` names the demo and the engine separately (`fabops-investigate` vs `fabops-diagnose`) so that typing the obvious command cannot hand somebody the hard-coded story while they believe they ran the engine. The lint this ADR's status line anticipates (`no conclusion constant anywhere in src/fabops`) remains an **EXPANSION_ROADMAP Phase 5** acceptance item and is not yet enforceable, because enforcing it today would delete a demo that ADR-010 protects.

## ADR-004 — Faults must be physics-mediated in synthetic data
**Status: Planned (Phase 1).** The data engine may not write a fault label's effect directly into a target variable (the audited `−0.08 if bad_tool` yield term is the canonical violation). Faults act through mechanisms: parameter shifts → defect generation → die kill → yield. If a fault is undetectable through its mechanisms, the mechanism is tuned — never the target. Null (no-fault) and confounded scenarios are mandatory members of the scenario library.

## ADR-005 — Evaluation is a first-class subsystem and gates all claims
**Status: Planned (Phase 6, scaffolded from Phase 3).** The scenario benchmark (detection rate, attribution precision/recall, false-positive rate on nulls, onset error) runs in CI; the README's benchmark table is generated from its output. A capability without a benchmark number may not be claimed in any public surface. This subsumes test strategy: seed-locked exact-count assertions (audited debt #4) are replaced by invariant tests + benchmark expectations.

## ADR-006 — No LLMs, agents, RAG, or knowledge graphs in this repository
**Status: Accepted (binding prohibition).** None of the in-scope engineering questions require them; each would compromise the explainability guarantee (every conclusion decomposes into hand-recomputable statistics) or duplicate FabKG. This includes "AI insight" text generation in the dashboard. Revisit requires a new ADR demonstrating a measured gap on the Phase 6 benchmark that a proposed method closes.

## ADR-007 — Statistical baseline before ML, ML only as benchmarked comparison
**Status: Accepted.** Detection and attribution ship first as transparent statistics (SPC rules, EWMA/CUSUM, permutation tests, additive evidence scores). Any ML proposal must beat that baseline on the Phase 6 benchmark and report the comparison; it becomes an alternative engine, never a replacement of the explainable path.

## ADR-008 — FabKG integration is optional, file-based, one-way per direction
**Status: Accepted (contract), Planned (activation, Phase 10).** Exchange happens only through schema-versioned flat files: `fabops.investigation/v1` JSON export; optional knowledge-prior data files import (validated against this repo's schema, ignored if absent). No shared code, no runtime dependency, no FabKG library in requirements, CI independent of FabKG assets. Full contract: `docs/audit/FABOPS_VS_FABKG_BOUNDARY.md`.

## ADR-009 — Packaging: src-layout Python package with console entry points
**Status: Planned (Phase 0).** `pyproject.toml`, `pip install -e .[dev]`, pytest configured so the documented test command works from a clean clone (fixes the audited P0 defect: bare `pytest` fails with `ModuleNotFoundError: No module named 'src'`). CLI verbs (`fabops build|monitor|investigate|report`) via entry points; Makefile becomes a thin convenience wrapper, portable.

## ADR-010 — The demo scenario is preserved through every migration
**Status: Accepted.** The ETCH-02 case (README, notebook, figures, PDF) is the project's narrative front door and regression anchor. Each phase must keep an equivalent demo working: first on the legacy generator, then as `scenarios/demo_etch02.yaml` producing a statistically equivalent story that the *engine* solves. The demo is deleted from no surface until its replacement is strictly better on that surface.

## ADR-011 — Single source of analytical truth; presentation surfaces are generated
**Status: Planned (Phases 5–9).** The audit found five hand-synchronized copies of the investigation (Python, SQL library, notebook, README, PDF). Target: the diagnosis engine's investigation artifact is the single source; the notebook case study, README benchmark/table sections, and dashboard views are generated or read from it. Hand-written duplicates of engine output are retired as each generator lands.

## ADR-012 — Scale ceiling is declared, not apologized for
**Status: Accepted.** Target scale is one fab, ~10–20 lots × 25 wafers per scenario, full route of ~10–20 steps, SQLite single-file storage, seconds-level batch runtimes. This covers every capability in scope. Kubernetes, streaming, services, multi-fab federation, and cloud deployments are rejected (TARGET_ARCHITECTURE §6); a future need would arrive as a new ADR with a workload that demonstrably exceeds this ceiling.

## ADR-013 — Two-plane dataset artifacts: observable data and hidden ground truth are separate, independently versioned files
**Status: Planned (Phase 1); designed 2026-08-08 (`docs/design/GROUND_TRUTH_CONTRACT.md`).** Every fabsim dataset is emitted as two physically separate planes under `data/scenarios/<dataset_id>/`: the **observable plane** (`fab.db` schema v2 + portable dump + `manifest.json`) and the **hidden plane** (`truth/truth.json`, schema `fabsim.truth/v1`, recording the *realized* ground truth — affected runs/wafers, latent trajectories, recovery — not merely the config's intent). No truth table, column, or value may exist inside the observable database; manifests and dataset IDs are opaque (content-hash scenario IDs, no scenario names). Read discipline: `fabsim` writes both planes; `src/fabops/` and `app/` read only the observable plane (lint-enforced: no fabsim import, no `scenarios/`/`truth` path references); only `eval/` joins the two planes, on `dataset_id`. Reproducibility contract: (scenario config hash, seed, fabsim version, schema version) — all recorded in the manifest — fully determine the dataset. *(The word "byte-stably" in the original text is refined by ADR-014: the portable oracle is a canonical content hash, not the bytes of the SQLite file.)* This extends ADR-003 (which governs *code* blindness) to the *artifact* level, and is what makes the Phase 6 benchmark's provenance statement ("result R came from scenario X, generator Y, seed Z") exact.

## ADR-014 — Scenario configs are JSON; reproducibility is proven by content hash, not by file bytes
**Status: Accepted (2026-08-08), implementing the Phase 1 design gate's "approved with conditions".** The architecture review approved `docs/design/` unchanged and left three implementation-level conditions. They are resolved as follows.

1. **Configuration format is JSON.** `FABSIM_DESIGN.md` §8/R2 left YAML-vs-JSON to implementation review, with a hand-written YAML-subset parser as one option. JSON wins: it needs no parser, keeps FabSim stdlib-only, and is already the format of the manifest and the truth artifact — one syntax for every FabSim artifact. A YAML subset would have been a second, weaker JSON with its own bugs and its own silent-coercion surprises, for a readability gain the strict loader recovers by naming the exact field path of every rejection. **No YAML parser and no PyYAML dependency enters this repository.** Wherever an earlier document writes `scenarios/*.yaml`, read `scenarios/*.json`. The header field keeps its documented spelling: `"fabsim": "scenario/v1"` *is* the `fabsim.scenario/v1` contract.

2. **Reproducibility is defined by a canonical content hash.** Acceptance criterion A1 originally required byte-identical `fab.db` across clean runs "including on CI's OS". SQLite file bytes depend on the library version, page size and free-list history, so that test measures the storage engine as much as the generator and can fail for reasons FabSim does not control. A1 is restated (`PHASE_1_ACCEPTANCE.md`) around four checks: the `build_fingerprint` over the four inputs; a canonical row-level `content_sha256` over every table of `fab.db` in normalized form (the portable, cross-platform guarantee); byte comparison of the deterministic `.sql` dump and the canonical-JSON truth file; and — only on the pinned CI reference image — an *additional* SHA-256 of `fab.db`, which never gates the cross-platform result. The requirement is not weakened: byte identity was a proxy for "the same data", and the content hash tests that property directly, value by value, and names what diverged when it fails.

3. **`dataset_meta` seed wording reconciled, and the schema table count corrected.** `SCHEMA_V2_DESIGN.md` §2.1 claimed the provenance row contains "no seed" while `dataset_id` is defined as `<scenario_id>-s<seed>`. Both stay as designed; the wording now states the distinction that was meant: no seed *column* and no scenario name, with the seed present only inside the opaque id, where it discloses nothing (every scenario can be built with seed 42, and `scenario_id` is a one-way hash of the canonicalized config with name and description excluded from the digest). The seed as a first-class field remains in the manifest; the scenario name remains only in truth. Separately, the schema's own §2 enumerates tables 2.1–2.22 while the prose said "21 tables"; the count is **22**.

The identifier scheme itself is unchanged: `config_sha256` → `scenario_id` → `dataset_id` exactly as `SCENARIO_SPECIFICATION.md` §5 defines them. `build_fingerprint` is added alongside, not in place of them, because `dataset_id` names a (scenario, seed) pair and cannot by itself distinguish two generator versions.

## ADR-015 — Dedication is layered and share-based; the scenario contract gains `routing_conditions` at v1
**Status: Accepted (2026-08-08), implementing the Step 3 architecture gate's "ready with conditions".** Step 2 placed dedication in the world template and implemented it as a hard candidate restriction (`TEMPORAL_MODEL.md` §7). Both halves of that are now decided differently, and the scenario contract is extended in place rather than versioned.

**1. Dedication is layered: standing policy in the world, experimental conditions in the scenario.** The world template keeps `routing.dedications` — the routing machinery, the qualification map, stickiness and the standing allocation policy all describe *this fab* and belong together. What does not belong there is "during days 28–62 of this experiment". A time-bounded condition is a property of the experiment, not of the fab, and three things follow if it lives in the world anyway: scenario G needs a world of its own; the anti-leakage rule that all library scenarios share one world (D7) acquires an exception whose only purpose is the confounder; and the world template — the thing that is supposed to be scenario-independent — starts carrying one scenario's intent. So `fabsim.scenario/v1` gains an optional `routing_conditions` array, and `fabsim.routing` composes the two layers with the scenario's conditions in front of the world's standing policy.

A routing condition is deliberately **not** an event. Events are hidden; a dedication window is *observable by design* — the routing shift appears in `runs`, and a diagnosis engine is expected to see it and control for it (`SCENARIO_SPECIFICATION.md` §4 G). It touches no latent state and reaches no mechanism.

**2. Dedication is a share, not a filter.** A dedication now carries `share ∈ (0, 1)`: with that probability a covered routing decision is restricted to the dedicated tool, and otherwise the whole qualified pool — including that tool — is in play. Qualification, chamber eligibility, stickiness, availability and deterministic tie-breaking are untouched. **Dedication changes exposure probability, not eligibility.** The hard filter had to go because it makes product and chamber exposure the *same* variable inside the window, and scenario G's entire demand is that the chamber effect survives a within-product comparison while the product effect does not survive a within-chamber one. Under a filter neither comparison has data, and the benchmark would be scoring an impossibility rather than the hard part of commonality analysis. Realized share sits somewhat above the configured one — the released traffic can still land on the dedicated tool, and stickiness compounds it — which is the intended shape: a preference, bounded away from certainty.

**3. Dedication is tool-level. There is no chamber field, in either layer.** Stating one is an error, not a warning, in both the world loader and the scenario loader. A chamber-scoped dedication would aim traffic at exactly the grain a fault is later attributed at; the confounder would stop being something to untangle and become a pointer at the answer.

**4. Extending v1 rather than minting v2 is safe *because no scenario config exists yet*.** The field is optional and defaults to `[]`, so every configuration that would have been valid still is and canonicalizes identically; no dataset ID, truth file or benchmark result depends on it, because none has been emitted. That window is now closing. Once `scenarios/*.json` and their datasets exist, a contract change that could alter the meaning or the identity of an existing config — a removed field, a narrowed vocabulary, a changed default, a changed canonical form — requires `fabsim.scenario/v2` and a migration note, because `config_sha256` is load-bearing for every artifact downstream of it. Additive optional fields with empty defaults remain a v1 change; anything else does not.

**5. The world template joins the reproducibility inputs.** `build_fingerprint` covered (config hash, seed, fabsim version, schema version) and silently omitted the world — so two datasets built from the same config against two different worlds were indistinguishable by their recorded identity. `world_sha256` (SHA-256 over the template's semantic content, prose excluded, formatting and byte-order mark normalized away) is added as the fifth input, and `dataset_identity` requires it rather than defaulting it: a fingerprint that guessed at the world would claim a reproducibility it cannot deliver. `dataset_id` is unchanged and stays a (scenario, seed) pair.

## ADR-016 — The latent plane: a versioned integration grid, world-declared dynamics, and severity calibrated against the null
**Status: Accepted (2026-08-08), implementing Step 3A of the Step 3 gate sequence.** The hidden middle layer ADR-004 requires is built (`src/fabsim/latent.py`, `src/fabsim/mechanisms/`). Five decisions are recorded because each one changes what a dataset realizes or what a later slice may assume.

**1. One clock, sampled — not a second timeline.** Latents are integrated on a **60-minute grid** (`fabsim.latent.LATENT_GRID_MINUTES`) over the same simulated clock the timeline runs on; grid point *i* is the state during `[i·60, (i+1)·60)` minutes, so a run and the latent state it saw are the same instant by construction. The constant is part of the dataset identity: every latent process is parameterized *per grid step* (an AR(1) φ is a φ per step), so changing the grid changes every realization and may only move together with a `fabsim` version bump. `fabsim.__version__` accordingly moves 0.1.0 → 0.2.0, which propagates into `build_fingerprint`.

**2. Latent noise is addressed by (tool, chamber, day block), not by run and not by a single per-chamber stream.** Extending the horizon, changing the lot count, or adding a subsystem cannot perturb a day that was already realized. Per-grid-point streams would give the same guarantee at roughly twenty times the cost and no additional property, so the block is one simulated day. New namespaces: `variation.tool`, `variation.chamber`, `latent.<name>`, `mechanism.<name>`. Every Step 2 namespace is untouched. (`variation.fab_week` and `variation.lot` are *not* used here: the fab-wide wander and the lot AR(1) of `CAUSAL_MECHANISM_MODEL.md` §2 are observation-plane terms and belong to 3C.)

**3. Baseline latent dynamics are world-template data, not code** — a new required `latents` block, one entry per latent declared in `observation.latents`, and a new required `mechanisms` block holding every constant the mechanism library acts with (rule D6, risk R6). Two families are declared: `ar1` (wander about a level it reverts to) and `accumulation` (climbs between cleans). This extends `fabsim.world/v1` with **required** fields rather than optional ones; that is safe only because the sole world template lives in this repository and no dataset has been emitted, and it is the last such change that will be cheap — the ADR-015 §4 reasoning now applies to the world contract too.

**4. Severity is calibrated against the null latent distribution and against nothing else.** A magnitude is `severity_sigma × severity_reference`, where `severity_reference` is the declared σ of a *healthy* chamber's weekly-mean latent, and `test_the_declared_severity_reference_matches_the_null_world` checks the declared number against what the null realization actually does across seeds. No yield, defect count, benchmark score or diagnostic result is consulted — none of them exists yet, and calibrating against them is how a project re-invents target leakage in a politer form. Consequence to know: for the `ar1` latents the realized shift lands on the §8 ladder (≈1.5σ / 3σ / 6σ, `param_bias` a little under because a PM recentres it); for the `accumulation` latent an **unattended excursion exceeds its nominal shift**, because a load keeps climbing until something cleans it. Severity there sets the escalation, not a ceiling, and `realized_shift_sigma` records what actually happened. That over-run is the physics scenario I's repair exists to stop.

**5. Only PMs move latents at this gate; fault-driven repair is 3B — and 3B owes background repairs the same treatment.** PM semantics follow `CAUSAL_MECHANISM_MODEL.md` §6 exactly: `particle_load` fully cleaned, `param_bias` recentred by a drawn `N(0.7, 0.1)` fraction, `edge_uniformity` untouched (hardware is not fixed by cleaning), benign offsets never reset. Unscheduled maintenance windows exist in the timeline and deliberately do not reach the latent plane yet. **This leaves an obligation on 3B:** when repairs are wired to latents, background breakdowns must recover latent state too. If only fault-driven repairs moved a latent, "a repair that changed behaviour" would become a fault fingerprint — leakage class T3 — since only faulted chambers would ever have one.

**6. A mechanism is never told which entity it acts on.** `contribute` receives a `MechanismContext` carrying the grid, the onset, the profile, a severity-calibrated magnitude, its world constants and one RNG stream — no chamber, no tool, no world, no timeline, and not even the latent it drives. It returns a drive series that the engine applies to every chamber the target resolves to. Entity-specific behaviour is therefore not merely forbidden by a lint, it is unwritable, which is the strongest available form of rule D6. Realized mechanism effect is measured by integrating each trajectory **twice on identical draws** — once as configured and once with every drive removed — so "what the fault did" is a counterfactual difference rather than an assertion, and "the fault had not started yet" is bit-identity rather than a promise.

## ADR-017 — The fab's response is generic, fab-wide, and applies one recovery machine to every maintenance event
**Status: Accepted (2026-08-08), implementing Step 3B of the Step 3 gate sequence.** The response layer (`src/fabsim/response.py`) turns latent conditions into alarms, work orders and maintenance, and maintenance into latent recovery. It is the easiest place in the simulator to build a fault detector by accident, so five decisions are recorded.

**1. One recovery machine for every maintenance event.** This discharges the condition the Step 3A review carried forward. Scheduled PM, background breakdown and condition-driven repair all recover latent state, and the unscheduled kinds are *indistinguishable*: one intervention-quality draw ~ Beta(8, 2) with a 10% no-fix chance (`CAUSAL_MECHANISM_MODEL.md` §6), spread across latents by each latent's declared `repair_efficacy`. The function that draws it cannot tell a breakdown from a requested repair, because it is not told. Had only fault-driven repairs moved latents, "behaviour changed after a repair" would have separated faulted chambers from healthy ones perfectly — leakage classes T3 and T5 — since only faulted chambers would ever have had one.

**2. The response policy is a property of the fab, not of a scenario.** Thresholds, detection probability, escalation, repair delay, cooldown and recovery are one world-template `response` block, applied identically everywhere. Consequently **a scenario's `response` block is declared intent, recorded for the truth artifact, and is not read by the engine.** Honouring `response.alarm`, `repair_delay_days_mean` or `recovery` per event would have made the fab react differently *because* a chamber was faulted, which is precisely the shortcut §5 and §13 of the gate forbid — and `response.alarm` in particular is an *outcome* under a generic threshold model, not an input. Two scenarios differing only in their `response` block now produce byte-identical responses, and a test pins that. If a later gate needs per-scenario response tuning, it arrives as a **world variant** (which anti-leakage rule D7 already prefers) rather than as an engine input.

  *Consequence for acceptance:* `PHASE_1_ACCEPTANCE.md` A5 spoke of scenario I's residual being "≈ configured (1 − recovery_fraction)". With the engine fab-wide the residual is *emergent* — Beta(8, 2) mean 0.8 gives ≈20% residual, close to the 15% the scenario text imagined — and truth records the **realized** fraction. A5's wording is updated to say "realized" rather than "configured"; the criterion is not weakened, it is made checkable against something that exists.

**3. Alarms are individuals charts on the chamber's own history, and the chart matches the process — never the fault.** A rule compares a signal against limits that chamber set for itself during a qualification window. The centre absorbs the permanent benign offset, without which rule F11's honest structure would have become the fingerprint it exists to prevent. The spread is measured once and then **frozen**, because a spread that kept adapting would widen to accommodate a slow ramp and a fault would become invisible by being persistent. Stationary signals get a fixed centre so a sustained departure stays visible; accumulating signals get a centre that climbs with the sawtooth, or every chamber in the fab would alarm at the top of every PM cycle. That choice is made from the latent's declared `family`, which is a property of the physics and knows nothing about events.

**4. Escalation counts alarms, not causes.** *N* complaints on a chamber inside a window become one work order, whatever raised them. A healthy chamber that draws three background false alarms in a week gets maintenance — and that is the point: it is what puts unscheduled repairs on chambers where nothing is wrong, so a repair cannot be read backwards into a fault. A requested repair is emitted as an ordinary `UNSCHEDULED` window with the same technician roster and the same action-code vocabulary as a breakdown; nothing on the observable row says which it was.

**5. A repair blocks production, which means the schedule is no longer fault-blind — and that is honest data.** `simulate()` gains an optional `maintenance=` parameter: the response layer walks the clock over the fab's own calendar, places the repairs it asks for into the chamber's free time, and production is then laid out against the completed calendar by the *unchanged* scheduler. So routing sees a repaired chamber as unavailable, and `TEMPORAL_MODEL.md` §6's "no run overlaps DOWN/PM/QUAL" holds over response maintenance too. The Step 2 property "a null and a faulted scenario schedule identically" now holds of `simulate_scenario` (the timeline slice, still fault-blind and still tested) rather than of the full pipeline — which is correct, because a chamber taken out of service really does lose exposure, and background breakdowns already do that to every chamber in the fab.

**Interim signal, to be replaced by 3C.** Until the process-observation model lands, a `channel`-source alarm rule is evaluated against a *latent-space proxy*: the channel's own declared sensitivities applied to the chamber's latent state. It invents no FDC value and emits no observable row; it is the same linear map 3C will use, read one plane earlier. `_signal_value` is the single function 3C replaces.

## ADR-018 — The observation plane: sensitivities are per latent severity-σ, radial shape is declared, and the transfer function is calibrated but not amplified
**Status: Accepted (2026-08-08), implementing Step 3C of the Step 3 gate sequence.** The process observation plane (`src/fabsim/observation.py`) projects realized latent state into `run_measurements` and `metrology` rows. Four decisions are recorded.

**1. A channel's `sensitivity` means *channel scales per latent severity-σ*.** The two planes are stated in unrelated units — latent magnitudes are O(0.01) in their own terms, channel scales are O(1) in theirs — and their raw product sits four orders of magnitude below the run noise, which would have made every fault invisible. The latent is therefore divided by its own `severity_reference` before the sensitivity is applied:

```
latent_effect(p) = Σ_latent  sensitivity[p][latent] × latent(c, τ) / severity_reference[latent] × scale[p]
```

This is what makes `CAUSAL_MECHANISM_MODEL.md` §8's ladder mean the same thing on both sides of the boundary: a departure of one latent σ arrives as one channel σ-ish, and the world template's sensitivities set how much of it each channel sees. No contract field changed; the *reading* of `sensitivity` is now stated.

**2. `radial_weight` is added to the per-latent contract.** §2 requires a within-wafer radial term for *one* latent ("`cd_nm_edge` and `cd_nm_sigma` respond while `cd_nm_center` barely moves") and a uniform one for the others — and the observation model must not learn which is which by name. Each latent therefore declares how much of its effect is radial, in [0, 1]: 0 shifts every zone alike, 1 leaves the centre untouched and lands entirely at the edge. A zone's radial position is its index in `observation.wafer_zones` normalized onto [0, 1], the list being declared centre-outward; this keeps `wafer_zones` a list of names rather than of radii and carries the same information for evenly spaced zones.

**3. A run reads the mean of the latent grid points its window covers.** A run is a window on the same clock the latent grid samples, so an FDC summary of a whole run is the mean over that window. Nothing past the run's end is read, which is what makes "a measurement never reads its own future" checkable rather than assumed. A metrology reading uses the **measured run's** window on the **measured run's chamber** — not the metrology tool's — which is what makes the `measures` relation (gate condition F1) load-bearing: a CD number indicts the chamber that made the feature.

**4. The transfer function is calibrated against the null world, and deliberately not amplified.** Measured on the baseline world: a channel's within-chamber weekly σ is 0.5–1.6 channel scales, and one latent σ produces about 0.6 of that — so the declared sensitivities already sit within a factor of ~1.6 of "one latent σ moves the weekly aggregate by one of its own σ". They were left alone. Amplifying them to make a fault easier to find is precisely the over-tuning the gate forbids, and the per-run effect is *meant* to be small: a moderate fault moves a single wafer by well under one run-noise σ, and the affected and healthy per-run distributions overlap heavily. The audited v1 gave the answer away in one GROUP BY; that is what this replaces.

*Two consequences worth knowing.* First, because 3B repairs and PMs repeatedly recover a latent, the *realized* departure of a sustained fault is well below its configured severity — a moderate `param_drift` realizes ≈1.8σ rather than 3σ — and the observable follows the realized state, exactly as the gate requires. Second, because `edge_uniformity` is signed (ADR-016), a chamber whose benign radial offset opposes the fault sees its within-wafer *spread* **narrow** rather than widen while the fault cancels that offset. The edge-versus-centre contrast still moves in the fault's direction on every chamber; only the spread's direction is chamber-dependent. That is honest physics for a signed latent, and it means `cd_nm_sigma` is corroborating evidence rather than a guaranteed signature.

## ADR-019 — The defect plane: origin is physics, classification is an instrument, and coordinates are the artifact
**Status: Accepted (2026-08-08), implementing Step 3D of the Step 3 gate sequence.** The defect/inspection plane (`src/fabsim/defects.py`) turns realized latent state into `inspections` and `defects` rows. Four decisions, plus one defect found upstream that this plane made visible.

**1. Intensity is a mixture of physical origins, each with declared latent sensitivities.** `Λ = Σ_origin (base_rate × product defect_scale + Σ_latent sensitivity × magnitude)`, realized as `Poisson(Λ × scan_area)`. The components are named by *physical origin* (`uniform`, `edge_ring`, `center`, `particle_cluster`, `scratch` — `CAUSAL_MECHANISM_MODEL.md` §4.2), never by a mechanism, and the mixture's components must be exactly the classifier's declared origins: an origin the fab can produce but the classifier cannot label would emit a defect with nothing to call it, and one the classifier knows but nothing produces would be a class with no support (rule D2). Every origin has a positive `base_rate`, so a null world produces the whole vocabulary. A new required `defects` block carries this; a new required per-product `defect_scale` carries §4.1's product-dependent baseline defectivity, which is standing distractor structure a naive commonality analysis could wrongly accuse.

**2. A signed latent contributes its magnitude.** An intensity may not go negative, and a signed latent says nothing about which direction is worse: a chamber that etches its edge fast and one that etches it slow are both non-uniform and both leave a ring. `_propensity` therefore takes `abs()` for the `ar1` family and `max(0, ·)` for `accumulation` — a stated transformation rather than a clip applied after the fact (task §22).

**3. Geometry comes from the origin, and coordinates are the only output.** Each component places its defects by its own shape (§4.3): uniform over the disc by area, an annulus with radial jitter, a centre Gaussian, clumps around a shared seed, and a stroke along a seed direction. Nothing writes a zone, a ring flag or an edge label — an analyst derives the edge fraction and the wafer-map signature from `(x_mm, y_mm)`, and so will 3E, which needs real coordinates to intersect defects with a die grid. Wafer radius comes from the product; a jittered point that would land off-wafer is pulled back along its own radius, which cannot manufacture a preferred direction. Reported size is lognormal and thinned by each inspection step's own `sensitivity_threshold_um`, so a scanner reports only what it can see.

**4. The classifier is an instrument, not a label.** `classified_type` is *drawn* through the world's confusion row for the hidden origin, so a `particle_cluster` is called PARTICLE about 88% of the time and something else otherwise (measured: 0.880 against a declared 0.88). Every class arises from more than one origin, and origin and class disagree on more than 40% of defects — so the observable plane cannot be read back into the hidden one. The origin lives in a separate `DefectOrigin` record for a later truth emitter; the observable `Defect` has no field for it, and no `killer_flag` (deliberately dropped by `SCHEMA_V2_DESIGN.md` §2.20 — killer status is what a fab learns from test overlay, which is 3E).

**5. An upstream defect this plane made visible — reported, not worked around.** *(Fixed on 2026-08-09 by **ADR-020**, in its own gated session as this entry proposed. The description below is left as written: it is the record of what 3D found and why it did not tune around it. Two details of the proposed fix turned out to be wrong on inspection and are corrected in ADR-020 — it is not a one-line change, because a PM and a repair are different physical acts and only one of them may skip the wander; and the standing correction an earlier PM left behind is itself persistent, so a repair acts on it too.)* 3D is the first plane whose output depends on the *magnitude* of a latent rather than on changes in it, and that exposed a flaw in the Step 3A/3B recovery model. `_LatentState.recover` books a permanent carry of `fraction × (drive + state + carry)`, but `state` is the *mean-reverting* AR(1) term. Cancelling a transient permanently biases the chamber: measured on the null baseline world, chambers that were repaired end the horizon 2.5–4.3σ away from their benign offset, while unrepaired chambers sit within ±1σ of it. Because `|edge_uniformity|` is what edge defectivity reads, a chamber can be pushed far from zero by an unrelated background repair, and a subsequent fault can then *reduce* its non-uniformity. The consequence: the end-to-end edge-defect signature of scenario B is reliable only at `obvious` severity on the baseline world, which puts acceptance criterion **A9 at risk**.

  3D was implemented against the physics as it stands and was **not** tuned to mask this — amplifying the defect sensitivities to manufacture a signature is precisely the over-tuning the gate forbids. What 3D guarantees is *directional mediation*, verified in both directions and at three chambers: where the magnitude a component reads rises its defects rise, and where it falls they fall. The proposed fix, for a dedicated follow-up before the scenario library is built, is one line: recover only the persistent part of the departure (`drive + carry`), leaving the mean-reverting `state` alone. That changes every 3A/3B realization and so belongs in its own gated session, not here.

## ADR-020 — Maintenance recovers a latent's *persistent* departure, never its self-correcting wander
**Status: Accepted (2026-08-09), correcting the Step 3A/3B recovery model that ADR-019 §5 reported.** This is a correction gate, not a new plane: no slice was added, and 3E remains unstarted. Six things are recorded because the change moves every 3A/3B realization and everything downstream of them.

**1. What was wrong.** `_LatentState.recover(fraction)` booked a permanent credit against the whole of `value - offset`:

```
carry -= fraction × (drive + state + carry)
```

and applied it identically to a PM, a background breakdown and a requested repair. For the `ar1` latents `state` is the **mean-reverting** AR(1) term.

**2. Why that is physically wrong.** Correcting a transient permanently is over-control: the wander reverts on its own, the credit does not, and the chamber is left biased by `−fraction × state_at_the_repair` for the rest of the horizon. It is not a small effect, because `severity_reference` is the σ of a *weekly mean* while the credit is booked against an *instantaneous* value — for `edge_uniformity` the stationary spread is about 1.9 weekly σ, so one unlucky repair moves a chamber a couple of σ and repeated ones compound. Measured on the null baseline world over 12 seeds, repaired chambers' final-week distance from their own benign offset had rms 1.67σ against 1.06σ for unrepaired chambers, reaching 4.6σ. Because the defect plane reads `|edge_uniformity|` as a magnitude (ADR-019 §2), a background repair on a chamber where nothing was wrong could raise its edge defectivity, and a later fault could *reduce* it.

**3. What recovery acts on now.** A wander latent is held as named parts whose *dynamics* differ, and maintenance is dispatched by the **kind of work**:

```
value = offset + drive + credit + state          persistent := drive + credit

recentre(f)   credit -= f × (drive + credit + state)     a clean, a calibration — a PM
restore(f)    credit -= f × (drive + credit)             a repair — any UNSCHEDULED window
```

The two differ by exactly one term. Nothing else changed: the same fraction, from the same draw, on the same stream.

**4. Why the natural state is preserved under `restore`, and not under `recentre`.** A technician replacing a worn part removes a degradation that was not going to correct itself; there is no instrument in that job that reads the chamber's ordinary fluctuation, and nothing to re-zero it against. A *calibration* is the opposite kind of act: it reads the delivered-versus-set deviation and trims it, without asking how much of it was a fault, how much was drift and how much was wander. So a PM's over-control is real, deliberately kept, and applies to every chamber on the same 30-day cadence — it is symmetric standing behaviour, not a repair-conditioned artifact. `CAUSAL_MECHANISM_MODEL.md` §6 says a PM *recentres* `param_bias`, and a recentre that skipped the wander would do nothing at all to a healthy chamber, which is not what §1's table declares.

**5. PM semantics are unchanged, and so is the symmetry ADR-017 bought.** `particle_load` fully cleaned, `param_bias` recentred by a drawn `N(0.7, 0.1)`, `edge_uniformity` untouched by a PM, benign offsets never reset. The **accumulation** family is untouched by this ADR in both actions: a load is real material, whoever put it there, so a repair removes a share of it on a healthy chamber exactly as it always did — which is what keeps ADR-017's "a repair happens to healthy chambers too" visible in the physics and not only in the calendar. `restore` is reached by a background breakdown and a requested repair through one call with one distribution; it is handed an action and a fraction and is told nothing about a mechanism, a scenario or a cause. What differs after a repair now differs *because the chamber's state differed*, which is mediation (ADR-004), not a shortcut.

**6. How a recurrence is caught.** Five tests fail if the old arithmetic comes back, and one of them is exact: on the null world a repair leaves `edge_uniformity` **bit-identical** (`before == after`), because a wander is not something a technician can restore and a null chamber has nothing else standing. The population form compares repaired against unrepaired null chambers over three seeds in the design's own weekly-σ scale, with the ±30% tolerance the severity-reference check already uses; the arithmetic form pins `recentre` and `restore` side by side on one state and asserts they differ by exactly `fraction × wander`; and a mutation test reinstalls the pre-ADR-020 formula and asserts the guard breaks. The correction is verified in both signs of `edge_uniformity`, because the `abs()` that makes a signed latent into a defect propensity lives in the intensity model and must never reach the recovery arithmetic.

*What this changes downstream, stated plainly.* Every 3A/3B/3C/3D realization moves — the latent values differ, so alarms differ, so repairs differ, so observations and defects differ. The **RNG derivation is untouched**: same domain tag, same namespaces, same draw order, same `LATENT_MODEL`/`RESPONSE_MODEL` version strings, and determinism, seed-sensitivity and cwd-independence are unchanged. The realized *departure* recursion is algebraically identical to the old one (`d ← (1 − f)·d` for both actions), so every mechanism-effect number — `realized_magnitude`, `realized_shift_sigma`, the ramp and onset properties, the severity ladder — is untouched by construction. The declared `severity_reference` values still match the realized null within the ±30% the acceptance check allows (`edge_uniformity` moved from 1.04–1.20 to 0.91–1.07 of declared), so **no world-template constant and no defect sensitivity was retuned.**

## ADR-021 — The die plane: yield is a count of survivors, and the kill model is not shown the hidden plane
**Status: Accepted (2026-08-09), implementing Step 3E of the Step 3 gate sequence.** The die grid and yield model (`src/fabsim/die.py`) turn a product's geometry, the fab's own measurements and the reported defects into `die_bins` and `wafer_yield`. This is the plane the audit's central defect lived in — `wafer_yield` was a number with `−0.08 if bad_tool` subtracted from it — so six decisions are recorded.

**1. The strongest guarantee is the function signature.** `probe(timeline, observations, population)` takes three *observable* collections and **no `Realization`**. Latent state, mechanism records, distractor records, the counterfactual series and the hidden defect origin are therefore not merely unread by the kill model: they are unreachable from it, and `−0.08 if bad_tool` has no expressible form. Every earlier plane could reach the hidden plane and was held back by an AST scan; this one is held back by what it was handed. The scans are still there — no mechanism, scenario, entity, truth, benchmark or `target_yield_pct` identifier, no origin, no trajectory — and one of them is new in kind: **every string a comparison in the module tests against must be declared vocabulary** (a coverage state, the partial-die policy, an operation type), so a branch on a tool or a mechanism name cannot be written without failing a test.

**2. The lattice is real geometry on a real disc, and the conventions are stated once.** Not `πr²/die_area` with coordinates invented afterwards. Pitch is the die footprint plus one `street_width_mm`, so a scribe lane is real space a defect can land in harmlessly; the lattice is centred on the wafer, so no product gets a phase of its own; `row_major` numbers rows downward from the top; and **edge exclusion is applied to the footprint, not the centre** — a die is `inside` only if all four of its own corners clear the usable radius. A centre rule would admit die hanging over the edge, and on this world such die exist, so the distinction is load-bearing rather than decorative. The one softened comparison in the module is a 1e-9 mm tolerance on that corner test, documented in place: it is 1e-4 of a street, and the alternative is that floating-point noise decides a die. The grid is a pure function of (world, product) — no seed reaches it — so `die_bins.die_x/die_y` means the same physical place in every dataset.

**3. `partial_die_policy` is dispatched, not assumed.** `PARTIAL_DIE_POLICIES` has one member today (`exclude`). The engine branches on it explicitly and raises on a value outside the vocabulary, so a second policy has exactly one function to answer for; and no second value was invented to make a comparison test possible, because widening a versioned contract to suit a test is how contracts stop meaning anything.

**4. Three competing risks, one Bernoulli, one dead die.** `P(dead) = 1 − (1−p_bg)(1−p_defect)(1−p_param)`, exactly `CAUSAL_MECHANISM_MODEL.md` §5, with one draw per die — so a die that a defect reached *and* that missed its parametric window is one dead die, not two. Which risk to attribute it to, needed only to draw a bin, is each risk's share of the total hazard `−ln(1−p)`: the standard competing-risks decomposition, and bookkeeping over probabilities that have already decided the outcome rather than a fourth model. The three:

  * **`p_bg = 1 − exp(−D₀·A)`**, the classical Poisson yield model over the die's own area, with `D₀` a new required per-product `killer_density_per_mm2`. It is deliberately a *different* number from `defect_scale`: an inspection reports only what its threshold can see at the two layers it scans, and a wafer's killers are mostly the ones no scanner reported — which is why the ~44 reported defects per wafer cannot, and should not, account for a 13-point loss.
  * **`p_defect`**, from the reported defects that physically reached the die: overlap of the defect's own radius plus a declared halo with the die footprint, then `s²/(s² + half²)` — saturating in the defect's *cross-section*, because what bridges a feature is area — weighted by a declared per-layer lethality. Size and layer are fields on the observable defect row; the hidden origin is not consulted and is not reachable.
  * **`p_param`**, from the wafer's own metrology read at the die's own radius. The zone readings are interpolated linearly in radius — the same convention 3C *generated* them with, so this is an inverse rather than a second radial model — and the die fails if its local value plus within-die scatter falls outside a functional limit: `Φ((d−L)/s) + Φ((−L−d)/s)`. Two-sided, smooth, monotone in `|d|`, bounded, and with no threshold anywhere.

**5. The functional limit is wider than the control limit, and that is a decision the design left open.** §5 says a die's kill probability "rises with the local |CD − target|" and does not say how far out it becomes fatal. A control tolerance is where a *fab intervenes* (±3.5 nm on a 45 nm CD is ±7.8%); a transistor stops working somewhat further out. `parametric.kill_limit_tolerances = 3.0` states the functional limit in multiples of the step's declared tolerance, and `within_die_sigma = 0.25` states the within-die scatter in multiples of that limit. Conflating the two limits would turn ordinary process control into a yield cliff: at a limit of one tolerance the null world's own 95th-percentile edge reading already sits outside spec, and a healthy fab would lose most of its edge die.

**6. A bin is a symptom; the cause is hidden.** A dead die's `bin_code` is *drawn* through a declared row conditioned on which risk took it — a defect kill bins OPEN_SHORT about 72% of the time and something else otherwise. Every cause reaches more than one bin and every bin arises from more than one cause, so `die_bins` cannot be read back into the kill model, and the loader rejects both a cause with no row and a bin no row can reach (rule D2). The cause lives in a separate `DieOutcome` record for a later truth emitter; the observable `DieBin` carries a position and a code and has no field for a cause or a killer flag.

**A calibration recorded rather than hidden.** Two constants were set against the **null world** and against nothing downstream. `killer_density_per_mm2` was calibrated per product so that realized null yield lands near each product's declared `target_yield_pct` (measured: within 1.6 points on every product across three seeds) — this is §5's "product-calibrated so E[yield] ≈ target_yield_pct", and the specification itself is never read by the engine, which a test enforces. And a new `die_kill.background` block gives each lot and each wafer its own killer density about the product mean (lognormal, mean-preserving, keyed by lot and wafer only): without it the sole wafer-to-wafer yield variation is binomial noise over a few thousand die — under a point — and §2's declared budget of 2.5–3.5 points would be missed by a factor of five. A null world that uniform makes any fault separable at a glance, which is the audited failure this design replaces. Realized: wafer σ 1.1–4.4 points, lot-to-lot σ 0–2.9 points, product means within 1.6 points of specification.

**What 3E does *not* deliver, stated plainly.** The chain is continuous and monotone — an edge-uniformity activation raises `cd_nm_edge`, which raises the parametric risk of the die at large radius on the wafers that chamber processed, verified by counterfactual subtraction and monotone in severity across three levels. Its *magnitude* on the baseline world at the seeds measured is small: the affected chamber's outer-fifth parametric risk rises from 0.0065 to 0.0115 between null and `obvious`, and the resulting within-product cohort yield deficit stays under a point — far below A9's 4–10 point expectation. Two reasons, both identified and neither fixed here: at this seed the affected chamber's benign radial offset partly *opposes* the activation (the signed-latent consequence ADR-018 already records), and the parametric channel is a small share of a kill budget the background density dominates. Moving a constant to make that number larger is the tuning the gate forbids (`PHASE_1_ACCEPTANCE.md` A9, ADR-018 §4), so it is reported as a calibration question for the scenario-library gate.

## ADR-022 — The generator version is bound to what the generator produces, and 0.5.0's double meaning is recorded rather than renumbered
**Status: Accepted (2026-08-09), from the post-3E integrity gate.** `FABSIM_DESIGN.md` §7 has always said that *any change which can alter emitted bytes for a fixed (config, seed) bumps at least the minor version*. The gate asked whether the rule was being kept, and it was not. Four decisions.

**1. The breach, measured rather than asserted.** The trees at `188bf43` (Step 3D) and `02aed8d` (the ADR-020 recovery correction) were both built and run against the same reference config, world and seed:

```
tree                                     version   build_fingerprint   realization
188bf43  Step 3D                          0.5.0     068b95ca…           6bcc9296…
02aed8d  the ADR-020 recovery correction  0.5.0     068b95ca…           5b183b63…
```

Two semantically different generators, **one identity**. That is exactly the failure the build fingerprint exists to prevent, and it answers the gate's question "could two semantically different generators accidentally claim the same identity?" with *yes, and it already happened*. What kept it harmless is that no dataset had been emitted from either tree, so no manifest, no truth file and no benchmark result ever carried the false claim. The window in which that stays true closes the moment the emitter lands.

**2. `0.6.0` stands; there is no renumbering.** The tempting repair is to make 0.6.0 mean "the recovery correction" and 0.7.0 mean "Step 3E", so the version history has one entry per semantic change. It is rejected. A version identifies *an implementation that produced a dataset*, not a count of commits: no dataset exists at 0.6.0 either, so renumbering would move a label that labels nothing, and it would leave 0.6.0 pointing at a tree no artifact references. `0.6.0` already denotes exactly one implementation — the current one — and its changelog entry already says it carries both changes. History is not rewritten (the commits stand as they are), and the smallest honest correction is to record what happened and stop it recurring.

**3. The rule is now mechanically enforced, because a version number is a string somebody has to remember.** `tests/fabsim/test_generation_identity.py` holds `REFERENCE_BUILDS`, a table mapping a generator version to the digest of what that generator produces on a fixed reference build — null and faulted, across every plane, with the world digest and schema version folded in. Changing generation without changing the version means overwriting an entry *keyed by a version number*, which a reviewer can see; changing the version without changing generation costs nothing. Verified by mutation: reinstating the pre-ADR-020 recovery arithmetic — the precise change that shipped unnoticed — fails the tripwire, as does a ten-fold change to the defect halo.

  `0.5.0` is deliberately **absent** from the table. It named two different generators and no single digest would be honest about it; the gap is the record. The digests do not replace the physical invariant tests in the other modules — those say the physics is *right*, this says the physics has not *moved* without anybody noticing — and the one repair that is never correct is silently overwriting the digest under the current version.

**4. The identity model itself needed no change.** Traced end to end and re-verified: `build_fingerprint` = SHA-256 over (`config_sha256`, `world_sha256`, `seed`, `fabsim_version`, `schema_version`), each dimension independently tested to move it, with `world_sha256` required rather than defaulted. `dataset_id` stays opaque and stays a (scenario, seed) pair, so a different world or a different generator is a different *build* of the same dataset id — which is why the fingerprint has to carry them and does. The world digest ignores prose, key order, indentation, byte-order marks and the environment, and a new structural test now requires that **every** non-documentation top-level template key moves it, so a block added by a future slice cannot be declared, loaded and then quietly omitted from the identity it belongs to. The defect was never in the identity model; it was in nothing watching the version.

*One consequence worth stating.* Because the tripwire folds in `world_sha256`, a world-template edit now also demands a new `REFERENCE_BUILDS` entry. That is deliberate and correct under §7 — a template edit changes emitted bytes for a fixed (config, seed) — and it makes the world and the generator version move together rather than one silently outrunning the other.

## ADR-023 — The emission layer: two planes, two emitters that do not know each other, and a serializer that adds nothing
**Status: Accepted (2026-08-09), implementing the Phase 1 emission gate.** `fabsim.emit` turns one realized world into an observable dataset, a hidden truth artifact and a manifest (`src/fabsim/emit/`, `src/fabsim/selftest.py`). This is where every earlier plane's answer-blindness either survives contact with a file or does not, so seven decisions are recorded.

**1. The observable emitter is never handed the hidden plane, and the truth emitter never writes the observable one.** `project(timeline, observations, defects, die, alarms, …)` takes five *observable* collections; there is no `Realization` parameter, so latent trajectories, mechanism records, distractors, the counterfactual, the hidden defect origin and the die kill cause are unreachable rather than merely unwritten. The two emitters do not import each other — an AST test pins both directions — so "the truth cannot enrich the dataset it sits beside" is a property of the import graph. This is the same shape ADR-021 gave the die plane, applied one layer out: the boundary is the signature.

**2. The emitter is a serializer, not a model.** It draws no random numbers, computes no physics and makes no causal judgement. Two things follow that are worth stating separately: a build is reproducible partly because the emitter contributes no entropy, and the emitter cannot become the place a fault reaches an observable, because it has nothing to reach with. The critical test is the direction pair — rewriting **every** hidden record (defect origins, die kill causes) leaves the projection byte-identical (T5), while moving one observable defect coordinate changes the content hash (T6), so T5 is not passing because the projection is inert.

**3. Three orders, each stated where it is used.** `SCHEMA_TABLES` is §2's own numbering and belongs to the document; `INSERT_ORDER` is dependency order and is what both the database and the `.sql` dump are written in; the content digest uses plain **name** order so it cannot inherit either. They are genuinely different — §2 numbers `products` (2.2) before the `process_flows` (2.3) it references — and conflating them is not theoretical: the first dump this gate produced could not be replayed into an empty database, and the test that replays it and compares the content hash is what found that.

**4. Identity is the row-level content hash, exactly as A1 §2 specifies.** Tables in name order, rows in primary-key order, columns in schema order, values type-tagged (integers exact, floats by shortest round-trip repr, NULL distinct from the empty string, text in NFC). The `.sql` dump is written by this package rather than by `sqlite3.iterdump()`, whose statement order follows `sqlite_master` and is therefore a property of the storage engine; it is byte-comparable, and so is `truth.json` (canonical JSON). `fab.db` bytes are compared nowhere, which is what A1 already decided and this implements.

**5. Two schema fields are omitted rather than invented, and both are recorded here.**

  * **`lots.priority`** (§2.11). In v1 it was `random.choice(["HOT","STANDARD","STANDARD","STANDARD","LOW"])` — causally inert, like the operator dimension. The FabSim timeline models no lot priority: releases are a cadence and scheduling is availability-driven (`TEMPORAL_MODEL.md` §2), so nothing realized exists for the column to hold. Drawing one in the emitter would put entropy in the serializer and make it a generator, which decision 2 forbids. It belongs to a timeline slice if a scenario ever needs it, and the column is absent until then.
  * **`affected_wafers[].expected_mechanism_share`** (`GROUND_TRUTH_CONTRACT.md` §3). A qualitative bucket ("high"/"low") that nothing in the realization computes. The measured `exposure` emitted beside it carries the same information as a number — the share of a wafer's runs that were on an affected chamber while the activation was live — and a truth file whose fields are partly measured and partly guessed is worse than one that is smaller and entirely measured.

  `maintenance.description` (§2.18) is the opposite case and *is* emitted: the contract says "templated from action codes shared across tools", so it is `"<maint_type> <action_code>"` — identical for every window with the same two coded values, which means it adds no information those columns did not already carry and therefore cannot leak any. A test asserts that one-to-one property rather than the string.

**6. Truth records the realization, never the intent.** `severity` stays the configured label and `severity_realized` is what the latent plane actually produced (measured 4.35σ against a configured `obvious`); affected runs and wafers are the ones that really happened at this seed; `alarms_emitted` is filtered by the hidden `kind == "condition"`, which only the truth side can see; `recovery_fraction` is the realized `LatentReset`; and `expected_impact` is computed from the kill model's own output **within product**, because products differ by up to ten yield points and a raw cohort mean would mostly measure which products were in the cohort. `causal_chain` is *derived* from the world's declared sensitivities rather than narrated, so it cannot disagree with the physics it describes. The distractor list leads with the standing benign offsets rule F11 puts on every chamber of every world — the ones nobody declared and a diagnosis engine is most likely to accuse — because listing only the declared ones would make false-attribution scoring look complete while missing the common case.

**7. The self-test is stage 7 of the pipeline, and it reads the observable plane only.** `SCHEMA_V2_DESIGN.md` §4's four families, checked on every build, failing it rather than shipping. Two of them the database already enforces (foreign keys are declared and `PRAGMA foreign_key_check` runs at write time), so what is left is the half SQLite cannot express: relationships between values, the clock, the sums and the closed vocabularies. It is deliberately not given the realization — a checker that consulted the thing that produced the dataset would be confirming the dataset against its own author rather than against the contract. Mutation-verified across all four families.

*Version consequence.* `fabsim` moves 0.6.0 → 0.7.0 although **generation is byte-for-byte unchanged**. A dataset's `fabsim_version` has to identify the code that *wrote* it as well as the code that realized it — the manifest, the content hash, the dump and the truth file are all products of `fabsim.emit` — and two trees claiming one version while emitting differently would be ADR-022's defect in a new place. The reference-build tripwire now digests the emitted observable plane as well as the five generation planes, so it covers both halves.

*What the manifest does and does not say.* The five reproducibility inputs, their `build_fingerprint`, the `content_sha256`, per-table row counts and a SHA-256 per emitted file — including `truth/truth.json`, because a hash is provenance rather than disclosure: it lets a benchmark prove the hidden plane was not edited between generation and scoring without putting a byte of it in the observable directory. No scenario name anywhere (rule D5); `created_at` is the only wall-clock value in the pipeline and is in no hash, so two builds of one dataset differ in exactly that field.

## ADR-024 — The evaluation plane: a third actor, a truth validator, and three verdicts instead of two
**Status: Accepted (2026-08-09), implementing the Phase 1 benchmark/evaluation gate.** `src/fabeval/` scores a generated dataset against `PHASE_1_ACCEPTANCE.md` A1–A11 and `ANTI_LEAKAGE_DESIGN.md` L1–L11. Six decisions.

**1. The evaluator is a third actor, not a part of either plane.** ADR-013 gave `fabsim` write access to both planes and `fabops` read access to one; scoring needs both, so the scorer is neither. `fabeval` reads the observable dataset *and* `truth.json`, joins them on `dataset_id`, and **writes nothing** — a test asserts the package calls no `write_text`, `write_bytes`, `mkdir`, `unlink` or `commit`, because a grader that could write into a dataset could contaminate the thing it grades. The one function that causes writing, `build_library`, does so by calling the emitter into a caller-supplied root.

**2. It lives at `src/fabeval/`, and the design documents' `eval/` means this.** Four documents name the directory `eval/` and the *role* is exactly what they describe. The location differs because this repository settled on src-layout installed packages after the audit's P0 defect was precisely a non-installable source folder; a root-level `eval/` would need the `sys.path` manipulation ADR-009 removed. `eval` is also a builtin name. The read-discipline table in `GROUND_TRUTH_CONTRACT.md` §4 is unchanged in substance: the row that said `eval/` now says `fabeval`.

**3. Three verdicts, not two.** `PASS`, `PARTIAL`, `BLOCKED`. Several criteria are genuinely part-testable and saying PASS would make the matrix a worse instrument than no matrix: A1's fourth check is a CI reference-image job, A9's checklist ends in a manual wafer-map review, A6 asks for a severity sweep this gate does not build. A criterion is PASS only when everything it asks for was measured. On the library today: **A2, A4 and A10 are PASS; A1, A3, A5, A6, A7, A8 and A11 are PARTIAL; A9 is BLOCKED** on the cohort yield deficit ADR-021 already explains.

**4. The reference queries are observable-only, by signature.** Every function in `fabeval.queries` takes a path to `fab.db` and there is no parameter through which truth could arrive — a test pins the parameter lists and scans the module for any hidden-plane identifier. This is not fastidiousness: L7 runs the same queries on the null that L11 runs on a faulted dataset, and if the queries could see truth the two would not be comparing the same thing. They also never read `classified_type` for geometry, because it is a noisy draw over the hidden origin (ADR-019 §4) and trusting it would quietly restore the circularity the audit found.

**5. The expectation table is separate from the checks, and marks corroborating channels as corroborating.** What each scenario should show is declared once in `fabeval.fixtures` — a check that decided for itself what "recoverable" means could always be satisfied. Expectations are relational (which chamber ranks where, above which leave-one-out σ floor), never golden values.

  One entry is worth recording. Scenario B declares three channels and only two are *required*. ADR-018 records that `edge_uniformity` is signed, so whether a fault reinforces or opposes a chamber's own benign radial offset is a per-seed coin flip — and the edge-*defect* channel reads `|edge_uniformity|`. Measured across B's three A2 seeds: metrology ranks the planted chamber 1st every time (z = +2.65 / +1.46 / +2.15) while defect share ranks it 1st, 3rd and **6th** (z = +2.34 / +0.08 / −1.54). The first version of this table required all three to lead and failed at two of three seeds. Requiring it would be requiring a guarantee the physics does not give; the channel is now measured, reported, and unable to fail the scenario alone. The simulator was not touched.

**6. Two evaluator defects found and fixed during the gate, both in the evaluator.** Recorded because §9 of the gate asks that a failure be diagnosed before anything is changed, and in both cases the answer was "the check is wrong", not "the simulator is wrong".

  * **L5 was measuring nothing.** It compared an origin's *name* against a class's and called the difference "disagreement", reading 0.96 on a perfectly honest classifier — origins and classes are different vocabularies (`edge_ring` is called PATTERN 62% of the time and there is no EDGE_RING class). The design's "5–15%" is a *confusion* band and confusion is only defined against a declared row. L5 now compares the realized per-origin class distribution against the world's own confusion matrix, and additionally requires every class to arise from more than one origin.
  * **The arc check could pass on a coincidence.** It took the first alarm at or after onset from the observable plane, which can pick a background false alarm followed by a pre-existing breakdown and call the pair an arc. It now takes the *causal* references from truth — `alarms_emitted` is filtered on the hidden condition kind, `maintenance_response.maint_id` names the window the escalation earned — and reads their **timestamps out of the observable tables**, so what is asserted is that the dataset an analyst receives really shows the ordering.

*What this gate deliberately did not do.* No diagnosis, no scoring of a diagnostic output, no suspect ranking. The reference queries compute engineering quantities and rank chambers by them; they do not weigh evidence or combine channels. A grader that grew an opinion could not grade the thing that has one, which is the whole reason the benchmark is built before the engine rather than after.

## ADR-025 — A9's target is unreachable and self-contradictory; A6's recovery half fails against a measured floor; diagnosis stops at the design gate
**Status: Accepted (2026-08-09), from the A9/A6 review gate.** No simulator code was changed. Five measured findings and one decision about what happens next.

**1. Scenario B's chain is intact; the effect is small because two independent calibrations compose badly.** Traced by counterfactual subtraction on an identical timeline — the same wafers, the same die grid, the mechanism removed and nothing else:

```
latent      +2.51 sigma_ref post-onset departure on the chamber
process     cd_nm_edge signed d/L  +0.003 -> +0.464 tolerances
            cd_nm_mid                      -> +0.304   (half, radial_weight 1.0)
            cd_nm_center           +0.146  -> +0.146   (exactly unchanged)
defects     +17 on the exposed cohort; edge share +0.0083
die         outer fifth p_param 0.00657 -> 0.00917;  p_defect +0.00024
            p_background delta +0.00000000   (no leak into the background)
yield       exposed -0.058 pts;  every other wafer +0.0000 pts exactly
```

Every stage is non-zero, correctly signed and correctly localized — the centre zone moves by *exactly* zero, the background risk by *exactly* zero, and the unexposed wafers by *exactly* zero. **There is no implementation defect.** What limits the magnitude is composition: ADR-018 §4 deliberately did not amplify the observation transfer, so a moderate fault is a +0.46-tolerance edge shift; ADR-021 §5 set the functional kill limit at 3.0 control tolerances on the physical argument that a control limit is where a fab intervenes. Both are individually defensible. Together they put the die model 2.5 tolerances into a Gaussian tail, where a 0.46-tolerance push buys 0.0003 of kill probability. Neither ADR checked the composition against A9, because A9's checklist predates the die plane.

**2. A9's 4–10 point band cannot be reached by the one constant that governs it.** Recomputing the parametric kill off the already-emitted metrology under hypothetical functional limits — a measurement, with nothing modified:

| limit (tolerances) | null world parametric loss | scenario B cohort deficit |
|---|---|---|
| 3.0 (current) | 0.46% | −0.13 pts |
| 2.0 | 2.18% | −0.29 pts |
| 1.5 | 4.45% | +0.52 pts |
| 1.2 | 6.65% | +1.56 pts |
| 1.0 | 8.91% | +2.26 pts |

At a limit of 1.0 the *null* world loses 8.9% of its die to parametric kills — an absurd healthy fab — and scenario B still reaches only 2.26 points, short of the 4-point floor. The band is unreachable at any setting, and the reason is not the limit: the null's own edge |d|/L (mean 0.439, p95 1.403) and the exposed cohort's (mean 0.492, max 1.832) overlap almost entirely. Any limit low enough to kill B's edge die kills the null's at nearly the same rate. **Tuning cannot fix this and was not attempted.**

**3. A9 contradicts itself, and the number it pins is the defect the architecture removed.** A9's prose says the criterion is "statistical equivalence and demo continuity, not reproduction of the legacy numerical outputs", and ADR-010 says the same. Its checklist then pins "deficit in 4–10 pts" — and that figure comes from the audited v1, where the yield formula carried `−0.08 if bad_tool` and the audit found "8.0 of ETCH-02's ~12 yield points are a direct label effect". Requiring FabSim to reproduce 4–10 points is requiring it to reproduce the magnitude of the term ADR-004 exists to abolish. The mediated remainder of the legacy cohort gap was roughly 4 points — the very bottom of the band — and FabSim produces 0.058 points of *mechanism-attributable* effect.

  A9 therefore stays **BLOCKED**, and it is recorded here as an acceptance-document contradiction requiring a decision rather than an engineering fix. Three options exist and none of them is this gate's to take: restate the checklist item in mediated terms with a band derived from the current physics; keep the band and accept A9 as permanently unmet on the baseline world; or change the world's severity calibration, which would move every dataset and needs its own gate. **No constant was moved and no penalty was added.**

**4. A6's severity sweep now runs, and its "recovery" half fails against a properly measured floor.** `fabeval.sweep` was added: the reference queries over one scenario at each rung, read against the *natural-variation floor* — the worst standing any chamber reaches on worlds with nothing wrong.

```
                realized   edge_cd  edge_share    alarms  yield_split
subtle             1.61      +1.88       +2.09     +2.14        +1.16
moderate           3.22      +2.65       +2.34     +4.34        +1.25
obvious            4.00      +3.02       +2.22     +3.97        +1.26
null floor (3 seeds)         2.84        3.29       5.05         2.26
```

The difficulty axis exists — realized severity rises 1.61 → 3.22 → 4.00 and edge-CD and yield-split rise with it. But **at moderate the planted chamber does not exceed the floor on any single channel**. Ranking first is not separation: on a null world some chamber always ranks first, and it does so at a comparable sigma. A6 stays **PARTIAL** with that as its measured reason.

  This is the same fact as finding 1, seen from the analyst's end, and it is *by design*: rule F11 puts benign offsets in the subtle-severity band and states that a fault and an offset differ "only by shape in time". The evidence that exists is multi-channel and temporal, which is the diagnosis engine's problem, not a reference query's.

  **A caution recorded because this gate nearly made the mistake.** The first version of the floor was read from a single null world, and it reported A6 as **PASS** — seed 42's floor happens to sit at 2.31 on edge-CD, below the moderate fault's 2.65. Three seeds put it at 2.84 and the PASS evaporates. `natural_variation_floor` now refuses fewer than three realizations rather than returning a number that flatters whatever it is compared against, and a test pins the refusal.

**5. Sampling the null properly broke L7, and A7 drops to BLOCKED.** Building the null at three seeds for finding 4's floor had a consequence the gate did not go looking for: `l7_null_blindness` fails at seeds 101 and 2024 — ETCH-02/A reaches **3.29σ** on edge-defect share, ETCH-03/A reaches **2.84σ** on edge CD — and passes only at seed 42, the one seed the library had ever built. A second one-draw problem sat behind it in the evaluator: `evaluate` assembled A7's verdict from the seed-42 rows alone, so the suite's results at every other seed were computed and then thrown away. A7 could not have reported this even if the datasets had existed. Both are fixed; the fix is to *score more*, and nothing was relaxed.

  Measuring L7's floor instead of accepting its hardcoded 2.5 makes the finding worse, not better. The design words the criterion as "all effect sizes below the subtle-severity floor", and a subtle fault's planted chamber reaches 1.88 / 2.09 / 1.16σ on the three channels. **On two of three fault-free worlds a benign chamber stands out more than a subtle fault does.** So L7 fails on its own wording, not merely against a stand-in constant.

  This is the third sighting of one fact — findings 1 and 4 are the other two — and the open question it raises is now sharp enough to state: the benign chamber-to-chamber spread the world carries by design (F10/F11) may be larger than the subtle rung was calibrated to sit above. That is a world-calibration decision that moves every dataset, and like A9's it belongs to a gate of its own. **The floor was not raised, the world was not retuned, and the null was not put back to one seed** — the last of those would have restored the green by unlearning the measurement. `test_l7_fails_on_the_null_at_two_of_three_seeds` pins it in both directions: a third failure, or a failure anywhere but L7-on-the-null, still breaks the suite.

**6. Diagnosis stops at the design gate, because its contract did not exist.** ADR-003 states the rule, ADR-005 that evaluation gates the claim, ADR-007 that statistics precede ML, ADR-008 names an output artifact — but nothing said what the engine is handed, what it returns, or how a conclusion is scored. `docs/design/DIAGNOSIS_CONTRACT.md` now says: the entry point takes a **path to `fab.db`** and nothing else (a dataset directory would put `truth/` one join away); the output is a ranked set of candidates each carrying falsifiable evidence, a mandatory `considered[]` of rejected hypotheses, and `insufficient_evidence` as a first-class answer; five static checks and one runtime invariance test (rewrite every hidden record, leave `fab.db` byte-identical, the report must not move) are specified. Five open decisions — the score's definition, the abstention threshold, the onset statistic, the artifact's schema name, and where the package lives — are recorded rather than guessed, because guessing them is the architecture-invention this gate forbids. *(All five are closed by **ADR-029**; the contract's §8 keeps them with their answers.)*

*What changed in code.* `fabeval` gained `sweep.py` and a sweep-aware `check_a6`; `build_library` now builds the null at three seeds so the floor has more than one draw; `evaluate` scores the leakage suite on every row rather than on seed 42's. Nothing under `src/fabsim/` or `src/fabops/` was touched, no threshold was moved, and no check was relaxed. Two criteria moved, both downward and both on measurement: **A6 stays PARTIAL with a measured reason instead of an unbuilt one, and A7 drops from PARTIAL to BLOCKED.**

*Superseded in part by ADR-026.* Findings 1, 2 and 3 stand. Findings 4 and 5 stand as *measurements* and are wrong in their *diagnosis*: the open question §5 states — whether this world's benign chamber-to-chamber spread is larger than the `subtle` rung was calibrated to sit above — is measurably answered **no**, and the comparison that raised it turns out to be between a maximum and a point. ADR-026 records what A6's floor and L7's threshold actually compute.

## ADR-026 — A6's floor and L7's threshold measure an order statistic, not the fab; the severity ladder is calibrated and A9's band is unreachable through the channel that has to carry it

**Status: Accepted (2026-08-09), from the benchmark validity / calibration resolution gate.** ADR-025 closed with one open question and called it a world-calibration decision needing its own gate. This is that gate. **No simulator code was changed, no threshold was moved, no acceptance criterion was rewritten, and no verdict was upgraded** — because the answer is that there was nothing in the simulator to change. Seven findings.

**1. What A6's floor and L7's threshold actually compute.** Both reduce a fault-free world to `max over chambers of |leave-one-out z|`, and both compare it against a number that means *one chamber's standing*:

```
fabeval.sweep.natural_variation_floor   max over null seeds ( max over chambers |z| )
                                        ...compared against the planted chamber's z at moderate
fabeval.leakage.l7_null_blindness       max over chambers |z|, per null
                                        ...compared against a hardcoded 2.5
```

A maximum over *N* exchangeable draws exceeds a single draw by an amount that is a property of *N*. Neither comparison is a statement about the fab, and both would return the same verdict on a simulator of any quality.

**2. The reference distribution, computed rather than argued.** For seven exchangeable chambers — the count every etch-grain reference query reports at — under the leave-one-out z these queries use:

```
E[max|z|] 2.995    median 2.687    p90 4.347    p95 5.180
P(max|z| > 2.50) = 0.598     <- L7's threshold
P(max|z| > 2.65) = 0.519     <- scenario B at moderate, on edge CD
P(max|z| > 2.84) = 0.429     <- the three-seed floor A6 was read against
```

L7 evaluates three such channels and fails if any trips, so its expected failure rate on a **correct** null world is near 0.9. Measured over twelve fault-free worlds: **L7 fails on 10 of 12** (83%). The 2-of-3 ADR-025 §5 reported is the modal outcome of a healthy fab, not evidence about one.

**3. The null world is not over-dispersed; it is exchangeable to three decimal places.** Pooled per-chamber |z| over twelve null realizations, against the exchangeable-Gaussian reference:

```
                     measured mean   reference   measured p90   reference p90
edge_cd                  1.129         1.123         2.28           2.38
edge_defect_share        1.124         1.123         2.27           2.38
yield_split              1.084         1.123         2.15           2.38
alarms  (n = 17)         0.891         0.895         1.59           1.86
```

If anything this world is marginally *quieter* than perfect exchangeability. There is no excess benign structure to find, and ADR-025 §5's hypothesis is false. Its sentence "on a fault-free world a benign chamber stands out more than a subtle fault does" is true of every possible world, including one with no benign variation at all, because it compares a maximum to a point.

**4. There is no calibration lever, and that is provable before it is measured.** `zscore` standardizes by the realized between-chamber spread, so it is invariant under any common positive rescaling *and* any shift of the scores; F10/F11 make the chambers exchangeable; therefore the null distribution of `max|z|` depends on the chamber count and the shape of the per-chamber statistic and on **no magnitude the world declares**. Measured to confirm it — eighteen fault-free worlds over six world calibrations, spanning 16× in the benign latent offset and 20× in the observation-plane chamber offset:

```
world calibration                    L7 failures / 3 null seeds
baseline                                      2
benign latent offsets       x0.25             2
benign latent offsets       x4                3
observation chamber_offset  x0.2              2
observation chamber_offset  x4                3
severity_reference          x2                2
```

A fifth of the declared benign chamber variation fails exactly as often as the baseline; fourteen of the eighteen worlds fail. The parameters ADR-025 named as candidates — benign offset scale, chamber variation scale, severity reference — cannot move these criteria, and neither can any other. **This is why nothing under `src/fabsim/` was touched: not restraint, arithmetic.**

**5. Read against a reference that converges, the severity ladder is calibrated as designed.** The like-for-like comparison is the planted chamber against the null's own *per-chamber* distribution — one specified chamber against unspecified single chambers. Tail probability of the null per-chamber |z| at or above the planted chamber's standing (84 chamber-seeds on the etch-grain channels, 219 on alarms):

```
                  edge_cd   edge_defect_share   alarms   yield_split
subtle  (1.61 s)   0.167          0.143          0.059      0.429
moderate(3.22 s)   0.060          0.095          0.018      0.405
obvious (4.00 s)   0.036          0.131          0.023      0.405
```

`CAUSAL_MECHANISM_MODEL.md` §8 defines `subtle` as "near the detection floor" and `moderate` as "detectable with competent statistics". Subtle sits at p ≈ 0.14–0.17 on the channels the mechanism drives; moderate reaches p = 0.060 on edge CD and p = 0.018 on alarms, monotone in severity on both. **That is the specified ladder, met.** The world does not need recalibrating, and A6's difficulty-axis half was never the half in doubt.

Yield is the exception, and it is a real physical result rather than a measurement artefact: the planted chamber's yield split sits at p ≈ 0.41 at every rung and does not move with severity at all. §7 is why.

**6. A6's floor cannot converge, so the verdict it yields is a function of the evaluator's budget.** The floor is a cumulative maximum: it rises monotonically with the number of null realizations and has no limit. Measured on the real world as null seeds are added:

```
null seeds            1      3      5      8     12
edge_cd             2.31   2.84   5.38   5.38   5.38
edge_defect_share   1.82   3.29   3.29   4.08   5.24
alarms              2.50   5.05   5.05  13.08  13.08
yield_split         2.26   2.26   2.53   3.02   3.59
```

ADR-025 §4 raised `MINIMUM_FLOOR_SEEDS` from 1 to 3 because a one-seed floor had reported A6 as PASS, and recorded the refusal as a guard against a floor "that would flatter whatever it is compared against". The guard is right about the direction and wrong about the cure: three draws do not stabilise a divergent statistic, they move it one step along a sequence with no limit. `separated_at_moderate` is empty at three seeds, empty at twelve, and would be empty at any larger number for a simulator of any quality. **The number was not lowered and the seed count was not reduced** — either would restore a green by unlearning the measurement, which is what ADR-025 rightly refused.

  The divergence is in the *aggregation across seeds*, not in the per-world statistic, which is worth stating because it narrows what the next gate has to decide. Taking the **same** per-world maxima and averaging them instead of maximizing over them is stable at every budget, while the maximum runs away:

```
seeds per floor        3      12      48
max  (as implemented)  3.98   5.54    7.56
mean                   3.01   3.01    3.02
```

That does not make `mean` the right answer — a mean of maxima is still a maximum-flavoured quantity, and §5's per-chamber reference is the more defensible reading of "the natural-variation floor". It does show the floor's instability is a choice rather than a property of the fab.

**7. A9, verified independently, with an upper bound that needs no sweep.** Re-probing the realized world under hypothetical functional limits reproduces ADR-025 §2's conclusion and strengthens it. The measurement that settles it needs no hypothetical at all — the hidden `DieOutcome` causes on the null at the current limit:

```
null_baseline@42, 897,725 die     pass 87.440 %   background 11.127 %
                                  defect 0.634 %  parametric  0.799 %
```

**The parametric channel's entire share of a healthy fab's die loss is 0.80 yield points.** A9 asks for a 4–10 point *incremental* within-product cohort deficit through that channel. A fault that killed every parametrically-vulnerable die in its cohort and left the control untouched could not reach one point. The band is over-subscribed by a factor of five before any question of tuning arises.

The full re-probe (each row redraws every die, so each carries ≈ ±0.4 pts of cohort sampling error) confirms it and shows what reaching for the band would cost:

```
limit (tolerances)   null parametric loss   null yield   B cohort delta   B yield
3.0 (current)               0.799 %           87.43 %       +0.428 pts    87.42 %
2.5                         1.772 %           86.63 %       +0.702        86.60 %
2.0                         4.134 %           84.70 %       +1.363        84.59 %
1.5                         8.590 %           81.09 %       +1.586        80.75 %
1.2                        12.284 %           78.16 %       +0.258        77.48 %
1.0                        15.783 %           75.31 %       -1.914        74.27 %
```

Positive is a *surplus*: the exposed cohort out-yields its within-product peers at every setting down to 1.2, and only at 1.0 — where a healthy fab has lost 15.8% of its die and yields 75% — does a deficit appear at all, reaching 1.9 points against a 4-point floor. The as-built cohort delta is **+0.4281 pts**, matching the truth artifact's own `expected_impact` exactly, and it is *flat across the ladder* (+0.466 / +0.428 / +0.409 at subtle / moderate / obvious) against a cohort standard error of 0.11–0.46 points. The yield channel carries no severity information at these magnitudes; it is measuring the chamber's benign character and nothing else.

  **The documentary contradiction, stated once more with its provenance.** The band is not A9's alone: it appears as "4–10 pts" in `PHASE_1_ACCEPTANCE.md` A9, and as "≈ 4–8 pts" in `CAUSAL_MECHANISM_MODEL.md` §8 and `SCENARIO_SPECIFICATION.md` §4 B. All three trace to `docs/audit/SYNTHETIC_DATA_AUDIT.md` #5, and that entry contains the decisive number: *"of ETCH-02's ~12-pt deficit, **8.0 pts are this direct label effect, only ~3.7 pts flow through defects**."* The audited v1's own **mediated** remainder was 3.7 points — below the band's own 4-point floor. The band was therefore never reachable through mediation *in the system it was measured from*; it is the magnitude of the `−0.08 if bad_tool` term ADR-004 exists to abolish, and requiring FabSim to reproduce it is requiring the defect back.

**What this gate changed, and what it deliberately did not.** Changed: this ADR, the status paragraphs in the five design documents the findings touch, and one new test module (`tests/fabeval/test_floor_semantics.py`) that pins the reference distribution, the divergence, the scale-invariance and the null's exchangeability — arithmetic that needs no dataset, so the finding cannot be lost between gates. Not changed: `src/fabsim/`, `src/fabops/`, `src/fabeval/`, every threshold, every world constant, every verdict. **A6 stays PARTIAL, A7 stays BLOCKED, A9 stays BLOCKED.**

Correcting A6 and L7 means choosing what they should compare instead, and that is an architecture decision this gate is not entitled to take — the same discipline ADR-025 applied to A9. The options, recorded so the next gate chooses rather than invents:

1. **Score the planted chamber against the null's per-chamber distribution** (§5's table), which converges, is like-for-like, and is what "effect size below the subtle-severity floor" means when both sides are single chambers. Needs a declared quantile and a declared minimum null sample.
2. **Score maximum against maximum** — the family-wise form: a faulted world's worst chamber against the *distribution* of a null world's worst chamber. Answers a different and also legitimate question ("would an analyst who does not know where to look find it?"), and is strictly harder.
3. **Restate L7 as a false-positive rate over a null population** rather than a per-world assertion, which is what the leakage taxonomy's T5 actually asks for.

A9's three options are unchanged from ADR-025 §3, with one addition this gate's §7 supports: any restatement of the band should be derived from the parametric channel's measured budget rather than from the legacy cohort gap.

**Diagnosis remains blocked.** *(Unblocked by **ADR-029**.)* `DIAGNOSIS_CONTRACT.md` §5's measured warning was read off the defective comparison and is corrected there: an engine ranking chambers on one statistic does **not** score at chance on the null — moderate reaches p = 0.018 on alarms and p = 0.060 on edge CD. The contract's *conclusion* is unaffected and better supported: the evidence is multi-channel and temporal, no single channel is decisive, and §8's five open decisions still have to be made before an engine is written.

## ADR-027 — The null reference distribution: one derived object, three criteria read against it; A9's legacy band retired as binding

**Status: Accepted (2026-08-09), from the A6/A7/A9 architecture decision gate.** ADR-026 measured that A6's floor and L7's threshold could not work and recorded three options rather than choosing one, because choosing was an architecture decision. This gate makes the choice. **No `src/fabsim/` change, no world constant, no simulator tuning** — ADR-026 §4 proved no such lever exists, and this gate re-confirmed it before touching anything.

**1. The decision, in one sentence.** The three criteria that ask "is this chamber's standing larger than benign variation produces?" now read against **the distribution of that statistic under the null hypothesis they are about**, derived from exchangeability and from the chamber count, in `src/fabeval/reference.py`. What each criterion asks of it differs, because the questions differ — and that difference is the whole of the design.

```
per_chamber   one *specified* chamber.  A6 asks this: truth names the planted
              chamber, so the comparison is one chamber against single
              benign chambers.
per_world     the *worst* chamber.  L7's guard asks this: an analyst facing a
              fault-free world does not know which chamber to excuse.
```

The reference is **external to every dataset** — it comes from the chamber count and from F10/F11's exchangeability and from nothing any dataset contains. That is what keeps it from being the tautology ADR-026 §4 warned of: a threshold fitted to the nulls it judges cannot fail. Whether the simulator's nulls actually match it is then a real question, and `l7_null_calibration` is the check that asks it.

**2. L7 — the options, and why the chosen one.** The gate weighed four.

| | what it measures | preserves T5 | seed-count invariant | needs simulator change | leakage risk |
|---|---|---|---|---|---|
| **A** empirical null quantile | fault evidence against a quantile of the nulls | no — it scores the *fault*, which is L11's job, and a quantile fitted to the same nulls it judges is circular | yes | no | none, but vacuous |
| **B** within-world matched control | affected against contemporaneous healthy chambers | partly — a null has no affected chamber, so the form does not apply | yes | no | none |
| **C** effect-vs-null distribution | null worlds against subtly-faulted worlds | yes | yes | no | none |
| **D (chosen)** derived reference, two clauses | a per-world action limit **and** a population exceedance rate | yes, in both failure directions | yes | no | none |

**A** was rejected as circular and as answering L11's question rather than L7's. **B** does not have a form on a fault-free world, which is the only world L7 runs on. **C** is genuinely defensible and was the closest runner-up — "a fault-free world must not look more fault-like than one at the detection floor" is exactly L7's own wording read as a comparison — but it costs a second population of subtle-severity builds for every verdict, and a single grossly poisoned null among *N* barely moves a two-sample statistic, so it would have been weaker against the mutation L7 exists to catch. **D** keeps C's insight (the reference is a distribution, not a constant) while splitting the question in two, because the two failure modes need different instruments:

  * **the per-world action limit** (`l7_null_blindness`, unchanged in shape) catches one chamber grossly out. Its level is the fab's **own** control-limit convention — 3 sigma, the multiple eight of the nine `alarms.codes` in `baseline_fab_v1` declare, i.e. a per-chamber false-alarm rate of 0.0027 — carried into the leave-one-out currency, which is **6.46** at seven chambers. The anchor is not decoration: this runs on every null dataset of every build, so it needs an *action* limit rather than a screening one, which is the same reason a real fab charts at 3 sigma and not at 2. The evaluator borrows the convention the simulated fab already declares rather than inventing one.
  * **the population calibration** (`l7_null_calibration`, cross-dataset like L8) catches structure spread thinly across many chambers, where no single world looks alarming and every world is a little out — which is what a *generator* defect would actually produce, and what no per-world threshold can see. Screening level `ALPHA = 0.05`, because this is where power matters and there is one verdict rather than one per dataset.

**3. What that measures on the world as built.** At the derived limits, over the twelve fault-free worlds ADR-026 built:

```
per-world action limit (alpha 0.0027 -> c 6.46 at n=7)
    worlds tripping any channel        0 / 12      (worst chamber seen: 5.38)
population calibration (alpha 0.05 -> c 3.04 at n=7)
    edge_cd            3 / 84          edge_defect_share  4 / 84
    yield_split        2 / 84          pooled             9 / 252 = 0.036
                                       against 0.050 expected
at the fab's stricter 3-sigma level    0 / 252     against 0.7 expected
```

The null is correctly sized, and it is correctly sized at *both* declared levels — so the conclusion does not depend on which one is chosen, which is the property a level picked to produce an outcome would not have.

**4. What the correction gave up, measured rather than glossed.** Poisoning one chamber's `cd_nm_edge` in a null database: a 30% shift reaches 19.2 sigma and fails, 10% reaches 10.8 and fails, **5% reaches 4.7 and passes**. The old 2.5 constant caught a 2% shift. That is not sensitivity this gate traded away — at 2% the old check named the *wrong* chamber, and it flagged nine healthy worlds in ten. A check that fires on the benign structure rule F11 *requires* the null to contain is not detecting anything; ADR-026 §2 measured it failing 10 of 12 correct nulls. The population calibration covers the failure mode the per-world guard cannot see, and its resolving power is reported in its own detail line rather than assumed.

**5. A6 — the same reference, the per-chamber side.** A6's sweep half is now read as an **exceedance probability**: how often a benign chamber reaches at least the planted chamber's standing. That currency converges, and unlike sigma it means the same thing on a channel read at 7 chambers and one read at 18. Measured against the declared `ALPHA = 0.05`:

```
                  edge_cd   edge_defect_share   yield_split   | alarms
subtle  (1.61 s)   0.173          0.137            0.372      |  0.061
moderate(3.22 s)   0.076          0.105            0.339      |  0.0009 *
obvious (4.00 s)   0.051          0.119            0.336      |  0.0018 *
```

Two decisions inside that table. **A6's evidence channels are the three its own text names** — "chamber-grain yield split + edge-zone defect elevation + edge-CD shift". `alarms` is carried because the criterion's temporal alignment is read off it, but A6 does not list it as evidence, so it corroborates and cannot satisfy the criterion alone; letting the strongest channel carry a criterion that never asked for it is how a benchmark quietly becomes easier. And **the difficulty axis is now checkable from both ends**: subtle must stay *inside* benign variation (it does, on all four channels) and moderate must clear it (it does not, on any of the three declared ones). A subtle rung that separated would block A6, which the old formulation could not express.

**A6 therefore stays PARTIAL, with a reason that converges.** The verdict did not move; what moved is that it is now a statement about the simulator instead of about the evaluator's budget. `natural_variation_floor` is kept and still reported as evidence — it is a real quantity — but it is no longer a threshold.

**6. A9 — the legacy band is retired as binding and preserved as a reference; the yield *item* is not this gate's to retire.** Provenance, traced end to end: the band appears as "4–10 pts" in `PHASE_1_ACCEPTANCE.md` A9 and as "≈ 4–8 pts" in `CAUSAL_MECHANISM_MODEL.md` §8 and `SCENARIO_SPECIFICATION.md` §4 B; all three trace to `docs/audit/SYNTHETIC_DATA_AUDIT.md` #5, whose decomposition of the audited v1's ~12-point ETCH-02 deficit is 8.0 points of direct `−0.08 if bad_tool` label effect and **~3.7 points mediated**. So:

  1. **What question was it meant to answer?** ADR-010 demo continuity — does the successor still tell a recognisable ETCH-02 story?
  2. **Legacy observation or architectural requirement?** A legacy *numerical observation*, promoted to a numerical target. A9's own prose says the criterion is "statistical equivalence and demo continuity, not reproduction of the legacy numerical outputs", and ADR-010 says the same.
  3. **Does the architecture forbid reproducing it?** Yes. ADR-004 abolishes the term two thirds of it consisted of.
  4. **Can the current mechanism reach it?** No, by a factor of at least 4.4 (ADR-026 §7), and the band's own floor sits *above* the 3.7 points the legacy system produced through physics.
  5. **Correct action:** **retire the numeric band as binding, preserve it as a historical reference marked non-binding.** `check_a9` reports it in the evidence at every verdict and no longer gates on it; `acceptance.LEGACY_COHORT_BAND` keeps the number so retiring it cannot become deleting it, and a test pins both halves.

  **A9 nevertheless stays BLOCKED, on the item that is actually unmet.** With the band no longer in the way, the checklist's first item fails on its own terms: the affected tool is **not** the worst etch tool on cohort yield (ETCH-03 at −0.163 pts is, against ETCH-02's −0.082). That is a *ranking* failure, not a magnitude one, and its cause is measured: the between-tool benign spread on cohort yield is **0.410 pts** against a mechanism-attributable effect of **0.058 pts** (ADR-025 §1), so which tool ranks worst on yield is decided by benign variation at every severity.

  Whether demo continuity should keep a yield item at all is therefore the open question, and **this gate does not answer it**, because both available answers have consequences no evaluator change can contain: dropping the item changes what the project's flagship demo claims, and making the item reachable means recalibrating the composition ADR-026 §1 identified (ADR-018 §4's un-amplified transfer against ADR-021 §5's 3.0-tolerance functional limit), which moves every dataset and needs its own gate. The band is retired because its provenance settles it; the item is not, because its provenance does not.

**7. A defect this gate found in its own new code, recorded rather than quietly fixed.** The first `_leave_one_out_z` computed the held-out mean and variance by subtracting from running totals — algebraically identical to the definition and *numerically wrong*: `Σx²/(n−1) − mean²` is a difference of two nearly equal large numbers whenever the spread is small against the mean, and it was measured disagreeing with `fabeval.queries.zscore` by up to **43 sigma** on heterogeneous inputs. It never reached a verdict, because the check that caught it was written before the reference was used for anything. A reference distribution of subtly the wrong shape would have mis-sized every criterion in this ADR while looking entirely healthy, so the agreement with the definition is now a test.

**8. What changed, and what did not.** Changed: `src/fabeval/reference.py` (new), `sweep.summarize`, `leakage.l7_null_blindness` + `l7_null_calibration`, `acceptance.check_a6`/`check_a7`/`check_a9`, `matrix.evaluate`, their tests, and the design documents these findings touch. **Unchanged: `src/fabsim/`, `src/fabops/`, `app/`, `data/`, `sql/`, `scenarios/`, every world constant, and the legacy v1 surfaces.** One verdict moved, on measurement: **A7 BLOCKED → PARTIAL**, because its check stopped measuring an order statistic. **A6 stays PARTIAL** and **A9 stays BLOCKED** — A9 on a different and truer item, which is a strengthening rather than a relaxation: it used to block on something provably unreachable, and now blocks on something a better-composed model could actually satisfy. **Nothing became PASS.**

**9. Diagnosis is still not authorized**, and the blocker is now precise. ADR-026 corrected `DIAGNOSIS_CONTRACT.md` §5's measurement; this gate gives §8's open decision 2 — the abstention threshold, which "must be calibrated against the null worlds, never against the faulted ones" — the instrument it lacked, since `reference.py` is exactly that calibration and is engine-independent. Four of the five open decisions remain (the score's definition, the onset statistic, the artifact's schema name, and where the package lives), and A9's yield-item question is open. Diagnosis waits on those, not on the benchmark. *(**Superseded by ADR-029.** All five are now closed, and the engine's abstention no longer reads `reference.py` at all: it calibrates against a within-dataset candidate permutation. `reference.py` remains the **evaluator's** instrument, which is what it was built for.)*

## ADR-028 — Cohort yield is a downstream consequence in the demo-continuity gate, not an attribution channel

**Status: Accepted (2026-08-09), from the final A9 architecture decision gate.** ADR-027 retired A9's 4–10 point band and left one question open: *should the flagship demo-continuity criterion contain a cohort-yield item at all?* This gate answers it. **No `src/fabsim/` change; no world constant; the yield plane, the die grid, `die_bins`, `wafer_yield`, the yield queries and truth's `expected_impact` are all untouched.** What changes is what A9 *attributes through*.

**1. The decision.** Cohort yield is **retained in A9 as reported, corroborating evidence and removed as a gating attribution criterion.** The checklist item "the affected chamber's tool is worst of the three etch tools on cohort yield" no longer gates; the number, the tool standing and the between-tool spread are printed in the evidence at every verdict. What gates the downstream half instead is that the demo's *declared* causal chain still reaches the die plane and wafer yield.

**2. Why — the measurement that settles it.** On **twelve fault-free worlds**, the worst etch tool on cohort yield is:

```
ETCH-01   4 / 12        ETCH-02   4 / 12        ETCH-03   4 / 12
```

Exactly a three-way split. **A9's item is therefore satisfied by chance one time in three on a world with no fault in it.** A criterion a null world passes a third of the time is not an attribution criterion — it is the same class of defect ADR-026 found in L7, seen from the other side. The previous two gates each found a criterion whose reference distribution had never been computed; this is the third and last of them.

**3. The grain is not the fix, and this is worth stating because it was the obvious first hypothesis.** A9 asks a *tool*-grain question about a *chamber*-grain fault, and averaging a faulted chamber's deficit with its healthy siblings' dilutes it — so one might expect chamber grain to rescue the item. It does not. Scenario B's planted chamber on chamber-grain cohort yield:

```
seed   42    rank 1 / 7   z +1.25    seed 2024   rank 6 / 7   z -0.65
seed  101    rank 1 / 7   z +2.58
subtle rank 2 / 7  z +1.16      moderate rank 1 / 7  z +1.25      obvious rank 2 / 7  z +1.26
```

At seed 2024 the planted chamber is the *sixth worst of seven* — nearly the best-yielding chamber in the fab. And the standing does not move with severity: p ≈ 0.34 against the per-chamber null reference at every rung of the ladder. On fault-free worlds ETCH-02/B itself ranks 2nd of 7 on five of twelve. The channel carries no attribution information at either grain, at any severity.

**4. Why this is faithful to A9 rather than a relaxation of it.** Four independent arguments, none of which depends on the effect currently being small:

  * **It is the audited defect's shape.** `RCA_AUDIT` found v1's "three independent signals" were "three readouts of one boolean", and yield is the variable `−0.08 if bad_tool` wrote into. Requiring yield to *rank* the tool is requiring the mechanism by which v1 gave its answer away — the same argument that retired the band, applied to the ranking instead of the magnitude.
  * **A9's own last bullet already forbids it**: "the story is recoverable **only** through mediated channels". A mediated yield signal is by construction the most attenuated one in the chain.
  * **It inverts the design's own information ordering.** `FABSIM_DESIGN.md` §2.2 requires "independent noise at every stage". Yield is the last stage, so it carries the least signal per unit of latent departure. Requiring the noisiest channel to carry attribution is backwards.
  * **The tool grain is itself a legacy artifact.** v1 had no chambers; the audit's own remedy was that "chambers become real". A tool-grain yield requirement asks the successor to reproduce a limitation of its predecessor.

**5. What replaces it, and why nothing is lost.** The concern a reviewer should raise is that dropping the item hides a future regression in which the chain stops reaching yield. It does not, because that property is gated where it can actually be measured — by **counterfactual subtraction in the simulator's own suite**, which is the only instrument that can see an 0.058-point effect:

  * `test_a_mechanism_reaches_a_die_only_through_what_the_fab_observed` runs the same timeline twice, with and without the mechanism, and asserts `moved, "the mechanism reached no die at all"` **and** that every moved die belongs to an exposed wafer.
  * `test_a_larger_edge_departure_raises_the_edge_risk_it_produced` asserts the outer-fifth parametric risk is monotone across `None → moderate → obvious` with `risks[-1] > 1.2 * risks[0]`.
  * L3's mediation test continues to bound any *direct* yield effect at ≤ 2 points (measured +0.57 on B).

  At the benchmark level A9 gains one cheap tripwire in place of the ranking: `DEMO_CHAIN_ENDPOINTS` requires the demo's declared `causal_chain` to reach `die_bins` and `wafer_yield`. That chain is *derived* from the world's own sensitivity maps (ADR-023 §6), so it cannot disagree with the physics, and severing the die plane from the story fails A9 loudly while saying nothing about the size of the last stage.

**6. The alternatives, weighed.** *Keep and gate at chamber grain* — rejected by §3: rank 6 of 7 at one of three seeds. *Keep and gate on an exposed-versus-unexposed observable contrast* — rejected because the contrast has the **wrong sign**: the exposed cohort out-yields its within-product peers by +0.43 pts, benign character dominating a 0.058-point mechanism. *Keep and gate on a counterfactual-derived mediated delta* — rejected as duplicating L3 and as requiring the hidden plane for a criterion whose whole subject is what an analyst can see. *Recalibrate the physics until yield attributes* — out of scope here and forbidden by the anti-tuning rules; it remains a legitimate future gate (§7).

**7. What is deliberately not decided.** Whether the *world's* composition should be recalibrated so that yield becomes diagnostically informative — ADR-018 §4's deliberately un-amplified observation transfer against ADR-021 §5's 3.0-tolerance functional limit — is untouched. That is a physics question with fab-wide consequences, and this ADR takes no position on it beyond noting that it is separable: "yield is weak in this scenario" and "yield should never be used diagnostically" are different claims, and only the first is established. A later scenario designed around a defect-dominated or parametric-dominated mechanism may well make yield a primary channel, and nothing here forecloses that.

**8. Consequences, checked rather than assumed.** `A9` moves **BLOCKED → PARTIAL**, and **cannot become PASS**: the wafer-map review is a human item and no arithmetic can run it. *(**Superseded by ADR-030, 2026-08-10.** The clause "cannot become PASS" was true of this gate and is no longer: the review was performed rather than automated away, found not met, measured flat across the severity ladder, and retired as a gate on the pattern this very ADR established — while the two items A9 still gates on were measured against twelve fault-free worlds and shown to discriminate. A9 is now PASS. The rest of this section stands unchanged.)* No other criterion changes. Nothing is removed from the observable schema, from FabSim, from the queries or from the truth artifact; `chamber_yield_split` is still computed, still reported by A9, and still one of A6's three declared evidence channels and one of L7's three reference channels — where it is measured against a converging reference rather than asked to rank. The anti-leakage suite is untouched. The benchmark now has **no blocked criterion**, which is a statement about the criteria having been repaired, not about the simulator having improved: three PASS, eight PARTIAL, and every PARTIAL naming genuinely unrun work — CI jobs, manual reviews, and checks inapplicable to a null.

**9. One thing noticed and deliberately not acted on, recorded so it is not rediscovered.** A9's *remaining* gating signature item — "elevated edge-ring share on the affected chamber's wafers", scored as rank 1 — is seed-fragile in exactly the way ADR-024 §5 already documents for L11: the planted chamber ranks **1st at seed 42, 3rd at 101 and 6th at 2024**, because `edge_uniformity` is signed and the defect channel reads its magnitude. A9 is scored on the demo at its *published default seed*, which is the criterion's own declared scope, so the item holds where A9 asks it. But a future gate that scores A9 across seeds will find a failure ADR-024 has already explained, and the honest fix then is the one `fabeval.fixtures` already applied — mark the channel corroborating and require the metrology channel instead. That is a change to a different item than this ADR's, in a session scoped to one question, so it is left alone.

**10. Diagnosis.** A9's resolution determines none of `DIAGNOSIS_CONTRACT.md` §8's four remaining open decisions (the score's definition, the onset statistic, the artifact's schema name, the engine's location). Diagnosis is now blocked **only** on those. One thing it does settle for the engine's designer: yield is not the channel to rank chambers on in this scenario, and the contract's §5 warning against single-channel scoring now has a third measured example behind it. *(**ADR-029** closes those four decisions and authorizes the engine.)*

## ADR-029 — Diagnosis is one self-calibrating stage: a shared anchor, a within-dataset permutation null, and a statistic that is deliberately not frozen

**Status: Accepted (2026-08-09), from the diagnosis architecture decision gate.** Four measurement gates preceded it and none of their findings had reached this file; that is corrected here. **No `src/fabsim/` change, no world constant, no scenario edit, no acceptance threshold moved** — every number below was obtained by scoring datasets the simulator already produces, with thresholds read only from fault-free worlds.

This entry is long because it is the record of what was rejected. Each rejection cost a gate, and a gate that is not written down is a gate that will be repeated.

**1. What was measured, and what it rejected.** Four candidate architectures were built and scored against the same populations. Held-out fault datasets and held-out fault-free worlds were built from seeds disjoint from every development seed, and every threshold is a quantile of fault-free worlds alone.

| # | Architecture | Measured outcome | Verdict |
|---|---|---|---|
| 1 | **Magnitude-primary** — rank candidates on a whole-horizon level | Rules F10/F11 put permanent benign offsets in the `subtle` band by design; under matched multiplicity discipline the planted candidate reached family-wise significance on **0 of 22** development datasets and 1–3 of 25 held-out | **Rejected** |
| 2 | **Temporal-primary** — a within-candidate change point as the sole primary evidence | Better than magnitude on development (5/22 against 0/22) but the advantage **did not replicate**: held-out 4/25 against 1–3/25, a difference of no significance at that size. Its natural derived null is 1.7–2.6× anti-conservative, and its proposed within-candidate permutation null cannot express `alpha = 0.05` at all — a K-bin series has K rotations, so the smallest attainable p is 1/K, i.e. 0.08–0.11 | **Rejected as the *sole* primary evidence** |
| 3 | **More evidence** — the same rule at 2× and 4× the horizon, fault magnitude held constant | Null calibration held (0.025 at every scale) and detection did **not** improve: 3/30 → 1/30 → 3/30 held-out. The planted candidate's statistic stayed flat (2.74 / 3.16 / 2.75 on the cleanest channel) while the benign one grew (1.65 / 1.83 / 1.90) | **Rejected**; ADR-012's ceiling stands, now measured rather than asserted |
| 4 | **Two-stage, with a fab-level excursion-detection stage** | At matched null rates a candidate-free stage 1 fires on **1 of 25** held-out faults against the candidate-enumerating maximum's **7 of 25** | **Rejected** |

**Why (4) fails is physics rather than an implementation accident.** For a single-entity alternative the maximum over entities is the powerful statistic and an omnibus is not; a fab-level statistic discards exactly the information that makes one faulted chamber findable. It is also *structurally* harder to calibrate: exchangeable candidates yield one null draw **per candidate per world** (1,117 over the calibration population) while a fab-level scalar yields one **per world** (60). A separate detection stage therefore needs roughly the candidate count in extra fault-free worlds to reach the same resolution, and buys negative power for it.

**2. The one thing a scope was measured to buy, and what it actually is.** Attribution was scored under four time anchors, paired over the same 25 held-out faults, everything else identical:

```
                                   rank-1   top-3    MRR    detected
A  per-candidate scan               7/25    13/25   0.481     4/25
B  declared midpoint anchor        11/25    15/25   0.574     4/25
C  fab-level change-point anchor   11/25    15/25   0.574     6/25
D  ORACLE anchor at truth's onset  10/25    16/25   0.568     3/25
E  random anchor (three draws)    8,8,14  16,16,19  .51/.51/.69
```

**B = C = D, and random anchors span the same range.** Knowing the true onset buys nothing over an arbitrary constant. The whole effect is that a benign candidate wins a *per-candidate* maximization more often than a faulted one does — with more bins, more benign excursions compete, which is exactly the mechanism finding (3) isolated. Paired McNemar against A: B 6 wins / 2 losses (p = 0.29), C 5/1 (p = 0.22), D 4/1 (p = 0.38).

**The decision.** The change point is a **shared nuisance parameter, estimated once and applied to every candidate** — never a per-candidate free parameter. This is enforced structurally rather than by review: a candidate's evidence is computed at anchors it was not consulted about, and `fabops.diagnosis.anchors` is the only module that may choose one.

**What is deliberately *not* decided:** *which* anchor rule. Variant B works on this library because every planted fault onsets at day 30–40 of 84, which is a benchmark artefact and must not become architecture. The anchor rule is a declared, versioned component selected by a benchmark that does not yet exist (§5).

**3. The null lives inside the dataset, so no calibration artifact exists.** `DIAGNOSIS_CONTRACT.md` §2 gives the engine a database path and nothing else, and every size-based statistic needs a null. Until this gate the only candidates were a derived reference (measured invalid for the temporal statistic) or an external fault-free corpus — which would have created the `fabeval` → diagnosis control path the plane separation exists to forbid, and a provenance problem with no clean answer.

Neither is needed. Under F10/F11 the candidates of a fault-free world are exchangeable, so a candidate's *vector* of channel statistics is exchangeable with any other candidate's. Permuting the candidate label **jointly within an evidence family and independently across families** preserves every channel's marginal distribution *and* the real within-family dependence (metrology↔fdc r = 0.445), and destroys only what convergence is about: the same candidate being extreme in several near-independent families at once.

Measured on 65 fault-free worlds (binomial standard error .012 / .027 / .037 / .050):

```
                        p<=.01   p<=.05   p<=.10   p<=.20
family-joint, anchored    .000     .062     .092     .231
expected                  .010     .050     .100     .200
```

Valid within noise at every level. The earlier per-channel-independent permutation reads .015 / .092 / .169 / .292 — anti-conservative, *because* it destroyed within-family dependence that exists under the null. The difference between those two permutations is the whole of the design, which is why both are recorded.

**Consequence.** `diagnose(db_path)` is self-calibrating. There is no calibration artifact to version, ship, invalidate or prove independent, and no path by which the evaluator can reach the engine. The validity assumption is a *declared, falsifiable* property of the world, and the measurement above is a test the simulator can fail.

**The limit, stated plainly.** Exchangeability is a property of *this* world's construction. A world variant, or a real fab, would need the same measurement re-run before the null could be trusted. That is a feature: it is checkable, and the check ships.

**4. What the engine estimates — and what it cannot.** The concepts were conflated across four gates and are separated here:

| term | the object |
|---|---|
| **evidence** | a *residual*: an observation minus the fab's own reference for it (a recipe setpoint, a recipe `metric_target`) |
| **anchor** | a shared change point — a nuisance parameter, never a conclusion |
| **abstention** | one family-wise statement per dataset: is any candidate more extreme than candidate-label permutation produces |
| **attribution** | a *ranking* over entity hypotheses, with the alternatives it was compared against |
| **onset** | a separate estimate on the attributed candidate's own series, reported as an interval and permitted to be absent |

**"Root cause" in this project means entity attribution, not mechanism identification, and that is a boundary rather than a shortfall.** The mechanism lives only in the hidden plane: ADR-019 §4 makes `classified_type` a noisy draw over a hidden origin, and ADR-021 §6 makes a bin a symptom drawn over a hidden cause. No observable channel identifies which mechanism acted, by construction. An engine that named one would be matching a catalogue — which `DIAGNOSIS_CONTRACT.md` §6.4 already forbids. `fabeval` scores attribution and onset; it does not score mechanism naming, and no later version may add that without first giving the observable plane a channel that could carry it.

**5. The statistic is not frozen, and that is the decision.** Nothing measured across four gates distinguishes the statistic variants at the size of the current benchmark: the anchor comparison's paired tests sit at p = 0.22–0.38, and the spread across random anchors (8 to 14 of 25) is larger than the effect being estimated. Freezing a statistic here would be fitting the architecture to five scenarios.

So the architecture is one in which the statistic is a **declared, versioned, replaceable component** behind a stable API, and the benchmark is what selects it. Until the library reaches `EXPANSION_ROADMAP` Phase 6's ≥ 10 scenarios with a declared development/held-out split, **the engine may not claim a benchmark number.** `fabeval` reports diagnosis metrics as *measured on a named population*, never as a capability claim.

**6. The roadmap's `investigate <excursion>` sketch is superseded.** `EXPANSION_ROADMAP` Phase 5 sketches `fabops investigate <excursion>`, with Phase 4 producing the excursion; `DIAGNOSIS_CONTRACT.md` §2 gives the engine a database path. The conflict is settled in favour of the contract, on two grounds and not on preference:

1. **Measurement.** §1 finding (4): a separable detection stage is 7× weaker and harder to calibrate. There is no statistical stage to separate.
2. **Anti-leakage.** If detection is separately callable and diagnosis consumes its output, answer-blindness becomes a property of the *caller*: a hand-built excursion carrying truth's window is a leakage channel through the argument list. This is precisely why §2 already rejected a `Dataset` argument. One entry point keeps the guarantee structural.

**What is retained rather than dropped:** the Excursion *object* — window, onset, scope — becomes a **field of the `Investigation`**, an output instead of an upstream input. Monitors (Phase 3) remain a legitimate separate capability over a database path producing SPC output a human reads; they are simply not a precondition of diagnosis. The roadmap's dependency *order* survives in substance — you cannot explain a change you have not located — but locating and explaining are one deterministic computation behind one entry point.

This is a **governance correction, not an architecture change**: the roadmap is a Phase 0 planning document and the contract is the later, more specific governing artifact. The roadmap is annotated, not rewritten.

**7. What this decides, and what it leaves open.** Decided: the engine is single-stage and self-calibrating; `diagnose(db_path) -> Investigation` is the sole public entry point; the anchor is shared; the null is a within-dataset family-joint permutation; the artifact is `fabops.investigation/v1`; the package is `src/fabops/diagnosis/`; root cause means entity attribution. `DIAGNOSIS_CONTRACT.md` §8's five open decisions are closed by this ADR.

Open, and deliberately: which anchor rule, which per-candidate statistic, and what the abstention level should be for a *fab* rather than for a benchmark. All three wait on the benchmark of §5.

**8. Four corrections the implementation forced, and the configuration that shipped.** This ADR was accepted before the engine existed, and building it falsified four things the gate had assumed. None changes the architecture — one entry point, one stage, no calibration artifact, family-joint permutation, `not_assessable` — and all four change what "the same architecture" has to do to be correct. Each is pinned by a test.

  * **The anchor must be *declared*, not discovered.** §2 proposed reading the shared change point out of the fab's own aggregate. That aggregate is a series the candidates *are*: the entity that moves the fab creates the anchor it is then scored at, and a permutation that starts after the anchor exists cannot reproduce that selection. Measured on 200 fault-free worlds, the fab-chosen anchor made the engine fire at **0.200 against a nominal 0.05**. `anchors.select` is now handed the horizon and nothing else, so a candidate cannot reach it. The cost is real and stated: the engine cannot adapt to a world whose faults arrive somewhere unusual, and the present library cannot measure that because every fault in it begins mid-horizon.

  * **Peers are exchangeable within a *role*, not across a fab.** The first implementation ranked every chamber against every other chamber. A chamber every wafer passes through and a chamber a third of them reach are not draws from one distribution, and that is the permutation null's only precondition. Grouping candidates by `tools.tool_type` — ordinary observable data — is most of what moved the null back to its declared level. Rule F10/F11 gives exchangeability *within* the equipment population it describes; reading it as a fab-wide property was the error.

  * **The denominator decides whether convergence means anything.** A spread pooled over a stratum is more precise per candidate and measurably **not calibrated**: exposure differs by routing, so a lightly-used candidate's series is noisier, its standardized step is larger on *every* channel at once, and that is precisely the shape cross-family convergence exists to detect. Standardizing by each candidate's own spread restores the null. It costs most of the engine's measured power, and the trade is deliberate — an abstention is a claim, a mis-calibrated claim is worse than a weak one, and §5 already forbids claiming a benchmark number until the library can support one.

  * **The family-wise maximum has to be studentized.** The raw combined score is not comparable across strata: a rank inside a stratum of eighty-four recipes reaches 1/85 while a rank inside a stratum of seven chambers stops at 1/8, so a raw maximum is decided by whichever stratum is largest rather than by whichever candidate moved. Measured before the fix, poisoning one chamber on three evidence families moved that chamber's own p-value from 0.394 to 0.019 while the fab-wide statement moved from 0.151 to 0.141 — the engine saw it and the abstention could not. Each candidate is now standardized against its own permutation distribution before the maximum is taken, in a second pass over the same seeded draws.

  * **A fifth, added at the Final Integration gate (2026-08-10): the Excursion is delivered by the artifact's existing fields rather than as a field of its own.** §6 above says the Excursion *object* — window, onset, scope — "becomes a **field** of the `Investigation`". No such field shipped, and on inspection none should: `DIAGNOSIS_CONTRACT.md` §3, the later and more specific artifact contract, does not declare one, and the three things §6 wanted are each already there and each better placed. **Window** is `Investigation.window`, the period examined. **Onset** is `Candidate.onsets[]` — per candidate, an interval, permitted to be absent and permitted to be plural, which a single fab-level Excursion field could not express and which scenario I's two-change-point arc requires. **Scope** is the ranked `candidates[]` with `considered[]` beside it, which is strictly more than a scope: it carries the rivals as well as the winner. Adding an `excursion` field now would duplicate all three into a second, redundant surface on an export contract (ADR-008) that other systems version against — a new API with no new information, which §7's own prohibition on unnecessary surfaces rules out. **§6's substance stands and only its noun is wrong: the Excursion is an output, not an upstream input, and that is what made one entry point possible.**

  **The configuration that shipped, and the two-by-two it was chosen from.** Selection was made on **fault-free calibration and non-inertness only**; the held-out fault column is reported and was not selected on.

```
variant                        null .01/.05/.10/.20      30% 3-family mutation   held-out det / rank-1
sidak + own-scale  (SHIPPED)   .010 / .060 / .110 / .205   fires at p = 0.049       1/25 / 6/25
sidak + pooled                 .010 / .075 / .125 / .240   fires at p = 0.017       7/25 / 5/25
no-sidak + own-scale           .015 / .065 / .125 / .225   ABSTAINS at p = 0.073    0/25 / 7/25
no-sidak + pooled              .005 / .085 / .145 / .255   fires at p = 0.007       6/25 / 7/25
nominal                        .010 / .050 / .100 / .200   must fire
```

  The shipped row is the only one that is both calibrated at every declared level and able to be moved. The Sidak *inside* a family survives for a reason worth stating precisely: it is not there to control an error rate — the permutation does that exactly — it is a monotone transform that buys **separation**, and removing it made every candidate's family evidence shrink together, which moved the permuted maximum as much as the observed one and left the engine unable to be moved at all.

  **What the engine's power actually is, stated plainly.** One held-out fault in twenty-five clears the fab-wide bar, and the planted chamber leads the ranking in six. That is weak, it is measured, and it is not hidden: §5 forbids a benchmark claim until the library reaches ten scenarios with a declared split, and the statistic registry exists so that benchmark can choose a stronger member from a table rather than from folklore.

## ADR-030 — A9's manual wafer-map item is run, found flat across the severity ladder, and retired as a gate; A9 reaches PASS on the items that discriminate

**Status: Accepted (2026-08-10), from the Final Acceptance gate.** The review A9 had been waiting for since Step 3E was performed rather than waived. **No `src/fabsim/` change, no world constant, no scenario edit, no severity recalibration, no threshold moved** — the only code change is that `fabeval.acceptance.check_a9` stops gating on an item that was measured to be unreachable, and starts reporting the measurement that shows why. This is the **fourth** criterion-repair gate and the **third** A9 item to be retired, and the pattern is now unmistakable enough to state as a finding in its own right.

**1. The review, and what it found.** `chamber_edge_uniformity` at seed 42, GATE layer, geometry only — schema v2 has no `EDGE_RING` class, because ADR-019 §4 makes classification a noisy instrument over a hidden origin, so a class column would be the answer rather than the evidence. Four cohorts, each removing a different alternative explanation:

| cohort | what it removes | edge share (r > 0.8R) | mean radius |
|---|---|---|---|
| planted chamber, after onset | — (the claim) | 0.4087 | 97.85 mm |
| planted chamber, before onset | the chamber's own benign character | 0.4155 | 97.46 mm |
| other chambers, same window | the window | 0.3905 | 97.31 mm |
| planted chamber, fault-free world | the fab | 0.3984 | 97.32 mm |

The radial profiles of all four overlap. The excess is **≈25 edge defects on a 1500-defect cohort**, spread around the whole wafer edge. Against its own pre-onset window the faulted cohort is *lower*.

**2. It is not a severity problem, and that is what settles it.** The obvious hypothesis — *moderate is simply too quiet, turn it up* — was measured and is false. Geometric edge-zone lift of the planted chamber over its peers, whole horizon, via the reference query:

```
severity     planted   peer mean    lift     rank   leave-one-out z
subtle        0.4090     0.3887    x1.052     1        +2.091
moderate      0.4124     0.3892    x1.060     1        +2.344
obvious       0.4116     0.3887    x1.059     1        +2.220
```

**Flat.** The ladder moves the latent by 1.61 → 3.22 → 4.00 σ and moves this channel's *spatial* lift not at all. No rung of the declared severity scale makes a wafer map show anything, so the item is not waiting on a louder fault; it is unreachable in this world by construction. (This is consistent with A6, which already measured this channel's exceedance as non-monotone in severity: 0.137 / 0.105 / 0.119.)

**3. Where the target magnitude came from.** The legacy v1 figure the wording was written against (`reports/figures/04_wafer_maps.png`) reaches **×1.78 on the same geometric measure** — 0.5687 edge share against 0.3197 for the other etchers — and **×3.86** on the `EDGE_RING` *class* it colours by. Two things follow. The class channel that carries half of that figure's visual impact does not exist in schema v2 and was removed deliberately as leakage. And the geometric half is ×1.78 because v1's fault was written directly into its outputs — the same `−0.08 if bad_tool` provenance that ADR-027 traced for the 4–10 point band and ADR-028 traced for the yield ranking. **Requiring the successor's wafer map to be visibly as striking is requiring the magnitude the direct-label term produced, which ADR-004 exists to abolish and which A9's own text already forbids** ("no constant may ever be moved to bring a number closer to it").

**4. What the review also established, and this is the part that is not a retirement.** The two items that *do* gate had never had a reference distribution computed either — the same omission ADR-026, ADR-027 and ADR-028 each found. It has now been computed, on **twelve fault-free worlds**, and both items pass it:

```
                          faulted world      fault-free reference (n = 12)
edge-zone defect share    rank 1/7, z=+2.344  leads 2/12 (chance 1/7), mean z = -0.322
edge CD deviation         rank 1/7, z=+2.650  leads 0/12
```

Two of twelve is chance; the faulted world is not. **Unlike the cohort-yield ranking ADR-028 retired — which a fault-free world satisfied one time in three and which carried no severity information at all — these two separate a faulted world from a fault-free one.** A single null world (seed 42) happens to put the planted chamber first on defects at z = +1.819, which is exactly the one-draw trap ADR-025 §5 fell into; twelve worlds show it for the draw it is.

**5. The decision.** The wafer-map item is **retired as a gate and retained as reported evidence**, the treatment ADR-028 established. `check_a9` prints the ladder measurement and the null reference at every verdict; `WAFER_MAP_LADDER_LIFT` and `WAFER_MAP_NULL_RANK1` keep the numbers in code, and a test pins both directions, so retiring the item cannot become deleting the finding and cannot quietly become enforcing it again.

**A9 therefore becomes PASS**, on three gating items measured against twelve fault-free worlds. That is a status change on the flagship criterion and it is earned rather than relaxed: what was removed is an item no correct simulator could satisfy at any severity, and what remains is the part that discriminates. The matrix now reads four PASS (A2, A4, A9, A10), seven PARTIAL, nothing blocked.

**6. The pattern, stated once so the next gate does not rediscover it.** Four gates have now each found a criterion whose reference distribution had never been computed, and in every case the criterion's target traced to the audited v1 system: A6's floor and L7's constant (ADR-026, an order statistic that diverges with the null budget), A9's 4–10 point band (ADR-027, the direct-label magnitude), A9's cohort-yield ranking (ADR-028, satisfied by chance one time in three), and now A9's wafer map (this ADR, flat across the ladder). **The generalisable rule: a Phase 1 criterion inherited from the v1 demo states a magnitude that v1 produced by writing the answer in, and is therefore unreachable by a system that forbids doing so.** Any future criterion phrased as "the demo should visibly/numerically show X" must ship with the distribution of X on a fault-free world before it may gate anything. `EXPANSION_ROADMAP` Phase 6's ≥10-scenario library is what makes that affordable by default.

**7. What this does not license.** Nothing is removed from the simulator, the schema, the queries or the truth artifact. The wafer maps are still renderable and the review is still reproducible — `reports/figures/` and the legacy demo are untouched (ADR-010). "The spatial channel is weak in this scenario" is **not** "spatial evidence is useless": a later scenario built around a defect-dominated mechanism may well make it primary, and the diagnosis engine already reads defect geometry as one of its five evidence families. Whether the world's composition should be recalibrated so the spatial channel carries more signal is untouched and remains a separate physics gate (ADR-026 §1).

## ADR-031 — Phase 6: the library reaches twelve with a declared split, and the three open decisions are closed by measurement — all three against the incumbent

**Status: Accepted (2026-08-10), from the Phase 6 scenario-expansion and calibration gate.** `EXPANSION_ROADMAP` Phase 6 and ADR-029 §5 both said the same thing: the engine may not claim a benchmark number until the library reaches ≥10 scenarios with a declared development/held-out split, and the three parameters `DIAGNOSIS_CONTRACT.md` §8.1 left open wait on that library. It now exists. **No `src/fabsim/` change, no world constant, no severity recalibration, no mechanism edit** — the seven new scenarios are configuration over the existing mechanism registry, so the generator is byte-identical and `REFERENCE_BUILDS` is untouched.

The headline is that **none of the three open decisions moved the incumbent**, and that one of them nearly did. Recording why is most of what this entry is for.

**1. The deferred SQL determinism correction, and what it turned out to cost.** The Final Acceptance gate classified the missing `ORDER BY` as MUST FIX BEFORE PHASE 6 but sequenced the fix *into* the calibration cycle, on the reasoning that adding it "may perturb float summation and invalidate/recalibrate figures". It was therefore done first inside that cycle, before any Phase 6 number was measured. Both halves of that reasoning are now measured.

The defect was **real and latent**. SQL licenses a database to return rows in any order unless the query states one, and both analysis packages fold those rows into floats — a bin mean, a product mean, a per-wafer defect share — after which the decision plane turns values into *ranks*, where a last-bit difference is a tie broken by whichever plan the query planner chose. Reversing the row order on one dataset moved **383 of the peer-differenced series** while leaving the report itself intact. Nothing was visibly wrong and nothing would have been, until two candidates sat close enough together for the last bit to decide which chamber a fab was told to inspect — and Phase 6 was about to run the engine over hundreds of datasets to *select* a method, where a selection partly decided by the query planner is not a measurement of the method.

The cost of fixing it was **zero**. Every multi-row query in `fabops.diagnosis` and `fabeval` now states a total order on a primary key, and on the library at seed 42 the correction changes nothing: not a p-value, not a ranking, not a z-score, not a float hex — zero differing values across five scenarios. SQLite's natural scan order for these queries already *was* primary-key order. The deferral was prudent and it turned out to buy nothing, which is worth recording in both directions: the recalibration it was deferred to avoid did not exist, and the fix would have been equally safe earlier.

Guarded by two clauses, because either alone passes while the property is false. A **static** one — every multi-row query states an order, checkable by reading, and it fails on a query added later. And a **runtime** one: a connection that shuffles the rows of any query *without* an `ORDER BY` must leave the report byte-identical. That harness is not arbitrary; it is exactly the licence SQL grants. Its mirror ships too, because invariance is worthless on an inert engine. `fabops.diagnosis` moves 1.0.0 → 1.1.0 although reports are unchanged, on ADR-023's precedent: the rule is "could alter", and the two implementations differ in exactly that.

**The legacy v1 surface is out of scope and was measured rather than assumed.** `fabops.investigation` reads the committed schema-v1 database through pandas and is protected by ADR-010. Its tables were physically rewritten in reverse row order and the narrated ETCH-02 story came out identical, so the surface carries no deferred debt; a test pins it.

**2. The library: five to twelve, and what "diversity" had to mean.** Seven configurations join, three of them the members `SCENARIO_SPECIFICATION.md` §3 deferred (D sudden excursion, H benign correlate, J multiple simultaneous) and four answering questions Phase 1 did not anticipate. `fabeval.benchmark.diversity` measures coverage on eight axes from the configurations themselves and a test asserts each one varies, so "diversity is demonstrated" is a measurement rather than a claim.

**Severity is not the binding axis, and the roadmap's "per fault class × severity" understates the requirement.** The axis that was actually blocking a decision is **onset position**. Every Phase 1 fault begins at day 30–40 of 84 — ADR-029 §2 flagged that as "a benchmark artefact [that] must not become architecture" — and *no measurement could distinguish one anchor rule from another until the library contained a fault at day 12 and one at day 63*. That is the whole reason the expansion had to precede the calibration rather than follow it.

One scenario was retargeted during design and the reason is kept. `tool_wide_drift` was first written on `PVD-01`; measured across three development seeds a moderate `param_drift` there produced **no recoverable reference-query evidence at all**, because PVD's parametric sensitivities are a fraction of etch's and its only alarm rule sits at 3σ. The scenario's purpose is *grain* — its correct answer is a tool rather than a chamber, which no other member tests — so the target moved to a tool family where the mechanism has a channel. That is scenario design, not tuning: no world constant, sensitivity or threshold was touched, and PVD coverage is retained by J.

**3. The split runs on two axes, and which scenarios are development was settled by history rather than chosen.** Phase 1 declared a seed split only, and a seed split cannot protect a method chosen while looking at a *scenario*: selecting a statistic on `chamber_edge_uniformity` at seed 42 and scoring it at seed 555 still fits the method to that mechanism, that target, that severity and that onset. `fabeval.population` therefore declares `DEVELOPMENT_SCENARIOS` and `HELD_OUT_SCENARIOS` beside the seed roles and `assert_disjoint` checks both.

The five Phase 1 members are development **permanently**. ADR-025 through ADR-030 each measured them; declaring one held out would be a claim about the past. What Phase 6 could choose is where each *new* scenario goes, and three went to development because the anchor question cannot be answered without them. `claimable` is correspondingly strengthened to require the split as well as the count: ten scenarios all of which chose the method are ten scenarios the method is fitted to.

**The boundary is between the held-out scenarios and the diagnosis *method*, not between them and the evaluator's existing simulator checks.** An L11 expectation states what a scenario's own physics should show through the reference queries, which are a fixed instrument predating the method. Building a held-out scenario and confirming its declared fault fired is scenario QA; running the engine on it and changing something afterwards is not.

**4. The A1–A11 matrix deliberately still scores the Phase 1 five.** Pointing it at twelve looked like a free strengthening and is the wrong instrument: those criteria were written about, and ratified against, the five that existed then, so scoring them on later configurations lets a Phase 6 config retroactively move a settled verdict — reopening Phase 1 by another route. It is not hypothetical. A5 requires every faulted scenario to carry ≥30% of its horizon as baseline before onset, and `early_particle_excursion` sits at 14% **on purpose**, for the reason §2 gives. Scored under A5 it would move the criterion PARTIAL → BLOCKED, and the honest reading of that is not that the simulator regressed but that a Phase 1 criterion is being asked a Phase 6 question. The anti-leakage suite is the opposite case and runs on **all twelve**, because L1–L11 assert properties of a *dataset*: measured, no failures.

**5. The anchor rule: confirmed, not moved — and the near-miss is the finding.** Four grids, `own_scale_step`, pooled over **660 fault-free worlds from three disjoint seed ranges**:

```
grid                          a=.01            a=.05      a=.10      a=.20
(0.50,)          midpoint     .0106 (+0.2sd)   .0667      .1303      .2197
(0.25,.50,.75)   quarters     .0258 (+4.1sd)   .0697      .1197      .2167
```

A three-anchor grid runs at **2.6× nominal at α = 0.01**, and +4.1 standard deviations is not a draw; the single mid-horizon anchor is exact there. At the other levels the two are indistinguishable and both sit near 1.3× nominal, which is the pre-existing state ADR-029 recorded. The grid *finds more* — 6 of 21 faulted development datasets at α = 0.05 against **0** — and that is precisely the trade the architecture already decided: an abstention is a claim, and a mis-calibrated claim is worse than a weak one. **The anchor does not move. What changes is that its cost is now measured rather than suspected.**

  **This gate nearly made the mistake it exists to catch, and the record is more useful than the result.** The first measurement used 200 fault-free worlds, put the grid at .005 at α = 0.01 — *below* nominal — and the grid was adopted on it. The suite's own 60-world null population then failed the contract's validity test at .083. Two samples, a sixteen-fold disagreement, and picking the one that agreed with the preferred answer would have been the mistake ADR-026, ADR-027, ADR-028 and ADR-030 each found once. An independent 400-world population settled it against the grid. **The generalisable lesson, and it is a new one: a declared level of 0.01 cannot be validated on any population a test suite can afford to build.** Separating 0.01 from 0.02 at 80% power needs roughly eight hundred worlds. The suite's 60-world check can therefore fail for being small and pass for being small, and only a pooled measurement recorded outside the suite can say whether the level holds.

  A recorded figure is corrected. `anchors.py` said a three-anchor grid "reads 0.270 at a nominal 0.20 (+2.5 standard errors)"; four grids were re-measured at 0.185–0.225 there. The conclusion that note supported is right, for a reason it did not state — the grid's cost is at α = 0.01, not at α = 0.20.

**6. The statistic: confirmed, and the registry member that had never been measured is the one the benchmark disqualified.** `trend_contrast` shipped "registered and unmeasured", which was a real risk rather than an omission: it is by far the most *powerful* member — 14 of 21 development faults at α = 0.05 — and it states a level it does not keep, reading .025/.090/.135/.255 on 200 fault-free worlds and firing on 2 of the 3 fault-free development worlds. It stays registered with a measured reason instead of none. `standardized_step` reproduces its known shape: markedly better ranking (9 of 21 development faults in the top three against 3; MRR 0.328 against 0.148) and above nominal at all four levels. `own_scale_step` remains the default on ADR-029's ordering, and the ranking numbers are recorded because a later gate optimising for a human reading a ranked list rather than for an automated abstention would be choosing under a different priority and would have a real case.

**7. The level: confirmed, and the reason it does not move is the reason that matters.** Measured on the 660 fault-free worlds and the 21 faulted development datasets:

```
alpha     realized false alarms     detections (21 development faults)
0.0027           0.0000                        0
0.010            0.0106                        0
0.050            0.0667                        0
0.100            0.1303                        6
0.200            0.2197                        7
```

At the declared level the engine fires on **none** of them; at 0.10 it finds six, at a false-alarm rate of one world in eight; and at **0.0027** — the fab's own 3σ control-limit convention, which ADR-027 borrowed for the evaluator's per-world action limit — it finds nothing, which is the number to quote if a report were ever wired to dispatch a technician. Moving the level to 0.10 would be choosing it off the detection column, which `DIAGNOSIS_CONTRACT.md` §8's second decision forbids in terms. The principle available is ADR-027's: an action limit for a per-world decision, a screening level where there is one verdict. This abstention chooses between offering a ranked candidate list with its evidence and saying "insufficient evidence", and a human acts afterwards — a screening decision, read at the screening level. What a *fab* should declare stays open, now for a stated reason: closing it needs the relative cost of a missed excursion and a wasted investigation, this project has no cost model, and inventing one to justify a number is the architecture invention every gate here has refused.

**8. The held-out score, once, with the method frozen — and it is weak.**

```
                          development (8 x 3)      HELD OUT (4 x 5)
datasets                  24 (21 faulted)          20 (15 faulted)
false-alarm rate          0.000                    0.000
detection rate            0.000                    0.000
attribution rank 1        0.095                    0.000
attribution top 3         0.190                    0.000
mean reciprocal rank      0.228                    0.075
median onset error (d)    7.0                      10.0
```

**The engine abstained on all 44 datasets across all 12 scenarios.** It never once cleared the fab-wide bar, including on faults realized at 15σ. Its false-alarm rate is 0 on both populations, which is the half of the contract it keeps exactly. This is reported rather than buried, and it is not a surprise: ADR-029 §8 already recorded 1 of 25 held-out detections, and §5 and §7 above show the level and the anchor were each chosen knowing they cost power. **`EXPANSION_ROADMAP` Phase 6's population now exists, so a number may finally be called a capability — and the honest capability statement is that this engine detects nothing at its declared level and ranks the planted entity first on about one development dataset in ten.**

**9. A capability boundary nobody had written down, found by the held-out run.** Two of the four held-out scenarios plant faults the engine **cannot name by construction**, and the reason is structural rather than statistical. Every reference statistic and every panel is a leave-one-out standing among same-role peers, and `MIN_PEERS = 2` means an entity needs at least two — that is, **at least three chambers in its tool family**. In `baseline_fab_v1` only ETCH (7 chambers) and CVD (4) qualify. The other **13 of 24 chambers, across six tool families, are `not_assessable` in every dataset this project can build**, and the engine says so with a reason, which is the contract behaving correctly rather than failing. `intermittent_particle_load` (CMP) and `multi_fault`'s PVD event fall in that gap; the reference queries share the limitation for the same reason, returning `z = +0.00` where fewer than two peers exist — a refusal rather than a result.

  This is **not** repaired here, and the option that looks obvious is the one the architecture forbids: relaxing `MIN_PEERS` would leave the permutation null with no exchangeable population to permute, which is its only precondition. Widening a stratum to group tool families is a change to the exchangeability assumption ADR-029 §8 deliberately *narrowed* to `tool_type` after measuring that a fab-wide grouping fired on 20% of fault-free worlds; re-widening it needs its own gate and its own measurement. What Phase 6 does is state the boundary, quantify it, and pin it: `too_few_peers_to_rank` asserts it positively in the L11 expectation, so a world that later gives such a family a third chamber fails the check and the expectation has to be rewritten rather than inherited.

  Two L11 expectations were corrected once measured, in the way ADR-024 §5 established. `intermittent_particle_load`'s alarm channel becomes corroborating for the **structural** reason above — requiring a channel that cannot exist would be requiring a different equipment roster — and `multi_fault`'s etch event becomes corroborating for the **stochastic** one: `edge_uniformity` reaches the alarm plane only through `endpoint_time_s` at sensitivity 0.30, so ETCH-02/B reaches +4.34 and ETCH-03/B reaches −0.31 on the same mechanism at the same severity. Its PVD event stays required and leads at z = +7.03.

**10. A documentation defect in shipped code, corrected.** `decide.py`'s module docstring said a family's best channel is taken **without** a Sidak correction and that applying one left the report abstaining on a poisoned world; `_family_evidence` twelve lines below applied it and said so, and ADR-029 §8's shipped row is `sidak + own_scale`. Two records of one decision inside one module, disagreeing, with the code never wrong. It is the same failure `statistics.py` records for its own registry table and takes the same fix: the module that owns a decision states it once.

**11. What changed, and what did not.** Changed: seven scenario configs and the maintainers' index; `fabeval.population` (scenario split, `role_of`, strengthened `claimable`); `fabeval.benchmark` (new); `fabeval.diagnosisscore` (multi-planted scoring — an assumption the adapter carried silently until a two-fault scenario existed, whose failure mode was quiet); `fabeval.fixtures` (seven expectations, two structural checks); `ORDER BY` in `fabops.diagnosis.channels`, `fabeval.queries`, `fabeval.leakage`, `fabeval.acceptance`; the engine version; and the declaration comments in `anchors.py`, `decide.py` and `statistics.py`. **Unchanged: `src/fabsim/` entirely, every world constant, every severity, every threshold, the anchor rule, the default statistic, the level, `app/`, `sql/`, `data/`, and the legacy v1 surfaces.** Nothing became PASS, nothing became BLOCKED, and the acceptance matrix reads exactly as ADR-030 left it.

**12. What Phase 6 deliberately did not do.** It did not make the engine stronger. Every lever that would have — a multi-anchor grid, the pooled-denominator statistic, `trend_contrast`, a higher level — was measured, and each buys detections by spending calibration the architecture ranks above them. Whether that ordering is the right one for a fab is a question a cost model would answer and this project does not have one; whether the *world* should be recalibrated so a single channel carries more signal is the physics gate ADR-026 §1 and ADR-028 §7 have both already deferred; and whether the stratum definition should widen is §9's. All three are now blocked on something specific rather than on the absence of a benchmark, which is the state Phase 6 existed to reach.
