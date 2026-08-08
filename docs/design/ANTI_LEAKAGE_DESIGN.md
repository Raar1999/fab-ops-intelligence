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
- **D6 (T7):** all constants live in the world template or mechanism defaults parameterized by (step, product, operation type) — never keyed by a specific tool/chamber name. Grep-able rule: no tool/chamber literal may appear in `models/` or `mechanisms/` code.
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

## 4. Process rules (human-side leakage)

1. New mechanisms/scenarios must ship with their L-suite expectations before merging.
2. Tuning iterations that touch world-template constants re-run the full suite on all library scenarios (guards against fitting constants to one demo — risk R6).
3. The words used in observable free-text (maintenance descriptions, alarm messages) come from fab-wide template lists; scenario configs cannot add free text to observable surfaces.
4. Documentation of library scenarios (which answer belongs to which id) lives in `scenarios/README.md` and truth files only — never in READMEs of dataset directories.
