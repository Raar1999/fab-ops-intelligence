# Anti-Leakage Design — Preventing the Generator From Revealing Its Own Answer

**Status:** Phase 1 design for review.
**Motivation:** the audit's central finding — 8.0 of ETCH-02's ~12 yield points are a direct label effect, and the conclusion is additionally compiled into the analysis. Schema v2 removes the code-level leak by construction (fabops never sees configs/truth); this document handles the subtler problem: **the data itself must not be an answer key.**

---

## 1. Leakage taxonomy (what we defend against)

| Class | Description | v1 instance |
|---|---|---|
| T1 Direct label injection | target variable adjusted from fault identity | `−0.08 if bad_tool` in yield |
| T2 Feature-name leakage | fault identity readable from names/labels | (avoided in v1; guarded anyway) |
| T3 Categorical encoding | a category value that exists only for affected entities | risk in alarm codes / maint text |
| T4 Deterministic mapping | fault ⇒ signature with probability 1 | wafer signature ⇒ 75% of defect types; type ⇒ coordinates exactly |
| T5 Perfect correlation | an observable separating affected/unaffected perfectly | 4σ single-GROUP-BY yield gap; 100% gate/metal etch collinearity |
| T6 ID/metadata leakage | scenario names, ordering, or IDs encoding the answer | `BAD_ETCH_ID = 4` compiled in; risk of "demo_etch02" naming datasets |
| T7 Fault-specific constants | a magic number that exists only because of one fault | edge-fail fractions 0.35/0.12 keyed to slot |
| T8 Distributional fingerprints | affected entities drawn from visibly different families (zero overlap) | maintenance counts 4–7 vs 0–2, durations 3–14 vs 1–6 |

## 2. Design countermeasures (how generation prevents each class)

- **D1 (T1):** yield is exclusively the sum of a die grid; the kill model's inputs are geometry, defects, and process deviation — the code path from fault identity to yield does not exist (`CAUSAL_MECHANISM_MODEL.md` §5). Same for slot: edge-slot effects flow through defect intensity only.
- **D2 (T2/T3):** closed vocabularies. Every categorical value (alarm codes, action codes, bin codes, defect classes, states) is defined in the world template and can occur on any compatible entity; mechanisms may only *shift frequencies*, never mint values. Background rates (false alarms, breakdowns on every tool) guarantee nonzero support everywhere.
- **D3 (T4):** every mapping in the chain is a probability distribution with configured overlap: defect origin → geometry has jitter and mixture overlap; origin → classified type passes a confusion matrix (5–15%); kill cause → tester bin has 20% cross-assignment; alarms fire probabilistically.
- **D4 (T5):** severity calibration in σ-units of aggregate statistics (§8 of the mechanism model) keeps per-wafer distributions overlapping; routing independence at the two etch steps removes structural collinearity; the confounded scenario deliberately *adds* imperfect correlation the engine must untangle — correlation is allowed, perfection is not. This is why a dedication is a `share` and never a filter, and why it is tool-level and never chamber-level (ADR-015): a filter would make product and chamber exposure the *same* variable inside the window, which is perfect separation by construction, and a chamber-scoped dedication would aim the confounder at the exact grain the fault is attributed at.
- **D5 (T6):** opaque `dataset_id`s; scenario names/descriptions excluded from the id hash and absent from all observable artifacts; entity IDs assigned in world-template order, never fault-ordered; the faulty entity's identity varies across the scenario library (not always tool 4, not always etch).
- **D6 (T7):** all constants live in the world template or mechanism defaults parameterized by (step, product, operation type) — never keyed by a specific tool/chamber name. Grep-able rule: no tool/chamber literal may appear in `models/` or `mechanisms/` code. As implemented (Step 3A) the rule is stronger than grep-able: a mechanism's `contribute` receives a context carrying the grid, the onset, the profile, a calibrated magnitude, its world constants and one RNG stream — and **no chamber, tool, world or timeline**. Entity-specific behaviour is not merely forbidden, it is unwritable, because the entity is not in scope. The literal scan remains, over the whole package including subpackages.
- **D7 (T8):** hazards and duration distributions are shared across all tools; faults change *rates through latents*, not distribution families. The null world exercises every distribution family the fault world uses.

## 3. The automated leakage test suite

Runs post-generation (fast subset inside `selftest.py`, full suite in CI over the scenario library). Tests read **both** planes deliberately — the suite is the boundary's auditor. Thresholds are world-template constants; failing any test fails the build.

