# Schema v2 Design — Observable Operational Data Model

**Status:** Phase 1 design, **implemented**. The DDL and the projection live in `src/fabsim/emit/observable.py`; the §4 invariants are checked on every build by `src/fabsim/selftest.py` (ADR-023).
**Derivation:** DATA_MODEL_AUDIT §3 Tier 1–2 (chambers, recipes, tool events, clock consistency, structured parameters, die-grid yield, classification/geometry separation, metrology), RCA_AUDIT §1.4 (identifiability failures), SYNTHETIC_DATA_AUDIT §3.1.

Everything in this schema is **observable** — data a real fab's MES / FDC / defect-inspection / test systems would record. Nothing in it encodes fault identity, scenario configuration, or ground truth (`ANTI_LEAKAGE_DESIGN.md` governs; truth lives outside the DB per `GROUND_TRUTH_CONTRACT.md`).

---

## 1. Entity-relationship overview

```
products ─1:N─ lots ─1:N─ wafers ─1:N─ runs ─N:1─ flow_steps ─N:1─ process_steps
    │                        │           │ N:1                └─N:1─ process_flows
    │                        │           ├────── tools ─1:N─ chambers
    └─1:N─ recipes ─N:1─ process_steps   ├────── recipes (chosen per run)
              │                          ├────── operators
              └─1:N─ recipe_settings     └─1:N─ run_measurements        (FDC)

wafers ─1:N─ metrology            (post-step measurement by metrology tool)
wafers ─1:N─ inspections ─1:N─ defects                    (defect inspection)
wafers ─1:1─ wafer_yield  and  ─1:N─ die_bins             (final test)

tools/chambers ─1:N─ tool_states   (E10-style state intervals)
tools/chambers ─1:N─ alarms        (point events, coded)
tools/chambers ─1:N─ maintenance   (PM / unscheduled, intervals)

dataset_meta   (single-row provenance)
```

22 tables (§2.1–2.22) replacing v1's 11. Every addition below carries the engineering question it exists to answer; nothing is added without one.

## 2. Table-by-table specification

Column lists are design-level (names/types indicative, final DDL at implementation).

### 2.1 `dataset_meta` — provenance (1 row)
`schema_version, fabsim_version, dataset_id` (opaque), `time_origin, horizon_days`.
**Why:** the benchmark and every consumer must know exactly which generator/schema produced the file. **Observable:** yes — it carries provenance, not scenario semantics: no scenario name, no mechanism, no fault field, and no seed column.

The seed is nonetheless *present*, inside the opaque `dataset_id` (`scn-<12 hex>-s<seed>`, `SCENARIO_SPECIFICATION.md` §5) — this table is where the two facts must be stated together rather than contradict each other. The distinction that matters is disclosure, not presence: the seed alone says nothing about a scenario (every scenario in the library can be built with seed 42), and `scenario_id` is a one-way hash of the canonicalized configuration with the scenario's name and description excluded from the digest, so neither half of the id discloses the answer (anti-leakage rule D5). The seed as a *first-class field* lives in `manifest.json`; the scenario name lives only in `truth.json`. **Question:** "what am I looking at, and is my tooling compatible with it?"

### 2.2 `products`
As v1: `product_id, product_name, technology_node_nm, wafer_size_mm, die_size_mm2, target_yield_pct`, plus `flow_id` FK.
**Why:** target-attainment analysis; the flow FK removes v1's implicit single global route. **Question:** "which products are missing target, and by how much?"

### 2.3 `process_flows` / 2.4 `flow_steps` / 2.5 `process_steps`
`process_flows(flow_id, flow_name)`; `flow_steps(flow_step_id, flow_id, step_id, step_sequence)`; `process_steps(step_id, step_name, operation_type, is_inspection)`.
**Why:** the route becomes data, not convention. `runs` reference `flow_step_id`, so "gate etch" is a position in a flow resolved by name — the Phase 0 `v_gate_etch_runs` semantic-anchor pattern, now first-class. Phase 1 ships one flow, but the shape supports many.
**Question:** "at which step of which route did this happen?" **Note:** v1's `target_value/tolerance` move off this table onto recipes (specs are product-specific, which is what makes products statistically distinguishable).

