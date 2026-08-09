# Phase 1 Acceptance — FabSim + Schema v2

**Status:** design-gate deliverable. These are the exact tests Phase 1 implementation must pass before the phase closes and before any Phase 2 work begins. Each criterion is automated unless marked (manual review).

---

## A. Acceptance criteria

### A1 — Reproducibility
Same config + same world + same seed + same fabsim version + same schema version ⇒ **the same dataset content**, everywhere. (The world template joined the inputs in Step 3.0: `build_fingerprint` previously omitted it, so two datasets built from one config against two different worlds were indistinguishable by their recorded identity — ADR-015 §5.) The oracle is a canonical content hash, not the raw bytes of the SQLite file: `fab.db` bytes depend on the SQLite library version, page size and free-list history, so a byte compare across operating systems tests the storage engine as much as it tests FabSim, and would fail for reasons that have nothing to do with determinism.

Four checks, in order of authority:

1. **Input fingerprint.** The five inputs canonicalize to one `build_fingerprint` (`fabsim.scenario.derive_build_fingerprint`), the world entering as `world_sha256` (`fabsim.world.world_sha256`: the template's semantic content, prose excluded, formatting and byte-order mark normalized away). Two runs claiming to be the same build must agree on it; changing any input — including any semantic field of the world template — must move it; and it must not move when anything environmental changes: path, machine, user, locale, clock, hash seed, or the order in which streams were drawn.
2. **Content hash — the portable guarantee.** Two clean runs must produce the identical `content_sha256`: a canonical row-level digest over every table of `fab.db` in a normalized form — tables in name order, rows in primary-key order, values in a fixed type-tagged text encoding (integers exact, floats shortest round-trip repr, NULL distinct from the empty string, text in NFC). This is what CI compares across operating systems and SQLite versions, and it is what the manifest records.
3. **Normalized text artifacts.** `fab_database.sql` is emitted deterministically (fixed statement order, fixed formatting, no environment-dependent preamble) and compared byte-for-byte; `truth/truth.json` is canonical JSON (sorted keys, fixed separators) and compared byte-for-byte. Text dumps are portable in a way the binary file is not.
4. **Additional check, controlled environment only.** On the CI reference image (pinned OS, Python and SQLite), `fab.db` is *also* compared by SHA-256. A mismatch here while (2) and (3) are green is a storage-layer difference, not a reproducibility failure: it fails the reference-image job and is investigated there, and it never gates the cross-platform result.

`manifest.json` is identical across runs except `created_at` — the only wall-clock value in the pipeline, excluded from every hash.

*As implemented (the emission gate), checks 1–3 are met and tested.* The five inputs canonicalize to one `build_fingerprint`, and moving any one of them — seed, config, world — moves it while leaving the others alone. Two builds of one dataset produce the identical `content_sha256` (tables in name order, rows in primary-key order, values type-tagged), byte-identical `fab_database.sql`, byte-identical `truth/truth.json`, and manifests differing in `created_at` and nothing else — verified field by field rather than by comparing the whole object. Emission is also checked across processes under a changed hash seed, locale and timezone. Check 4, the reference-image `fab.db` byte compare, waits for CI.

This does not weaken the requirement. Byte identity was only ever a proxy for "the same data"; the content hash tests that property directly, on every value in every row, and unlike a byte compare it names the table and row that diverged when it fails. What is dropped is the claim that a *binary storage format* is identical across environments FabSim does not control — a claim the design never needed and could not have kept.

### A2 — Diversity
Three seeds of `chamber_edge_uniformity`: affected-wafer sets differ pairwise (Jaccard < 0.9); realized cohort yield deltas differ; all structural invariants (A4) hold in every realization; scenario semantics (mechanism, target, onset intent) identical in truth.

*As implemented (the scenario-library gate), **partial**.* The precondition is met and tested: every library scenario built at two seeds produces a different `content_sha256` and a different `dataset_id` while keeping the same `scenario_id` and the same realized mechanism, target and onset in truth. The Jaccard bound on affected-wafer sets and the cohort-delta comparison are three-seed statistics that belong with the benchmark matrix, which this gate deliberately did not build (§17).

### A3 — No-fault validity
The null dataset passes the full integrity suite; leakage tests L7 (null blindness) and L10 pass; no latent departs its baseline band; benign distractors present at configured magnitudes (verified against truth's distractor list).

*As implemented (Step 3A), the latent half of this criterion is met and tested:* in a null realization every trajectory equals its mechanism-free counterfactual exactly; every chamber carries every latent with the declared dynamics (F10); every chamber carries a permanent benign offset on every latent whether or not a distractor was declared (F11); and each latent's realized within-chamber weekly σ sits within ±30% of the `severity_reference` the world declares, across seeds. The dataset-level half waits for the emitters.

*As implemented (Step 3C), the null process data varies:* chamber means, weekly means, lot means and run-to-run values all differ without any mechanism, products scatter about their own recipe targets, and healthy chambers overlap each other far more than they differ. A null dataset of flat readings would make any variation an answer.

*As implemented (Step 3B), the null is not artificially clean:* it raises alarms of **both** kinds on most of its chambers, escalates some of them into work orders, and recovers latent state at background breakdowns and requested repairs alike. A null dataset that contained no alarms and no unscheduled maintenance would make either one an answer; this criterion now requires their presence, not their absence.

*As implemented (Step 3D), the null defect population is ordinary:* 22 defects per wafer, no spotless wafers, all five origins and all five classes present, and a background radial profile matching the uniform-over-area law to within 0.02 in every radial fifth. A null dataset with no defects — or with only background ones — would make a defect class an answer.

*As implemented (Step 3E), the null fab loses die the way a fab does:* every product's realized yield sits within 1.6 points of its declared specification across three seeds, wafer-to-wafer σ is 1.1–4.4 points and lot-to-lot σ 0–2.9 (§2's budget is 2.5–3.5 and 1–1.5), no wafer is perfect, all three kill causes and all five bin codes occur in quantity, and edge die die more often than centre die *with no fault anywhere*. A null world of perfect wafers — or of wafers that all yield the same — would make any fault separable at a glance.

*As implemented (the scenario-library gate), A is a dataset rather than a fixture, and it is **green** at the plane level.* `null_baseline` emits `events: []` with the distractor list populated, and it is not artificially clean: 74 alarms, 43 unscheduled repairs, 21,008 defects, all five bin codes, no perfect wafer, and wafer yield 87.4% ± 4.7. It is also not artificially *quiet* — a test requires every one of those volumes to sit within 1.5× of each fault scenario's, because a control that could be spotted by row count would be measuring the wrong thing. The full-integrity-suite half of the criterion waits for L1–L11 to be wired.

### A4 — Structural integrity (generator self-tests, every build)
All invariants of `SCHEMA_V2_DESIGN.md` §4 and `TEMPORAL_MODEL.md` §6: FK closure, run/step time ordering, zero runs during DOWN/PM, inspection/metrology/test time ordering, reconciliation (defect counts, die-bin sums, state-ribbon tiling), vocabulary closure.

*As implemented (the emission gate), this criterion is **met** and runs on every build.* `src/fabsim/selftest.py` is stage 7 of the pipeline and raises rather than shipping. FK closure is enforced by the database itself — foreign keys are declared and `PRAGMA foreign_key_check` runs before the file is handed over — and the half SQLite cannot express is checked here: a run's chamber belongs to its tool and its recipe matches its step and its wafer's product; runs are ordered and non-overlapping per wafer and none lies inside a DOWN/PM window; metrology, inspections and test follow what they observe and lot finish follows the last activity; defect counts, die-bin sums and state ribbons reconcile; and every categorical value comes from the world's own vocabulary. Verified by mutation across all four families. The checker is given the emitted rows and the world, never the realization — a check that consulted the thing that produced the dataset would be confirming it against its own author.

### A5 — Temporal validity
For each fault scenario: truth `onset` lies strictly inside the horizon with ≥ 30% baseline period before it; affected-cohort series (metrology → defects → yield) depart baseline in causal order; scenario I additionally shows repair time < recovery, with residual ≈ configured (1 − recovery_fraction).

*As implemented (Step 3A), the latent precondition holds:* a mechanism's trajectory is **bit-identical** to its counterfactual before the onset grid point and departs after it; a `ramp` profile climbs monotonically over its `ramp_days` and then sustains; an `edge_uniformity` activation runs through every PM in the window untouched, while a `param_bias` one is partly recentred by each. The observable ordering (metrology → defects → yield) waits for 3C–3E.

*As implemented (Step 3E), the chain reaches yield:* a latent departure moves the fab's own metrology, the moved metrology raises the parametric risk of the die at the radius it moved most, and those die fail more often — verified by counterfactual subtraction on the same timeline, and confined to the wafers the affected chamber processed. Defects reach yield the other way, through physical overlap with a die footprint. What A5 still needs is the *temporal* ordering across the three observable series on a library scenario, which waits for the scenario library.

*Wording change (Step 3B).* This criterion said scenario I's residual would be "≈ configured (1 − recovery_fraction)". The response engine is fab-wide and does not read a scenario's `response` block (ADR-017 §2), so the residual is **emergent** rather than configured: Beta(8, 2) gives a mean recovery of 0.8, i.e. ≈20% residual, and truth records the *realized* quality and fraction for every intervention. Read the criterion as "residual ≈ 1 − **realized** recovery fraction". Nothing is weakened — the check is now against a number the simulator actually produces.

*As implemented (Step 3B), the response precondition holds:* a condition-driven alarm never precedes the onset that produced the departure it reports; a work order follows the alarms that escalated into it; the repair window follows the order by a drawn delay; and the latent recovers when that window ends.

### A5.1 — Severity is an axis, measured against the null *(Step 3A)*
Severity is calibrated in σ of the **null latent distribution** and against nothing downstream — no yield, no defect count, no diagnostic score (ADR-016 §4). For every mechanism, `subtle < moderate < obvious` in measured σ. For the `ar1` latents the realized weekly shift lands within ±25% of the §8 ladder (1.5 / 3 / 6). For the `accumulation` latent the realized shift **exceeds** the nominal, because an unattended load climbs until something cleans it; severity there sets the escalation rate, and the over-run is what scenario I's repair (3B) exists to stop.

### A6 — Causal plausibility (reference recovery — leakage test L11)
Reference SQL (fixtures in `eval/`, not part of fabops) recovers each scenario's intended evidence at moderate severity: B/G chamber-grain yield split + edge-zone defect elevation + edge-CD shift, all temporally aligned with the window; C CD trend detectable before material yield movement; I before/after-maintenance defect-rate contrast. At subtle severity the same queries sit near the natural-variation floor (difficulty axis exists).

*As implemented (Step 3C), A6's precondition is in place and its calibration is honest:* the observable effect of a mechanism is exactly `latent departure × declared sensitivity × channel scale`, verified by counterfactual subtraction, and it scales with the **realized** latent shift rather than the configured severity. The transfer function was calibrated against the null world only — one latent σ moves a channel's weekly aggregate by ≈0.6 of that channel's own weekly σ — and deliberately **not** amplified: a moderate fault moves one wafer by well under a run-noise σ, and recovering it takes aggregation. Whether the reference queries can then recover each scenario's story is A6's question and waits for the scenario library.

*As implemented (the scenario-library gate), the datasets A6 needs now exist, and the criterion is still **blocked** on the reference queries themselves (`eval/` fixtures, which this gate did not build).* What can be said is what the observable plane already shows: B's planted chamber ranks first on all three of its declared channels; G's confound is real and both control comparisons retain data; I's arc is readable from timestamps alone; and C sits near the floor, ranking second of seven — the difficulty ordering A6 expects, measured rather than assumed.

### A7 — Leakage resistance
Full anti-leakage suite L1–L11 green on all five library datasets. Highlighted: L3 mediation residual ≤ 2 pts (the audited 8-pt direct effect is dead), L4 no perfectly separating categorical, L5 classifier confusion in band, L8 seed sensitivity.

### A8 — Entity realism
On every dataset: ≥ 2 chambers per multi-chamber tool actually used; per-chamber run counts nonzero for qualified chambers; gate-etch vs metal-etch tool assignments independent (contingency association ≈ 0, breaking the audited collinearity); recipes resolve per product×step; measurable (benign) tool/chamber offsets exist in null data; product mix spread over lots and time (no one-lot-per-week artifact). On a dataset carrying a routing condition: the dedicated tool's share of the dedicated product's traffic rises inside the window and falls back outside it, while the dedicated product still reaches other qualified tools, other products still reach the dedicated tool, and every qualified chamber of the dedicated tool still carries traffic — dedication moved exposure probability, not eligibility (ADR-015).

### A9 — Demo continuity (ADR-010) *(partly manual review)*
**The Step 3D risk is retired; the criterion is not yet assessed.** The flaw ADR-019 §5 reported — recovery booking a permanent credit against the mean-reverting AR(1) term, leaving repaired chambers several σ from their baseline so that a fault could *reduce* their non-uniformity — is fixed by **ADR-020**. Repaired and unrepaired null chambers now sit at the same distance from their own benign offsets (rms 1.05σ against 1.06σ, from 1.67σ against 1.06σ), and the mechanism that could turn a background repair into an apparent excursion is gone. Measured alongside it, scenario B's edge-ring lift on the affected chamber rose from ×1.67 to ×1.85 at `moderate` (×1.72 → ×1.84 at `obvious`, ×1.55 → ×1.53 at `subtle`), above 1.0 in all nine seed × severity runs, with **nothing retuned** — no defect sensitivity, no channel scale, no severity reference, no world-template constant.

*As implemented (Step 3E), the chain now reaches the criterion's own currency, and the measurement is small.* The die plane is continuous and monotone in severity — the affected chamber's outer-fifth parametric risk rises from 0.0065 (null) to 0.0092 (`moderate`) to 0.0115 (`obvious`) — but the resulting within-product cohort yield deficit on the baseline world at the seeds measured stays **under one point**, against this checklist's 4–10. ADR-021 records the two identified causes: at this seed the affected chamber's benign radial offset partly opposes the activation (the signed-latent consequence of ADR-018), and the parametric channel is a small share of a kill budget the background killer density dominates. Nothing was amplified to change that number, and nothing may be: the constant that would do it is the functional-limit multiple, and moving it to make a demo work is the tuning ADR-018 §4 and this criterion both forbid. It is a calibration question for the scenario-library gate — the candidates being the world's severity calibration, the chamber the demo scenario targets, and the observation model's absolute-versus-relative noise scaling.

*As implemented (the scenario-library gate), the successor dataset exists and A9 remains **blocked**, for a reason that is now precise rather than general.* `chamber_edge_uniformity` is the ADR-010 continuity anchor and it does produce a coherent ETCH-02/B story: the chamber ranks first on edge-site CD, on edge-zone defect share and on alarms. What it does not produce is the checklist's cohort *yield* deficit — the within-product cohort delta is under a point, against 4–10. The remaining items (wafer maps by manual review, reference-query recoverability, A7 on this dataset) need `eval/` and the benchmark, which are later gates. A9 is not marked green, and the yield magnitude was not adjusted to make it green: that constant is the parametric functional limit, and moving it to make a demo work is the tuning ADR-018 §4 and this criterion both forbid.

That says the physical model no longer sabotages the signature. It does **not** say A9 passes. The checklist below is end-to-end and reaches yield, die bins and wafer maps, none of which exist before 3E; the numbers above are a latent- and defect-plane measurement on one scenario at three seeds, and the criterion asks for a demo-level story. A9 stays **open** and is assessed when the chain it describes can be run.

The interpretation is unchanged and is worth restating, because "the signature improved" is exactly the kind of result that invites the wrong target: A9 is *statistical equivalence and demo continuity*, not reproduction of the legacy numerical outputs. The v1 demo's planted ETCH-02 conclusion is not a goal to be matched, and no constant may ever be moved to bring a number closer to it.

`demo_edge_uniformity` (scenario B, default seed) reproduces a **statistically equivalent** ETCH-02 story, defined as this checklist — not exact numbers:
- the affected chamber's tool is worst of the three etch tools on cohort yield; deficit in 4–10 pts;
- elevated edge-ring share and edge-zone defect concentration on the affected chamber's wafers;
- unscheduled maintenance present on the affected tool within the fault window;
- wafer maps visibly show the edge-ring signature (manual review of regenerated figures);
- the story is recoverable **only** through mediated channels (A7 holds on this dataset).

### A10 — Benchmark separation
L9 code-plane lint green; fabops/app/notebooks contain no fabsim import and no truth/scenario path references; truth files valid against `fabsim.truth/v1`; dataset directories contain observable artifacts + `truth/` only, with manifests free of scenario names.

*As implemented (the emission gate), the artifact half is in place.* A dataset directory holds `fab.db`, `fab_database.sql`, `manifest.json` and `truth/` — nothing else — and the manifest is free of scenario names, mechanisms, severities and fault fields, checked by token scan over everything but the `row_counts` block (whose keys are schema table names and are checked by shape). `truth.json` self-identifies as `fabsim.truth/v1`. The directory name is the opaque `dataset_id`. What remains is the truth-schema *validator* and CI wiring.

*As implemented (Step 3A):* the code-plane lint runs in both directions and over subpackages — `src/fabsim/**` imports no `fabops`, and `src/fabops/**` and `app/**` import no `fabsim` and mention no `scenarios/`, `truth/` or `truth.json`. The hidden `Realization` is in-memory only: no path, no registry, no singleton, so an observable projection can only be handed it. No truth file and no dataset directory exists yet.

### A11 — Backward compatibility
The legacy surfaces are untouched: `data/generate_fab_db.py` byte-identical, legacy `data/fab.db`/`fab_database.sql` unchanged, all 27 existing tests green, dashboard and notebook run exactly as at Phase 0 close. New code lives only in `src/fabsim/`, `scenarios/`, `eval/` (fixtures), and new tests.

## B. Phase 1 deliverables checklist

- [~] `src/fabsim/` package per `FABSIM_DESIGN.md` §3, stdlib-only — done; the `fabsim-build` console entry point is not wired (`fabsim.emit.build_dataset` is the function it would call)
- [x] `scenarios/worlds/baseline_fab_v1.*` + five scenario configs (A, B, C, G, I per `SCENARIO_SPECIFICATION.md` §4)
- [x] Schema v2 DDL + emit path (SQLite + portable dump + manifest) — `src/fabsim/emit/`
- [x] Truth emitter (`fabsim.truth/v1`) — `src/fabsim/emit/truth.py`; the schema *validator* remains open
- [x] Generator self-test suite (A4) wired into every build — `src/fabsim/selftest.py`
- [ ] Anti-leakage suite L1–L11 + reference-query fixtures in `eval/`
- [ ] Five library datasets generated deterministically in CI
- [x] pytest coverage: rng substreams, routing, mechanism math, kill model, invariants — *the generation planes; the emit/benchmark surfaces below remain open*
- [x] `scenarios/README.md` maintainers' index (id ↔ slug ↔ answer summary)
- [ ] Documentation updates confined to: this design set marked "as implemented" deltas, README pointer to fabsim (no claims beyond what A1–A11 prove)

## C. Exit gate

Phase 1 closes when A1–A11 are green in CI and a human review confirms A9's manual items. Only then does Phase 2 (semantic layer v2) begin. Retirement of the legacy generator remains **out of scope** — it happens no earlier than the phase in which every consumer surface has migrated (ADR-010).