| ID | Test | Method | Pass criterion (defaults) |
|---|---|---|---|
| L1 | Schema token lint | scan observable schema (table/column names, categorical vocabularies) for forbidden tokens: `fault, truth, scenario, bad, marginal, suspect, inject, ground` | zero hits |
| L2 | Plane separation on disk | observable artifacts contain no truth: no extra tables in fab.db beyond schema v2; manifest contains no scenario name/mechanism strings | zero hits |
| L3 | **Mediation test** (the T1 killer) | fit `yield ~ observables` (defect count, edge-zone share, CD deviation, product) on *unaffected* wafers; predict affected cohort; the unexplained cohort residual is the "direct effect" estimate | mean residual ≤ 2 pts (vs the 8-pt audited direct effect); and ≤ 40% of the raw cohort gap |
| L4 | Perfect-separation scan | for every categorical column × value: support among affected vs unaffected rows | no value with support ≥ 5 occurring *only* in the affected cohort |
| L5 | Classifier honesty | compare `defects.classified_type` against truth origins | disagreement rate within configured band (5–15%) |
| L6 | Signature overlap | distribution distance (per-wafer edge-zone share) affected vs unaffected cohorts | overlap coefficient ≥ 0.2 — separated, not partitioned |
| L7 | Null blindness | run the reference detection queries (chamber yield split, defect-rate split, CD shift) on the null dataset | all effect sizes below the subtle-severity floor |
| L8 | Seed sensitivity | build 3 seeds of one scenario; compare affected-wafer sets and realized onsets | sets differ; scenario semantics (mechanism/target/onset intent) identical |
| L9 | Code-plane lint | grep `src/fabops/`, `app/`, notebooks for `fabsim` imports, `scenarios/`, `truth` | zero hits |
| L10 | Constant-fingerprint scan | for every numeric observable column: within-group variance for the affected entity vs others | no column constant within the affected group while variable elsewhere |
| L11 | Reference-recovery asymmetry | the intended mediated evidence *is* recoverable at moderate/obvious severity by reference queries, and near the floor at subtle | per-scenario expectations table in `eval/fixtures/` |

L11 is the flip side of leakage: a fault that *cannot* be found through its mechanisms means the mechanism (never a label) needs tuning — ADR-004's operational form.

## 3.1 What Step 3A added, and the one debt it leaves

The latent plane is where D1's "the code path from fault identity to yield does not exist" becomes checkable rather than intended, and where two requirements land in full:

- **F10 — no component exists only when a mechanism does.** Every chamber carries every latent, always, with the same dynamics; a null realization exercises the whole distribution vocabulary a faulted one does. Asserted over the complete chamber population, not a sample.
- **F11 — benign offsets are baseline, not distractor bookkeeping.** Every tool and every chamber carries a permanent offset on every latent, drawn from the shared family, never reset by maintenance, with magnitudes reaching into the subtle-severity band. A declared `benign_offset` distractor widens an offset that was already there. If offsets existed only where one was declared, "this chamber has an offset" would be a categorical value with support only on named entities — leakage class T3 — and a null dataset would be visibly cleaner than a faulted one.
- **Structural, not lexical.** `test_the_latent_plane_names_no_observable` and `test_the_latent_plane_imports_nothing_observable` check that no identifier or import in `latent.py` or `mechanisms/` reaches a yield, defect, alarm or measurement concept. The only output of the slice is hidden float state.

**Debt carried into 3B (record it here so it is not forgotten):** only PMs currently move latent state. When fault-driven repair is wired in, **background breakdowns must move latent state too**. If only fault-driven repairs recovered a latent, "a repair after which behaviour changed" would be a perfect fault fingerprint (T3/T5) — visible on affected chambers and on no others. **Discharged in Step 3B — see §3.2.**

## 3.2 What Step 3B added: a response layer that is not a detector

The fab now notices conditions and reacts to them, which is the easiest place in the simulator to build a fault detector by accident. Four properties keep it from becoming one (ADR-017), each asserted:

- **The null world alarms, escalates and gets repaired.** A world with nothing wrong in it raises alarms on most of its chambers — background false alarms *and* condition alarms from natural wander — and some of those escalate into unscheduled maintenance. If they did not, the presence of an alarm or a repair would *be* the answer.
- **One recovery machine, discharging the 3A debt.** Scheduled PM, background breakdown and requested repair all move latent state. The unscheduled kinds share one distribution (Beta(8, 2), 10% no-fix) and one code path that is never told which kind it is serving, so "behaviour changed after a repair" is not a separator (T3/T5).
- **…and what that machine reaches is a statement about dynamics, not about causes (ADR-020).** A repair recovers a latent's *persistent* departure and leaves its mean-reverting wander alone. The distinction is drawn from how the components behave — one reverts by itself, the other does not — and the recovery function is handed an action and a fraction and nothing else: no mechanism, no scenario, no event, no severity, no truth, no counterfactual, no classification. It cannot ask why a departure is standing, only whether it would have gone away on its own. Two consequences are worth stating plainly. **First**, a repair on a healthy chamber now changes `edge_uniformity` by exactly nothing, and that is *not* a new fingerprint: the machinery, the distribution, the calendar and the observable record are identical, and what differs is the chamber's physical state — which is mediation (ADR-004), the same reason a faulted chamber's measurements differ at all. The alternative was worse in both directions: the old model made a background repair on a healthy chamber look like a process excursion, manufacturing false structure in the null world. **Second**, the symmetry is kept where it is physical: an accumulating load has no self-correcting part, so an unscheduled repair still visibly reduces `particle_load` on a chamber where nothing is wrong.
- **A requested repair is observationally an ordinary breakdown.** Same `UNSCHEDULED` type, same technician roster, same action-code vocabulary (D2). Neither `Alarm` nor `MaintenanceWindow` has a field for a cause — which also means a later emitter has nothing to fill from the hidden plane by accident.
- **The chart absorbs the benign offset.** Alarm limits are the chamber's own, set at qualification. Without that, rule F11's permanent offsets would have produced permanently-alarming chambers — turning the structure that exists to prevent a fingerprint into one.

Two further guards are static: the alarm/escalation/repair decision path is checked by AST to contain no mechanism, event, severity, counterfactual or departure identifier, and no tool or chamber literal; and a scenario's `response` block is proven inert (two scenarios differing only in it produce byte-identical responses).

## 3.3 What Step 3C added: mediation, measured

The observation plane is where the audited `−0.08 if bad_tool` term lived, so D1 is checked here *exactly* rather than argued:

- **The mediation test is a subtraction, not an inspection.** The same timeline is measured twice — once against the realized latent trajectories, once against their mechanism-free twins on identical draws — and the difference is asserted to equal `latent departure × declared sensitivity × channel scale` for every affected measurement, to be **exactly 0.0** on every other chamber, and to be **exactly 0.0** on every run that finished before the onset. There is no residual for a direct effect to hide in, because the residual is zero to floating-point exactness.
- **The counterfactual is a test instrument and never an output.** The engine cannot read it (checked by AST), no observable record has a field for it, and measuring the shadow realization produces different numbers — so nothing is carrying both.
- **Overlap is asserted, not hoped for.** Healthy chambers vary more within themselves than they differ from each other; an affected chamber's per-run distribution straddles the median of its healthy peers and vice versa. The 4σ single-GROUP-BY giveaway (T5) has no successor.
- **Products keep their specifications and predict nothing.** Each product's metrology scatters about its own recipe target, and every product both saw and missed the affected chamber — the precondition scenario G needs, and a guard against product identity becoming a fault detector.
- **The null is not flat.** Chamber means differ, weeks differ, lots differ, runs differ — all without a mechanism anywhere.

Static guards: the engine names no mechanism, event, severity, counterfactual, scenario or entity, reaches no 3D/3E concept, and opens no file.

## 3.4 What Step 3D added: the circularity has no successor

The defect plane is where the audit found the tightest loop — a defect's type chose its coordinates and the coordinates then confirmed the type. The arrows now run once, and in one direction:

- **D3 (T4), in full.** Geometry comes from the *hidden origin*; the observable class is a **draw** through the world's confusion row. Measured on the baseline world: `particle_cluster → PARTICLE` at 0.880 against a declared 0.88, every class arising from more than one origin, and origin and class disagreeing on over 40% of defects. Spatial confirmation of a classified type can therefore genuinely fail.
- **The hidden plane has its own record.** `Defect` has no origin field and no `killer_flag`; `DefectOrigin` is a separate collection keyed by defect id. An emitter handed the observable side has nothing to leak.
- **The null carries the whole vocabulary.** Every origin and every class occurs in a world with nothing wrong in it, at 22 defects per wafer with no spotless wafers — so neither "this wafer has defects" nor "this wafer has PARTICLE defects" is an answer.
- **No perfect separation (T5).** Exposed and healthy wafers' edge-share distributions straddle each other's medians, and every product both saw and missed the affected chamber.
- **Coordinates, not labels (T2).** Nothing writes a zone; the signature is geometry an analyst must measure.