### 2.6 `recipes` / 2.7 `recipe_settings`
`recipes(recipe_id, step_id, product_id, recipe_name, version, metric_name, metric_target, metric_usl, metric_lsl)`; `recipe_settings(recipe_id, setting_name, set_value)` (setpoints: pressure, RF power, gas flow…).
**Why:** DATA_MODEL_AUDIT Tier 1 #2 — "did the recipe change?" is among the first RCA hypotheses and is unaskable today. Recipes also carry the per-product targets/spec limits that make product effects real, and setpoints give FDC deltas a reference.
**Questions:** "which recipe ran?", "did behavior differ by recipe version?", "was the measurement out of spec *for its product*?"
**Observable:** yes — recipes are ordinary MES data. Phase 1 plants no recipe-change fault, but the entity must exist for the hypothesis space to be honest.

### 2.8 `tools`
As v1 (`tool_id, tool_name, tool_type, vendor, install_date, location_bay`) minus `chamber_count` (replaced by the chambers table).
**Question:** "which tool?" — the classic commonality dimension.

### 2.9 `chambers`
`chamber_id, tool_id, chamber_name (e.g. 'A','B','C'), install_date`.
**Why:** the audit's "chambers are cosmetic" fix. Chambers become the *primary* locus of behavior: latent states, benign offsets, and faults attach to chambers; multi-chamber tools can have one healthy and one marginal chamber. The demo story's own wording ("marginal chamber") finally matches the data grain.
**Question:** "tool-wide or single-chamber?" — the first question an equipment engineer asks.

### 2.10 `operators`
Kept as v1, causally inert by design (a deliberate dead-end hypothesis dimension; real investigations must be able to *exonerate* dimensions).
**Question:** "is there an operator/shift effect?" (correct answer in Phase 1 worlds: no).

### 2.11 `lots`
As v1: `lot_id, lot_number, product_id, start_time, finish_time, priority, status, wafer_count` — with finish/status now emerging from the simulated timeline (fixes the audited oldest-lots-still-open inconsistency).
**Question:** "which lots are affected / should be held?"

### 2.12 `wafers`
As v1: `wafer_id, lot_id, slot_number, status`. Slot keeps the benign edge-slot effect (a standing distractor).
**Question:** wafer-level commonality; slot effects.

