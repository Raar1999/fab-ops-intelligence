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

## 4. Process rules (human-side leakage)

1. New mechanisms/scenarios must ship with their L-suite expectations before merging.
2. Tuning iterations that touch world-template constants re-run the full suite on all library scenarios (guards against fitting constants to one demo — risk R6).
3. The words used in observable free-text (maintenance descriptions, alarm messages) come from fab-wide template lists; scenario configs cannot add free text to observable surfaces.
4. Documentation of library scenarios (which answer belongs to which id) lives in `scenarios/README.md` and truth files only — never in READMEs of dataset directories.