Static guards: the engine names no mechanism, event, scenario, severity, counterfactual or entity; reaches no die, bin or yield identifier *or code string*; and opens no file.

## 3.5 What Step 3E added: the audited term has nowhere to be written

`wafer_yield` is where the audit found `−0.08 if bad_tool`. The successor is not a better-guarded formula; it is a different shape of function (ADR-021):

- **The kill model is not shown the hidden plane.** `probe(timeline, observations, population)` receives three *observable* collections. There is no `Realization` parameter, so latent state, mechanism records, distractor records, the counterfactual series and the hidden defect origin are unreachable — not unread. Every earlier plane could reach the hidden plane and was held back by a scan; this one is held back by its signature, and a test pins the parameter list.
- **Yield is a count, not a quantity.** `total_die` is the number of `die_bins` rows, `good_die` the number of `PASS` ones, `yield_pct` their quotient. There is no term to add a penalty to, and no scenario-specific expected value anywhere. `target_yield_pct` is a product *specification* the engine never reads — a test scans for it by name — and the background killer density was calibrated against the **null world** rather than against a benchmark, a diagnosis result or a legacy number.
- **A new static rule, stronger than the name scans.** Every string constant that a comparison in `die.py` tests against must be declared vocabulary: a coverage state, the partial-die policy, an operation type. A branch on a tool, a chamber, a mechanism or a scenario name cannot be written without failing a test — which is the mutation that was tried, along with a chamber-keyed penalty carrying no literal at all. The literal one was caught lexically; the chamber-keyed one was caught by the null-world plausibility and healthy/affected overlap tests, which is the pair of guards that has to work when a lexical scan cannot.
- **The spatial signature is geometry both ways.** Edge die die more often than centre die *in the null world* — the benign radial term reaches them and edge-ring defects land on them. A gradient that appeared only under a fault would be the fingerprint; one that is always there is a background an analyst has to see past.
- **The hidden cause is a separate record.** `DieBin` carries a position and a bin code: no cause, no killer flag. A bin is drawn through a symptom row, every cause reaches more than one bin and every bin arises from more than one cause, so the observable plane cannot be read back into the kill model — the same property the defect classifier has (D3/T4).

## 3.6 As implemented (the benchmark gate)

L1-L11 are built in `fabeval.leakage` and run over every library dataset; the expectations L11 scores against live in `fabeval.fixtures`, separate from the checks, because a check that decided for itself what "recoverable" means could always be satisfied. Measured on the library at seed 42 and on scenario B at 101 and 2024: **no failures**. Checks that cannot apply are reported `SKIP` rather than `PASS` — a null has no affected cohort, so L3, L4, L6 and L10 have nothing to separate, and saying they passed would be counting four checks that never ran.

Two of the eleven were wrong when first written, and both were corrected in the evaluator rather than in the simulator (ADR-024 §6):

- **L5** compared an origin's *name* against a class's and called the difference "disagreement", which read 0.96 on a perfectly honest classifier — origins and classes are different vocabularies. It now compares the realized per-origin class distribution against the world's own declared confusion matrix, and additionally requires every class to arise from more than one origin. Realized worst cell across the library: within 0.08 of the declared probability.
- **L11's expectation for scenario B** required all three declared channels to lead. ADR-018 records that `edge_uniformity` is signed and the edge-*defect* channel reads its absolute value, so across B's three seeds the planted chamber ranks 1st, 3rd and 6th on that channel while ranking 1st every time on metrology. The defect channel is now marked corroborating: measured and reported, unable to fail the scenario alone.

Representative numbers: L3's unexplained cohort residual is +0.57 / +1.21 / -0.27 points on B / G / C against a 2-point limit (the audited direct effect was 8); L6's overlap coefficient is 0.79-0.87 against a 0.20 floor; L8's worst pairwise Jaccard across three seeds of B is 0.161 against a 0.9 limit.

**Correction (the A9/A6 review gate): "no failures" was a one-seed result, and L7 does not hold.** The sentence above is true of the library as it was built — the null at a single seed — and it was scored more narrowly still: the A7 verdict was assembled from the seed-42 rows alone, so the suite's own results at other seeds were computed and then not read. Both are fixed. The null is now built at three seeds and every row is scored, and the measurement is:

| null seed | L7 | worst chamber |
|---|---|---|
| 42 | pass | below the floor |
| 101 | **fail** | edge-defect share, ETCH-02/A at **3.29σ** |
| 2024 | **fail** | edge CD, ETCH-03/A at **2.84σ** |

L7's implementation reads "below the subtle-severity floor" as a fixed 2.5σ, and that constant was never measured. Measuring it makes the finding worse rather than better: a *subtle* fault's planted chamber reaches 1.88σ on edge CD, 2.09σ on edge-defect share and 1.16σ on the yield split, so on two of three fault-free worlds a benign chamber stands out **more than a subtle fault does**. On the criterion's own wording, not merely against the 2.5 stand-in, L7 fails.

**Nothing was changed to make it pass** — not the floor, not the world, not the null's seed count back down to one. `A7` is therefore **BLOCKED** rather than PARTIAL, `test_l7_fails_on_the_null_at_two_of_three_seeds` pins the measurement so it cannot quietly regress in either direction, and the finding is recorded in ADR-025 §5 with the open question it raises: whether the benign chamber-to-chamber spread this world carries by design (F10/F11) is larger than the subtle severity rung was calibrated to sit above. That is the same overlap L6 passes on and the same one A6's sweep ran into, seen a third time; it is a calibration decision, and it needs its own gate.

## 3.7 The calibration gate's answer: L7 is measuring its own construction

That gate ran, and the open question above is answered **no** — the spread is not the problem, and the comparison that raised it is between a maximum and a point. Recorded in **ADR-026**; four measurements, none of which changed a line of the simulator.

**L7 reduces a null world to `max over chambers |leave-one-out z|` and compares it against a hardcoded 2.5.** For the seven chambers the etch-grain reference queries report at, that maximum under *perfect exchangeability* — chambers differing by nothing whatsoever — has median 2.687 and exceeds 2.5 with probability **0.598**. L7 evaluates three such channels and fails if any trips. Its expected failure rate on a **correct** null world is therefore near 0.9, and the measured rate over twelve fault-free worlds is **10 of 12**:

| null seeds built | 3 (the A9/A6 gate) | 12 (this gate) |
|---|---|---|
| L7 failures | 2 | 10 |
| worst chamber, edge CD | 2.84 | 5.38 |
| worst chamber, edge-defect share | 3.29 | 5.24 |

The 2-of-3 the table in §3.6 reports is the modal outcome of a healthy fab, not evidence about one.

**The world carries no excess benign structure.** Pooled per-chamber |z| over the twelve nulls against the exchangeable-Gaussian reference: edge CD 1.129, edge-defect share 1.124, yield split 1.084, alarms 0.891 — against 1.123 (and 0.895 at seventeen chambers). This world is, if anything, marginally quieter than exchangeable. F10/F11's offsets are doing exactly what §3.1 says they do and no more.

**No world constant can move it, and that is arithmetic rather than an experiment.** `zscore` divides by the realized between-chamber spread, so it is invariant under any common positive rescaling *and* any shift of the per-chamber scores; the chambers are exchangeable by F10/F11; so the null distribution of `max|z|` is a function of the chamber count and of nothing the world declares. Measured across six calibrations spanning 16× in benign latent offset and 20× in observation-plane chamber offset, fourteen of eighteen fault-free worlds fail L7, and a *fifth* of the declared benign chamber variation fails exactly as often as the baseline.

**What this does not license.** The threshold was not lowered, the seed count was not reduced, and no simulator constant was touched. L7's *intent* — the reference queries must not find a fault where there is none, leakage class T5 — is sound and unmet by its current form; giving it a form that converges means deciding what it compares, which ADR-026 records as three options for an architecture gate rather than settling here. `tests/fabeval/test_floor_semantics.py` pins the reference distribution, the divergence, the scale-invariance and the null's exchangeability in arithmetic that needs no dataset, so the diagnosis survives between gates.

## 4. Process rules (human-side leakage)

1. New mechanisms/scenarios must ship with their L-suite expectations before merging.
2. Tuning iterations that touch world-template constants re-run the full suite on all library scenarios (guards against fitting constants to one demo — risk R6).
3. The words used in observable free-text (maintenance descriptions, alarm messages) come from fab-wide template lists; scenario configs cannot add free text to observable surfaces.
4. Documentation of library scenarios (which answer belongs to which id) lives in `scenarios/README.md` and truth files only — never in READMEs of dataset directories.
