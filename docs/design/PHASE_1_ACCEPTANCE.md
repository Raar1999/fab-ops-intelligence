# Phase 1 Acceptance — FabSim + Schema v2

**Status:** design-gate deliverable. These are the exact tests Phase 1 implementation must pass before the phase closes and before any Phase 2 work begins. Each criterion is automated unless marked (manual review).

---

## A0. Where the criteria stand (the benchmark gate)

`src/fabeval/` scores A1-A11 over the five-scenario library and reports one of three verdicts. `PARTIAL` is used deliberately and often: several criteria have a half this gate settles and a half that needs CI or a manual review, and calling those PASS would make the matrix a worse instrument than no matrix. Since the A9/A6 review gate, A6's severity sweep runs (`fabeval.build_sweep`) and its PARTIAL is a measurement rather than an unbuilt check. Run with `fabeval.build_library` + `fabeval.evaluate`; `fabeval.render` prints the table below.

**Three of these rows were about the criteria rather than about the simulator, and ADR-026/027/028 settle all three.** A6's floor and A7's L7 both thresholded a maximum over chambers against a number that means one chamber's standing; both now read against a *derived null reference distribution* (`src/fabeval/reference.py`) at a declared level, which converges and which no world constant can move. A9's numeric band is retired as binding, and its cohort-yield *ranking* is retired as a gate — measured to be satisfied by a fault-free world one time in three. Each retired item is still reported; none is deleted.

**Diagnosis is authorized as of ADR-029** and its acceptance is *not* part of A1-A11: this document grades FabSim and schema v2. The engine's own criteria — the eight anti-leakage checks, truth invariance and its mirror, determinism, and the permutation-null validity criterion — live in `DIAGNOSIS_CONTRACT.md` §5.1 and §6, and are executed by `tests/fabops/`. A1-A11 are untouched by the engine's existence.

**Nothing was made green by relaxation.** A6 stays PARTIAL; A7 and A9 became PARTIAL because their checks stopped measuring things a correct simulator fails. The matrix has no blocked criterion — a statement about the criteria having been repaired, not about the simulator having improved.

**A9 reached PASS at the Final Acceptance gate (ADR-030), and it is the one status change in this matrix's history that needs its provenance stated with it.** Its last unrun item — the manual wafer-map review — was **performed**, found not met, measured to be *flat across the entire severity ladder* (×1.052 / ×1.060 / ×1.059), traced to the magnitude the audited direct-label term produced, and retired as a gate on the ADR-028 pattern. What made that a repair rather than a relaxation is the other half of the same measurement: the two items A9 still gates on had never had a reference distribution either, and computing it on twelve fault-free worlds showed both of them discriminate (rank 1 of 7 at z = +2.344 and +2.650 on the faulted world; 2/12 and 0/12 on the nulls). A9 passes on items that separate a faulted world from a fault-free one, and every retired item is still printed in its evidence.

Every remaining PARTIAL names genuinely unrun work: a CI reference-image job (A1), checks inapplicable to a null (A7), a criterion whose wording assumes a lag the physics does not have (A5), a measured shortfall on the declared channels (A6), and halves delegated to other criteria (A3, A8, A11).

| | Status | What is outstanding |
|---|---|---|
| A1 | PARTIAL | checks 1-3 green on every scenario; check 4 (reference-image `fab.db` byte compare) is a CI-environment job |
| A2 | **PASS** | - |
| A3 | PARTIAL | the null is populated, varied and no quieter than the fault scenarios; the "full integrity suite" half is A4 and the L7/L10 half is A7 |
| A4 | **PASS** | - |
| A5 | PARTIAL | onset placement and the alarm->repair ordering hold; the metrology->defect->yield *series* ordering is not asserted (see below) |
| A6 | PARTIAL | the difficulty axis exists at both ends - realized severity rises and subtle stays inside benign variation - but at moderate the planted chamber clears the declared level on none of the three channels A6 names as evidence (p = 0.076 / 0.105 / 0.338); it clears on alarms, which corroborates (ADR-027) |
| A7 | PARTIAL | L1-L11 green where applicable, including both halves of L7; 18 checks are inapplicable to their dataset, which is why this is not PASS |
| A8 | PARTIAL | chamber usage and etch independence hold; recipe and benign-offset items belong to A4 and 3C |
| A9 | **PASS** | the declared chain reaches the die plane, the edge-ring signature leads (z = +2.344 against 2/12 fault-free worlds) and the fault window carries maintenance. Three items are reported and none gates: the 4-10 band (ADR-027), the cohort-yield ranking (ADR-028) and the wafer-map review (ADR-030 - **run** at the Final Acceptance gate, not met, and flat across the severity ladder) |
| A10 | **PASS** | - |
| A11 | PARTIAL | the legacy artifacts are present and still schema v1; the 27-test behavioural half is the test suite's |