### 2.13 `runs`
The spine, at wafer × flow_step grain: `run_id, wafer_id, flow_step_id, tool_id, chamber_id (FK), recipe_id, operator_id, start_time, end_time`.
**Changes vs v1:** `chamber_id` is a real FK to `chambers`; `recipe_id` added; the v1 `measured_value`/`pass_fail` scalars are **removed** (replaced by `run_measurements` and `metrology` — the audit found one inert scalar cannot carry process signal).
**Why:** exposure — which wafer saw which tool/chamber/recipe when — is the substrate of every attribution question. Gate-etch and metal-etch assignments are independent draws (fixes RCA_AUDIT identifiability failure #9).
**Questions:** "which wafers were exposed to X in window W?", "what changed in routing?"

### 2.14 `run_measurements`
`run_meas_id, run_id, param_name, value` — per-run FDC-style summaries recorded by the *process* tool (e.g., etch: `chamber_pressure_mtorr`, `rf_power_w`, `gas_flow_sccm`, `endpoint_time_s`).
**Why:** DATA_MODEL_AUDIT Tier 1 #5 — process intelligence needs a substrate. Summaries, not traces (rejected scale).
**Questions:** "did a tool parameter shift/drift?", "when?" **Observable:** yes — this is what FDC systems log.
**As implemented (Step 3C):** `RunMeasurement(run_meas_id, run_id, param_name, value, unit, set_value)`. Which channels a run reports is decided by the *recipe* — a channel appears because the recipe gave it a setpoint — so an FDC reading is always grounded in something the fab actually set, and `set_value` makes the delta computable without a join. No time column: `run_id` already carries the clock, the wafer, the flow step, the tool, the chamber and the recipe, and a field that exists is a field a later emitter can fill from the wrong plane.

### 2.15 `metrology`
`metrology_id, wafer_id, flow_step_id (the measured step), metrology_tool_id, meas_time, param_name, value` — post-step wafer-level measurement by a *metrology* tool (post-etch CD, post-depo thickness, post-CMP removal). Multiple sites per wafer are summarized as `param_name` variants (e.g., `cd_nm_center`, `cd_nm_edge`, `cd_nm_sigma`) rather than a site table — enough spatial resolution for uniformity faults without a new grain. **Which step is "the measured step" is declared, not inferred**: the world template's `measures` relation (§5) names it, so a metrology row's attribution to an upstream chamber is a stated fact rather than a guess about step order.
**Why:** Tier 2 #8 — metrology distinct from defect inspection, with correct tool attribution (fixes the audited CD-SEM-doing-defect-scans anomaly). This is where drift and uniformity faults become *measurable process effects*.
**Questions:** "which parameters moved, on which chamber's wafers, starting when?"
**As implemented (Step 3C):** `Metrology(metrology_id, wafer_id, flow_step_id, metrology_tool_id, meas_time_min, param_name, value, unit)`, one row per declared wafer zone plus a `_sigma` row for the within-wafer spread. Two attributions come from the world rather than from adjacency: *what* is measured is the `measures` relation (§5), and *whose* state produced it is the **measured run's chamber at the measured run's time** — a CD indicts the etch chamber, while the metrology tool contributes only its own permanent bias and its own noise. The value is referenced to the recipe's `metric_target`, which never moves: a fault changes the realized measurement, not the specification.

### 2.16 `tool_states`
`state_id, tool_id, chamber_id (nullable), state ∈ {PRODUCTIVE, IDLE, DOWN, PM, QUAL}, start_time, end_time` — contiguous per chamber.
**Why:** Tier 1 #3; enables real utilization/MTBF/MTTR, and the one-clock invariant "no runs during DOWN/PM" becomes checkable (and checked).
**Questions:** "was the tool down when this wafer allegedly ran?", "utilization trends?"

### 2.17 `alarms`
`alarm_id, tool_id, chamber_id, alarm_time, alarm_code, severity, message`.
**Why:** Tier 1 #3 — the alarm-vs-excursion correlation channel. Codes are generic and fab-wide (`PRESSURE_HI`, `RF_REFLECT`, `PARTICLE_HI`, `MFC_DEV`, `EPD_FAULT`); every code can fire on any compatible tool, with a background false-alarm rate, so no code is a fault fingerprint (anti-leakage D4).
**Questions:** "did alarms precede the excursion?", "which subsystem complained?"

### 2.18 `maintenance`
`maint_id, tool_id, chamber_id (nullable), maint_type ∈ {PM, UNSCHEDULED}, start_time, end_time, technician, action_code, description`.
**Changes vs v1:** chamber attribution; events are *caused* (scheduled cadence, or triggered by alarms/breakdowns) and *effective* (they move latent state); intervals align with `tool_states` DOWN/PM windows; the never-generated REPAIR type is dropped. Description text is templated from action codes shared across tools (no leaking prose).
**Questions:** "what interventions happened near the onset?", "did behavior change after maintenance?"

### 2.19 `inspections`
As v1: `inspection_id, wafer_id, flow_step_id, inspection_tool_id, inspection_time, total_defect_count, scan_area_mm2` — inspection tools drawn only from defect-inspection metrology (not CD-SEM). **Which steps an inspection can see defects from is declared** by the world template's `covers` relation (§5), and the `layer` it reports at comes from the world's closed layer vocabulary — so "this inspection indicts that deposition" is never an inference from adjacency.
**Question:** "what is the defect rate, by wafer/step/time?"
**As implemented (Step 3D):** `Inspection(inspection_id, wafer_id, flow_step_id, inspection_tool_id, inspection_time_min, total_defect_count, scan_area_mm2)`. An inspection exists only where the wafer actually reached the scanner *and* had every step the scan covers; `total_defect_count` is the length of its defect list, so §4.3's reconciliation holds by construction. `inspection_time_min` is the scan run's end, which the route guarantees is after everything it covers.

### 2.20 `defects`
`defect_id, inspection_id, wafer_id, x_mm, y_mm, size_um, classified_type, layer`.
**Changes vs v1:** `defect_type` becomes `classified_type` — the output of a simulated *noisy classifier* (5–15% confusion) over the hidden true origin; coordinates are generated by the *mechanism*, not from the label (breaks the audited type⇒geometry circularity — spatial confirmation can now genuinely fail). `killer_flag` is **dropped**: killer status is ground truth a fab only learns from test overlay; the observable counterpart is `die_bins`.
**Questions:** "which defect classes/spatial signatures increased, where, when?" — the project's showpiece, kept and made honest.
**As implemented (Step 3D):** `Defect(defect_id, inspection_id, wafer_id, x_mm, y_mm, size_um, classified_type, layer)` — and nothing else. The hidden physical origin lives in a separate `DefectOrigin` record for the later truth emitter, and `classified_type` is a *draw* through the world's confusion row, so origin and class disagree on more than 40% of defects. `layer` comes from the inspection step's declared layer. Coordinates are real wafer coordinates from the product's own wafer size, never a zone label: the edge fraction, the radial profile and the wafer map are things an analyst derives, and things 3E will intersect with a die grid.

### 2.21 `wafer_yield` and 2.22 `die_bins`
`wafer_yield(yield_id, wafer_id, lot_id, total_die, good_die, yield_pct, test_time)`;
`die_bins(wafer_id, die_x, die_y, bin_code)` with `bin_code ∈ {PASS, OPEN_SHORT, PARAM, LEAK, OTHER}`.
**Changes vs v1:** yield is the *sum of a die grid* produced by the kill model, not a formula; the v1 formulaic fail-bin fractions are replaced by per-die bins assigned by a noisy tester model (electrical symptom, not cause). Die-grid grain (Tier 2 #6) enables spatial yield–defect overlay — the wafer-map visual asset extends to yield maps.

**As implemented (Step 3E):** `WaferYield(yield_id, wafer_id, lot_id, total_die, good_die, yield_pct, test_time_min)` and `DieBin(wafer_id, die_x, die_y, bin_code)` — and nothing else. `total_die` *is* the count of `die_bins` rows for the wafer and `good_die` the count of `PASS` bins, so §4.3's reconciliation holds by construction rather than by a check afterwards; `yield_pct` is their quotient and nothing anywhere adjusts it. `die_x`/`die_y` are the lattice column and row of `fabsim.die.DieGrid`, which is a pure function of the product's wafer and die size and the `die_grid` policy — no seed reaches it, so a position means the same physical place in every dataset. `test_time_min` is the wafer's `TEST` run, which is its last. Only wafers that reached the tester inside the horizon have a row. The bin is a *symptom* drawn through a declared confusion row over the hidden kill cause; that cause lives in a separate `DieOutcome` record for the truth emitter, and the observable row has no field for it and no `killer_flag`.
**Questions:** "how much loss, spatially localized or systemic, edge vs center, aligned with defects or not?"

## 3. What is deliberately absent

| Not in schema v2 | Why |
|---|---|
| Any fault/scenario/truth column | The point of the design; see `ANTI_LEAKAGE_DESIGN.md` L1/L2 |
| Sensor traces (per-second FDC) | Rejected scale (ADR-012); summaries suffice for every in-scope question |
| Lot genealogy / rework paths | Tier 2 #9, deferred: no Phase 1 scenario exercises it; add with a rework scenario |
| Holds/dispositions, excursion records | Tier 3; these are *platform outputs* (Phase 4+), not generator outputs |
| Shift calendar, consumables/parts | Tier 3; deferred until a scenario needs them |
| Queues/carriers/AMHS, multi-fab | Rejected (ADR-012) |

## 3.1 As implemented (the emission gate)

All 22 tables are emitted, in the column order §2 states, with foreign keys **declared and enforced** — `PRAGMA foreign_keys = ON` at write time and `PRAGMA foreign_key_check` before the file is handed over, so §4.1's "every FK resolves" is the database's job rather than a check that hopes. Three orders are kept separate and each is stated where it is used: §2's numbering for this document, dependency order for writing the database *and* the dump, and plain table-name order for the content digest.

**One stated deviation.** `lots.priority` (§2.11) is **not emitted**. In v1 it was a `random.choice` over HOT/STANDARD/LOW — causally inert, like the operator dimension — and the FabSim timeline models no lot priority, because releases are a cadence and scheduling is availability-driven (`TEMPORAL_MODEL.md` §2). Nothing realized exists for the column to hold, and drawing one inside the emitter would put entropy in a serializer. It belongs to a timeline slice if a scenario ever needs the dimension; until then the column is absent rather than invented (ADR-023 §5).

**One field templated as the contract requires.** `maintenance.description` is `"<maint_type> <action_code>"` — identical for every window sharing those two coded values, so the free-text column carries exactly what the codes carry and can leak nothing. A test asserts the one-to-one property rather than the string.

## 4. Integrity invariants (generated data must satisfy; `selftest.py` asserts)

1. **Referential:** every FK resolves; every run's chamber belongs to its tool; every run's recipe matches its flow_step's step and its wafer's product.
2. **Clock:** per wafer, run intervals are ordered by step_sequence and non-overlapping; every run lies inside a PRODUCTIVE interval of its chamber (none overlaps DOWN/PM — fixes the 34 audited violations); inspections/metrology follow their step's run; `wafer_yield.test_time` follows the last run; lot finish ≥ last activity.
3. **Reconciliation:** `inspections.total_defect_count` = count of its defects rows; `wafer_yield.good_die + fails` = `total_die` = count of die_bins rows, with `good_die` = count of PASS bins; state intervals per chamber tile the horizon without gaps or overlaps.
4. **Vocabulary closure:** every categorical value (states, alarm codes, bin codes, classified types, action codes) comes from the world template's shared vocabulary — never minted per event.

## 5. The world-template contracts behind these tables (Phase 1 Step 3.0)

The tables above are *outputs*. Step 3.0 added the world-template configuration the later slices read to produce them — declarations only: no alarm is generated, no die grid is laid out, no observation is computed. All of it is keyed by operation type, step, product, channel, latent or defect origin, and never by a tool, chamber or event (rule D6).

**`measures` / `covers` — the relations behind 2.15 and 2.19–2.20.** A metrology step declares the process step it reads out (`"measures": "GATE_ETCH"`); an inspection step declares the process steps whose defects it can see and the layer it reports them at (`"covers": [...], "layer": "METAL"`). Neither is inferred from the route. "The step before is the one being measured" is a convention, and a later slice that guessed would be guessing about which chamber a measurement indicts — the Step 3 gate's F1 blocker. Both are validated for existence, for step kind (metrology and inspection observe; only a processing step can be measured or covered), for a measured step actually declaring a metric, and for coming *earlier* on every route that runs them. The loader navigates the relations in both directions: `measured_step`, `metrology_steps_for`, `covered_steps`, `inspection_steps_for`.

**`observation` — the substrate of 2.14, 2.15 and 2.20.** Declares the latent vocabulary; the wafer zones summaries are reported in; the channels (`fdc`, grounded in a recipe setting of the same name; `metrology`, grounded in a step metric) with their natural `scale`, unit, qualifying operation types and their latent → channel `sensitivities`; the `variation_stack` (fab-week, tool offset, chamber offset, lot AR(1), run noise, metrology noise) as dimensionless multiples of a channel's scale, so one stack serves nm, mtorr and watts alike; the `severity_calibration` in σ of the weekly aggregate; and the `classifier` (observable classes, hidden origins, and the confusion matrix, whose rows must be distributions over declared classes with no origin mapping to one class with certainty).

**`alarms` — the vocabulary and thresholds behind 2.17.** A severity vocabulary, a per-chamber background false-alarm rate, a per-check detection probability, and generic rules of the form "this declared signal, this far outside its spread, on tools of these operation types, is worth this code". A rule has no field for an event, a mechanism, a tool or a chamber, so `if <this chamber is faulty> then <this code>` is not expressible; and every code in the baseline can fire on at least two chambers, so no code can be a fingerprint of an entity.

**`die_grid` — the geometry behind 2.21–2.22.** Edge exclusion, street width, die aspect ratio, and the coordinate conventions (origin, index order, partial-die policy) as closed, versioned vocabularies. Combined with the product's `wafer_size_mm` and `die_size_mm2` this determines a die's coordinates from geometry alone, which is what lets the later kill model satisfy ADR-004 structurally rather than by intention. Validated against every product: a die that does not fit its usable wafer is a rejection.

## 6. Backward compatibility and migration

- Schema v2 applies only to fabsim-emitted datasets under `data/scenarios/`. The legacy `data/fab.db` (schema v1), its generator, views, dashboard, notebook, and 27 tests remain untouched and green through Phase 1 (ADR-010).
- The Phase 2 semantic layer will target v2; compat views mapping the v1 analytical surface (e.g., `v_gate_etch_runs` semantics) onto v2 are a Phase 2 deliverable, not Phase 1.
- v1→v2 naming carry-overs (`products`, `lots`, `wafers`, `tools`, `operators`, `inspections`) keep intent recognizable; changed-grain tables get new names (`runs` vs `run_history`, `wafer_yield` vs `yield_data`) so nothing silently reads the wrong schema.