**A5's wording assumes a lag the physics does not have.** The criterion asks that the affected-cohort series "depart baseline in causal order (metrology -> defects -> yield)". In this model those three are *simultaneous*: a run's latent state moves its own FDC and metrology, the same state feeds that wafer's defect intensity, and the die grid reads both on the same wafer. There is no lag to order them by, and asserting one would be asserting an artifact. What A5 does check, and what carries its intent, is that the *response* arc is ordered - onset before the condition alarm before the repair window - and that onset sits inside the horizon with at least 30% baseline before it. Recorded here rather than quietly dropped.

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

*As implemented (the A9/A6 review gate), the sweep runs, and it splits the criterion cleanly into a half that passes and a half that does not.* `fabeval.sweep` builds one scenario at each rung and reads the reference queries against a **natural-variation floor** — the worst standing *any* chamber reaches on worlds with nothing wrong, over three null seeds.

| | realized σ | edge_cd | edge_defect_share | alarms | yield_split |
|---|---|---|---|---|---|
| subtle | 1.61 | +1.88 | +2.09 | +2.14 | +1.16 |
| moderate | 3.22 | +2.65 | +2.34 | +4.34 | +1.25 |
| obvious | 4.00 | +3.02 | +2.22 | +3.97 | +1.26 |
| **null floor** (3 seeds) | | **2.84** | **3.29** | **5.05** | **2.26** |

The **difficulty axis exists**: realized severity rises 1.61 → 3.22 → 4.00, and edge-CD and yield-split rise with it; subtle sits at or below the floor on every channel, which is what the criterion's last sentence asks for. The **recovery half fails**: at moderate the planted chamber does not exceed the floor on *any* single channel. Ranking first is not separation — on a null world some chamber always ranks first, and it does so at a comparable σ. A6 is therefore **PARTIAL** with a measured reason rather than an unbuilt one.

This is not a defect. Rule F11 puts the benign per-chamber offsets in the subtle-severity band and states that a fault and an offset differ "only by shape in time"; the same overlap is why L6 passes. What it means is that the evidence that exists is multi-channel and temporal, and combining it is the diagnosis engine's job, not a reference query's — see `DIAGNOSIS_CONTRACT.md` §5. The floor is deliberately read from **at least three** null realizations (`sweep.MINIMUM_FLOOR_SEEDS`), because a single-seed floor reported this criterion as PASS during the gate: seed 42's edge-CD floor is 2.31, below moderate's 2.65, while three seeds put it at 2.84. `natural_variation_floor` now refuses an under-sampled input rather than returning a number that flatters whatever it is compared against, and a test pins the refusal.

*As investigated (the calibration resolution gate), the difficulty axis is confirmed calibrated and the floor is confirmed unusable — for a reason the paragraph above gets wrong.* Full evidence in **ADR-026**; two results.

**The floor does not converge, so the verdict it produces is a function of how many nulls the benchmark could afford.** It is `max over seeds (max over chambers |z|)` — a cumulative maximum, monotone in the seed count, with no limit:

| null seeds | 1 | 3 | 5 | 8 | 12 |
|---|---|---|---|---|---|
| edge_cd | 2.31 | 2.84 | 5.38 | 5.38 | 5.38 |
| edge_defect_share | 1.82 | 3.29 | 3.29 | 4.08 | 5.24 |
| alarms | 2.50 | 5.05 | 5.05 | 13.08 | 13.08 |
| yield_split | 2.26 | 2.26 | 2.53 | 3.02 | 3.59 |

Raising `MINIMUM_FLOOR_SEEDS` from 1 to 3 was right about the direction and wrong about the cure: three draws do not stabilise a divergent statistic. `separated_at_moderate` is empty at three seeds, at twelve, and at any larger number, for a simulator of any quality — because the planted chamber's standing is *one chamber's* and the floor is a *maximum over seven chambers and every seed*. **Neither the floor nor the seed count was moved**, and no verdict was upgraded: correcting the comparison means choosing what it should compare instead, which is an architecture decision (ADR-026's three options).

**Read against a reference that does converge, the ladder is exactly what §8 specifies.** Tail probability of the null's own *per-chamber* |z| distribution at or above the planted chamber's standing — one specified chamber against unspecified single chambers, measured over 84 chamber-seeds (219 on alarms):

| | edge_cd | edge_defect_share | alarms | yield_split |
|---|---|---|---|---|
| subtle (1.61σ) | 0.167 | 0.143 | 0.059 | 0.429 |
| moderate (3.22σ) | **0.060** | 0.095 | **0.018** | 0.405 |
| obvious (4.00σ) | 0.036 | 0.131 | 0.023 | 0.405 |

Subtle sits near the detection floor; moderate is detectable on two channels and monotone in severity on both. Yield alone carries no severity information at these magnitudes, which A9's finding explains. The world was not retuned to obtain this — it is what the existing world already does when the comparison is like-for-like.

*As decided (the architecture decision gate), A6 reads against a declared reference and stays **PARTIAL** — for a reason that is now a statement about the simulator rather than about the evaluator's budget.* **ADR-027**; three parts.

**The reference.** The planted chamber's standing is converted to an **exceedance probability** against `fabeval.reference` — how often a benign chamber reaches at least that far, derived from exchangeability and the chamber count. That currency converges, and unlike sigma it means the same thing on a channel read at 7 chambers and one read at 18. `natural_variation_floor` is retained and still reported as evidence, but it is no longer a threshold.

**The measurement**, against the declared `ALPHA = 0.05`:

| | edge_cd | edge_defect_share | yield_split | *alarms* |
|---|---|---|---|---|
| subtle (1.61σ) | 0.173 | 0.137 | 0.372 | *0.063* |
| moderate (3.22σ) | 0.076 | 0.105 | 0.338 | ***0.001*** |
| obvious (4.00σ) | 0.051 | 0.119 | 0.335 | ***0.002*** |

**The verdict, and the two decisions inside it.** A6's evidence channels are **the three its own text names** — "chamber-grain yield split + edge-zone defect elevation + edge-CD shift". `alarms` is carried because the criterion's temporal alignment is read off it, but A6 does not list it as evidence, so it corroborates and cannot satisfy the criterion alone; letting the strongest channel carry a criterion that never asked for it is how a benchmark quietly becomes easier. And the difficulty axis is now checkable from **both** ends: subtle must stay inside benign variation (it does, on all four channels) and moderate must clear it (it does not, on any declared one). A subtle rung that separated now *blocks* A6 — a failure mode the old formulation could not express, and one a test exercises.

So the evidence A6 asks for is present and multi-channel, and no single declared query recovers it at moderate. Combining channels is the diagnosis engine's job (`DIAGNOSIS_CONTRACT.md` §5), which is why the benchmark was built first.

*One terminological correction (ADR-029).* Every exceedance figure in this criterion is the standing of **one chamber that truth names, on one channel that A6 names**. A diagnosis engine names neither in advance: it faces ~19 candidates x ~17 channels and must pay for that multiplicity. The two quantities are not comparable, and A6's table is not a forecast of engine performance — a mistake made once already, in the paragraph ADR-026 corrected. A6 remains a statement about *the simulator's difficulty axis*; how well an engine does is `fabeval`'s question and is measured on a population, never inferred from here.

### A7 — Leakage resistance
Full anti-leakage suite L1–L11 green on all five library datasets. Highlighted: L3 mediation residual ≤ 2 pts (the audited 8-pt direct effect is dead), L4 no perfectly separating categorical, L5 classifier confusion in band, L8 seed sensitivity.

*As measured (the A9/A6 review gate), A7 is **BLOCKED**, and it was reported PARTIAL before only because of how thinly it was sampled.* Two independent one-draw problems hid the same failure: the null was built at a single seed, and the A7 verdict was assembled from the seed-42 rows alone, so leakage results the suite had already computed at other seeds were discarded before scoring. With the null at three seeds and every row scored, **L7 fails at seeds 101 and 2024** — the worst chamber on a fault-free world reaches 3.29σ on edge-defect share and 2.84σ on edge CD, against L7's 2.5σ floor and against the 1.88 / 2.09 / 1.16σ a *subtle* fault actually produces. On a fault-free world a benign chamber therefore stands out more than a subtle fault does. Neither the floor nor the world was adjusted; see `ANTI_LEAKAGE_DESIGN.md` §3.6 and ADR-025 §5. The other ten checks are unaffected.

*As investigated (the calibration resolution gate), A7 stays **BLOCKED**, and the failure is now traced to the check rather than to the world.* Full evidence in **ADR-026**; three results.

1. **The failure rate is what L7's own statistic produces on a correct null.** L7 reduces a null world to `max over chambers |leave-one-out z|` and compares it against a hardcoded 2.5. For seven exchangeable chambers — chambers differing by *nothing at all* — that maximum has median 2.687 and exceeds 2.5 with probability **0.598**. L7 evaluates three such channels and fails if any trips, so its expected failure rate on a healthy fab is near 0.9. Measured on twelve fault-free worlds: **10 of 12 fail**. Two of three is the modal outcome, not a signal.
2. **This world carries no excess benign structure.** Pooled per-chamber |z| over twelve nulls is 1.129 / 1.124 / 1.084 (edge CD / edge-defect share / yield split) against an exchangeable-Gaussian reference of 1.123 — if anything marginally quieter. So the open question ADR-025 §5 raised, whether F10/F11's benign spread outgrew the `subtle` rung, is answered **no**.
3. **No world constant can move it.** `zscore` divides by the realized between-chamber spread and is invariant under any common rescaling or shift, so the null distribution of `max|z|` depends on the chamber count and on no magnitude the world declares. Measured across six world calibrations spanning 16× in benign latent offset and 20× in observation-plane chamber offset: fourteen of eighteen fault-free worlds fail, and a fifth of the declared benign chamber variation fails exactly as often as the baseline.

**The threshold was not lowered, the null was not put back to fewer seeds, and no simulator constant was touched** — the last would have been pointless as well as forbidden. A7 stays BLOCKED because correcting L7 means deciding what "below the subtle-severity floor" compares, and that decision belongs to an architecture gate (ADR-026 lists three options). The measurement is pinned by `tests/fabeval/test_floor_semantics.py` so it cannot be lost.

*As decided (the architecture decision gate), A7 is **PARTIAL**.* **ADR-027** replaced L7's undeclared constant with the distribution of its own statistic under exchangeability, and split the criterion into the two questions it was conflating — see `ANTI_LEAKAGE_DESIGN.md` §3.8 for the full statement.

1. **A per-world action limit.** No chamber on a null may exceed the per-chamber critical value at the fab's *own* control-limit convention (3σ, the multiple eight of nine `alarms.codes` declare — 6.46 at seven chambers). Measured: **0 of 12** fault-free worlds trip it; the worst chamber ever seen is 5.38.
2. **A population calibration**, cross-dataset like L8: the exceedance rate over every fault-free world, at the declared screening level 0.05, must not be inflated beyond chance. Measured: **9/252 = 0.036** against 0.050 expected on twelve worlds, **1/63 = 0.016** on the three the library builds, and **0/252** at the fab's stricter 3σ level.

**A7 is PARTIAL and not PASS** because 18 checks are inapplicable to their dataset — a null has no affected cohort, so L3/L4/L6/L10 have nothing to separate, and L7 does not apply to a faulted one. Counting those as passes would be counting checks that never ran.

**What the correction cost, stated rather than glossed.** Against the derived limit, a poisoned null is caught at a 30% single-chamber shift (19.2σ) and at 10% (10.8σ), and a 5% shift passes (4.7σ). The old constant caught 2% — while flagging nine healthy worlds in ten, and while naming the *wrong* chamber at 2%. What was given up is a check that fired on the benign structure rule F11 requires the null to contain; the mutation test still fails on a poisoned null, and the population half covers the failure mode no per-world threshold can see.

### A8 — Entity realism
On every dataset: ≥ 2 chambers per multi-chamber tool actually used; per-chamber run counts nonzero for qualified chambers; gate-etch vs metal-etch tool assignments independent (contingency association ≈ 0, breaking the audited collinearity); recipes resolve per product×step; measurable (benign) tool/chamber offsets exist in null data; product mix spread over lots and time (no one-lot-per-week artifact). On a dataset carrying a routing condition: the dedicated tool's share of the dedicated product's traffic rises inside the window and falls back outside it, while the dedicated product still reaches other qualified tools, other products still reach the dedicated tool, and every qualified chamber of the dedicated tool still carries traffic — dedication moved exposure probability, not eligibility (ADR-015).

### A9 — Demo continuity (ADR-010) *(partly manual review)*
**The Step 3D risk is retired; the criterion is not yet assessed.** The flaw ADR-019 §5 reported — recovery booking a permanent credit against the mean-reverting AR(1) term, leaving repaired chambers several σ from their baseline so that a fault could *reduce* their non-uniformity — is fixed by **ADR-020**. Repaired and unrepaired null chambers now sit at the same distance from their own benign offsets (rms 1.05σ against 1.06σ, from 1.67σ against 1.06σ), and the mechanism that could turn a background repair into an apparent excursion is gone. Measured alongside it, scenario B's edge-ring lift on the affected chamber rose from ×1.67 to ×1.85 at `moderate` (×1.72 → ×1.84 at `obvious`, ×1.55 → ×1.53 at `subtle`), above 1.0 in all nine seed × severity runs, with **nothing retuned** — no defect sensitivity, no channel scale, no severity reference, no world-template constant.

*As implemented (Step 3E), the chain now reaches the criterion's own currency, and the measurement is small.* The die plane is continuous and monotone in severity — the affected chamber's outer-fifth parametric risk rises from 0.0065 (null) to 0.0092 (`moderate`) to 0.0115 (`obvious`) — but the resulting within-product cohort yield deficit on the baseline world at the seeds measured stays **under one point**, against this checklist's 4–10. ADR-021 records the two identified causes: at this seed the affected chamber's benign radial offset partly opposes the activation (the signed-latent consequence of ADR-018), and the parametric channel is a small share of a kill budget the background killer density dominates. Nothing was amplified to change that number, and nothing may be: the constant that would do it is the functional-limit multiple, and moving it to make a demo work is the tuning ADR-018 §4 and this criterion both forbid. It is a calibration question for the scenario-library gate — the candidates being the world's severity calibration, the chamber the demo scenario targets, and the observation model's absolute-versus-relative noise scaling.

*As implemented (the scenario-library gate), the successor dataset exists and A9 remains **blocked**, for a reason that is now precise rather than general.* `chamber_edge_uniformity` is the ADR-010 continuity anchor and it does produce a coherent ETCH-02/B story: the chamber ranks first on edge-site CD, on edge-zone defect share and on alarms. What it does not produce is the checklist's cohort *yield* deficit — the within-product cohort delta is under a point, against 4–10. The remaining items (wafer maps by manual review, reference-query recoverability, A7 on this dataset) need `eval/` and the benchmark, which are later gates. A9 is not marked green, and the yield magnitude was not adjusted to make it green: that constant is the parametric functional limit, and moving it to make a demo work is the tuning ADR-018 §4 and this criterion both forbid.

That says the physical model no longer sabotages the signature. It does **not** say A9 passes. The checklist below is end-to-end and reaches yield, die bins and wafer maps, none of which exist before 3E; the numbers above are a latent- and defect-plane measurement on one scenario at three seeds, and the criterion asks for a demo-level story. A9 stays **open** and is assessed when the chain it describes can be run.

The interpretation is unchanged and is worth restating, because "the signature improved" is exactly the kind of result that invites the wrong target: A9 is *statistical equivalence and demo continuity*, not reproduction of the legacy numerical outputs. The v1 demo's planted ETCH-02 conclusion is not a goal to be matched, and no constant may ever be moved to bring a number closer to it.

*As investigated (the A9/A6 review gate), the chain was traced end to end and no implementation defect exists. The 4–10 band is unreachable, and it contradicts the paragraph above.* Full evidence is in **ADR-025**; the three results that matter here:

1. **The causal chain is intact and correctly localised.** By counterfactual subtraction on an identical timeline: latent +2.51 σ_ref → edge-CD signed d/L +0.003 → +0.464 tolerances (mid +0.304, **centre exactly unchanged**) → +17 cohort defects → outer-fifth parametric risk 0.00657 → 0.00917 with the background kill delta **exactly 0.00000000** → exposed-cohort yield **−0.058 pts**, with every unexposed wafer at **exactly +0.0000**. Every stage fires, is correctly signed, and leaks into nothing it should not. The +0.47 pts A9 reports is mostly the chamber's pre-existing benign character; the mechanism-attributable effect is 0.058 pts.
2. **No setting of the governing constant reaches the band.** Recomputing the parametric kill off already-emitted metrology under hypothetical functional limits: at 3.0 tolerances (current) B is −0.13 pts and the null loses 0.46% of its die; at 1.5, +0.52 pts and 4.45%; at 1.0, +2.26 pts and **8.91%** — an absurd healthy fab, still short of the 4-point floor. The null's edge |d|/L (mean 0.439, p95 1.403) and the exposed cohort's (mean 0.492, max 1.832) overlap almost entirely, so any limit low enough to kill B's edge die kills the null's at nearly the same rate. **Nothing was tuned; this was measured, not applied.**
3. **The band is the audited defect's magnitude.** 4–10 points comes from the v1 demo whose yield formula carried `−0.08 if bad_tool`, and whose audit found "8.0 of ETCH-02's ~12 yield points are a direct label effect". Requiring FabSim to reproduce it is requiring it to reproduce the term ADR-004 exists to abolish — which is what the paragraph above already forbids.

The classification is therefore **(D) an acceptance-interpretation issue compounded by (B) a physically plausible outcome of the frozen model** — not (A) an implementation defect and not (C) an underpowered scenario. **The simulator was left unchanged**, per this gate's own instruction. Resolving it needs a decision no engineering change can substitute for: restate the checklist item in mediated terms with a band derived from the current physics; keep the band and accept A9 as permanently unmet on the baseline world; or recalibrate the world's severity scale, which moves every dataset and needs its own gate. Until one is taken, A9 stays **BLOCKED**.

*As verified independently (the calibration resolution gate), the finding holds and the argument no longer needs a sweep to make it.* **ADR-026** §7; three additions.

1. **An upper bound, from the kill budget itself.** The hidden `DieOutcome` causes on the null at the current limit: 87.440% pass, 11.127% background, 0.634% defect, **0.799% parametric**. The parametric channel's *entire* share of a healthy fab's die loss is 0.80 yield points. A fault that killed every parametrically vulnerable die in its cohort and left the control untouched could not reach one point, let alone four. The band is over-subscribed by a factor of five before any question of tuning arises — and this needs no hypothetical, only the fab as built.
2. **The limit sweep, recomputed by full re-probe.** At 3.0 (current) the exposed cohort out-yields its within-product peers by **+0.428 pts** — matching the truth artifact's own `expected_impact` exactly — and it goes on out-yielding them at 2.5, 2.0, 1.5 and 1.2. Only at a limit of 1.0, where the null fab has lost 15.8% of its die and yields 75.3% instead of 87.4%, does a deficit appear at all, and it reaches 1.9 points against a 4-point floor.
3. **The yield channel carries no severity information here.** The cohort delta is +0.466 / +0.428 / +0.409 across subtle / moderate / obvious — flat, faintly *decreasing*, and inside one standard error (0.11–0.46 pts on a 93-wafer cohort) at every rung. What the +0.47 measures is the chamber's benign character; ADR-025 §1's mechanism-attributable 0.058 pts is what the fault contributes.

**The documentary contradiction is wider than A9.** The same band appears as "≈ 4–8 pts" in `CAUSAL_MECHANISM_MODEL.md` §8 and `SCENARIO_SPECIFICATION.md` §4 B, and all three trace to `docs/audit/SYNTHETIC_DATA_AUDIT.md` #5 — which contains the decisive number: *"of ETCH-02's ~12-pt deficit, 8.0 pts are this direct label effect, only ~3.7 pts flow through defects."* **The audited v1's own mediated remainder was 3.7 points, below the band's own 4-point floor.** The band was never reachable through mediation in the system it was measured from. Nothing was changed; the number stays in all three documents, annotated, until a decision retires or restates it.

*As decided (the architecture decision gate), **the band is retired as binding and preserved as a historical reference** — and A9 stays BLOCKED, on a different item.* **ADR-027** §6.

**The band.** Its provenance settles it: a legacy numerical observation of a system whose deficit was two-thirds direct label effect, promoted to a numerical target, contradicting A9's own prose and ADR-010, and unreachable by a factor of at least 4.4. `fabeval.acceptance.LEGACY_COHORT_BAND` keeps the number and `check_a9` reports it in the evidence at every verdict; it no longer gates. A test pins both halves, so retiring it cannot become deleting it and cannot quietly become enforcing it again.

**What blocks A9 now.** With the band out of the way the checklist's first item fails on its own terms: *the affected chamber's tool is worst of the three etch tools on cohort yield* — and it is not. ETCH-03 is, at −0.16 pts against ETCH-02's −0.08. That is a **ranking** failure rather than a magnitude one, and its cause is measured: the between-tool benign spread on cohort yield is **0.41 pts** against a mechanism-attributable effect of **0.058 pts**, so which tool ranks worst on yield is decided by benign variation at every severity.

**The open decision, stated so the next gate can take it.** Whether demo continuity should keep a yield item at all is *not* settled here, because both answers have consequences no evaluator change can contain: dropping the item changes what the project's flagship demo claims, and making the item reachable means recalibrating the composition ADR-026 §1 identified — ADR-018 §4's deliberately un-amplified observation transfer against ADR-021 §5's 3.0-tolerance functional limit — which moves every dataset and needs its own gate. The band was retired because its provenance settles it; the item is not, because its provenance does not.

*As decided (the final A9 gate), **cohort yield is retained as reported evidence and removed as an attribution gate**, and A9 becomes **PARTIAL**.* **ADR-028**; the measurement that settles it, and the three things it does not license.

**The measurement.** On **twelve fault-free worlds** the worst etch tool on cohort yield is ETCH-01 four times, ETCH-02 four times and ETCH-03 four times — an exact three-way split. *The item is satisfied by chance one time in three on a world with no fault in it.* A criterion a null world passes a third of the time is not an attribution criterion; it is the third and last of the criteria these gates found whose reference distribution had never been computed.

**The grain is not the fix**, which was the obvious first hypothesis: A9 asks a *tool*-grain question about a *chamber*-grain fault. At chamber grain the planted chamber ranks 1st, 1st and **6th of 7** across the demo's three seeds — at seed 2024 nearly the best-yielding chamber in the fab — and its standing does not move with severity (z = +1.16 / +1.25 / +1.26, p ≈ 0.34 at every rung). On fault-free worlds ETCH-02/B itself ranks 2nd of 7 on five of twelve.

**Why this is faithful rather than a relaxation.** `RCA_AUDIT` found v1's "three independent signals" were "three readouts of one boolean", and yield is the variable `−0.08 if bad_tool` wrote into — so requiring yield to *rank* the tool is requiring the mechanism by which v1 gave its answer away, which is the same argument that retired the band applied to the ranking. A9's own last bullet already forbids it ("recoverable **only** through mediated channels"), and yield is the last stage of a chain the design gives "independent noise at every stage", so it is the most attenuated channel by construction.

**What it does not license.** *(i)* Nothing is removed from the simulator, the schema, the queries or the truth artifact — `chamber_yield_split` is still computed, still reported here, and still one of A6's three declared evidence channels and one of L7's three reference channels. *(ii)* "Yield is weak in this scenario" is **not** "yield should never be used diagnostically" — a later scenario built around a defect- or parametric-dominated mechanism may well make it primary. *(iii)* Whether the world's composition should be recalibrated so yield carries more signal is untouched and remains a separate physics gate.

**What gates the downstream half instead.** The demo's *declared* `causal_chain` must still reach `die_bins` and `wafer_yield` (`fabeval.acceptance.DEMO_CHAIN_ENDPOINTS`). That chain is derived from the world's own sensitivity maps, so it cannot disagree with the physics, and severing the die plane fails A9 loudly. The *magnitude* stays gated where it can actually be measured — counterfactual subtraction in `tests/fabsim/test_die.py`, which asserts the mechanism reached die at all, reached only exposed wafers, and rises monotonically with severity by at least 1.2× — because that is the only instrument that can see an 0.058-point effect.

*As reviewed (the Final Acceptance gate), the manual item was **run**, found not met, measured to be unreachable, and retired as a gate — and A9 becomes **PASS** on what remains.* **ADR-030**; three results.

**The review happened; it did not pass.** Four cohorts of scenario B at seed 42, GATE layer, geometry only: the planted chamber after onset, the same chamber before onset, the other chambers in the same window, and the same chamber on a fault-free world. Their radial profiles overlap. Edge-zone shares are 0.4087 / 0.4155 / 0.3905 / 0.3984 — the faulted cohort is *below* its own pre-onset window — and the excess is ≈25 edge defects on a 1500-defect cohort.

**It is not a severity problem.** The geometric lift over peers is ×1.052 / ×1.060 / ×1.059 at subtle / moderate / obvious: **flat**, while the latent moves 1.61 → 3.22 → 4.00 σ. No rung of the declared ladder makes a wafer map show anything, so the item was not waiting for a louder fault. The legacy figure it was written against sits at ×1.78 on the same measure (and ×3.86 on an `EDGE_RING` class schema v2 abolished as leakage), which is the magnitude the audited `−0.08 if bad_tool` term produced — so the item asks the successor to reproduce the defect ADR-004 exists to remove.

**What makes retiring it a repair rather than a relaxation.** The two items A9 still gates on had never had a reference distribution computed either. It has been, on twelve fault-free worlds, and both pass it: edge-zone defect share reaches rank 1 of 7 at z = +2.344 on the faulted world against 2/12 on the nulls (chance 1/7, mean z −0.322), and edge CD reaches rank 1 of 7 at z = +2.650 against 0/12. **Unlike the cohort-yield ranking ADR-028 retired, these separate a faulted world from a fault-free one.** A9 therefore passes on items that discriminate, with all three retired findings still printed in its evidence.

`demo_edge_uniformity` (scenario B, default seed) reproduces a **statistically equivalent** ETCH-02 story, defined as this checklist — not exact numbers:

*Gating:*
- the declared causal chain still reaches the die plane and wafer yield *(ADR-028; the successor to the yield item)*;
- elevated edge-ring share and edge-zone defect concentration on the affected chamber's wafers — **measured against a reference distribution for the first time (ADR-030): rank 1 of 7 at z = +2.344 on the faulted world, against 2/12 fault-free worlds (chance 1/7) at a mean z of −0.322. Edge CD deviation likewise: rank 1 of 7 at z = +2.650, and 0/12 on the null**;
- unscheduled maintenance present on the affected tool within the fault window;
- the story is recoverable **only** through mediated channels (A7 holds on this dataset — L1–L11 green on it, 1 inapplicable).

*Reported, never gating:*
- ~~wafer maps visibly show the edge-ring signature (manual review of regenerated figures)~~ *(**run at the Final Acceptance gate and retired as a gate by ADR-030.** The review was performed, not waived, and the item is not met on its literal wording: the planted chamber's geometric edge-zone lift over its peers is ×1.052 / ×1.060 / ×1.059 at subtle / moderate / obvious — **flat across the whole severity ladder**, so no rung makes a wafer map show anything. Four cohorts — faulted, the same chamber pre-onset, other chambers in the same window, and the same chamber on a fault-free world — have overlapping radial profiles, and the excess is ≈25 edge defects on a 1500-defect cohort. The legacy figure the wording was written against reaches ×1.78 on the same geometric measure and ×3.86 on an `EDGE_RING` class schema v2 abolished as leakage (ADR-019 §4); that magnitude is what the audited `−0.08 if bad_tool` term produced. `fabeval.acceptance.WAFER_MAP_LADDER_LIFT` and `WAFER_MAP_NULL_RANK1` keep the numbers and `check_a9` reports them at every verdict; a test pins both directions, so retiring the item cannot become deleting the finding and cannot quietly become enforcing it again)*;
- ~~the affected chamber's tool is worst of the three etch tools on cohort yield~~ *(**retired as a gate by ADR-028** — satisfied by a fault-free world one time in three. The tool standing, the cohort delta and the between-tool spread are printed in the evidence at every verdict)*;
- ~~deficit in 4–10 pts~~ *(**retired as binding by ADR-027 §6 and preserved as a historical reference.** It is the magnitude of the audited `−0.08 if bad_tool` term: the v1 deficit it was read from was 8.0 points of direct label effect and ~3.7 points mediated, so the band's own floor sits above what the legacy system produced through physics. `fabeval.acceptance.LEGACY_COHORT_BAND` keeps the number and the check reports it; nothing gates on it)*.

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
- [x] Anti-leakage suite L1–L11 + reference-query fixtures in `eval/` — delivered as `src/fabeval/` (`leakage.py`, `queries.py`, `fixtures.py`); the *role* is exactly what this line describes and only the directory differs, because a root-level `eval/` would need the `sys.path` manipulation ADR-009 removed and `eval` is a builtin name. **ADR-024** records the relocation
- [ ] Five library datasets generated deterministically in CI — the determinism itself is verified locally (A1 checks 1–3, and `tests/fabsim/test_emit.py` re-verifies emission across processes under a changed hash seed, locale and timezone); what waits on CI is running it *there*
- [x] pytest coverage: rng substreams, routing, mechanism math, kill model, invariants — *the generation planes; the emit/benchmark surfaces below remain open*
- [x] `scenarios/README.md` maintainers' index (id ↔ slug ↔ answer summary)
- [ ] Documentation updates confined to: this design set marked "as implemented" deltas, README pointer to fabsim (no claims beyond what A1–A11 prove)

## C. Exit gate

Phase 1 closes when A1–A11 are green in CI and a human review confirms A9's manual items. Only then does Phase 2 (semantic layer v2) begin. Retirement of the legacy generator remains **out of scope** — it happens no earlier than the phase in which every consumer surface has migrated (ADR-010).
