# Data Model Audit

**Scope:** the 11 base tables created by `data/generate_fab_db.py`, the `fact_yield` star table (`sql/star_model.sql`), and the 12 views (`sql/views.sql`), as they exist in the shipped `data/fab.db`. All row counts and behaviors verified by direct query.

---

## 1. Entity inventory (what exists)

```
products (6) ──1:N── lots (12) ──1:N── wafers (300) ──1:N── run_history (3,145) ──N:1── tools (15)
                                          │                        │                       │
                                          │                        N:1                     1:N
                                          │                   process_steps (12)      maintenance (105)
                                          │                        │
                                          ├──1:N── inspections (742) ──1:N── defects (7,366)
                                          └──1:1── yield_data (223)          [also FK → wafers]
operators (12) ──1:N── run_history
fact_yield (223)  =  yield_data ⋈ wafers ⋈ lots ⋈ products ⋈ (gate-etch tool from run_history)
```

### Per-table assessment

| Table | Grain | What it represents well | What is wrong or missing |
|---|---|---|---|
| `products` | product | node, wafer size, die size, target yield — enough for target-attainment analysis | no layer stack, no route/flow reference (route is implicitly the same 12 steps for every product) |
| `tools` | tool | type, vendor, chamber_count, install date, bay — good dimension | **chambers are a count, not an entity**; no tool state model; ETCH-02's "marginality" exists nowhere in data — only in generated consequences |
| `process_steps` | step | sequence, operation type, target parameter + tolerance (spec-limit-like) | single global route; no recipe concept; no control limits distinct from spec limits; `target_param_name` is one scalar per step |
| `operators` | operator | shift, certification | present but causally inert (verified: shift yields 70.1/70.6/71.5 — no effect, and nothing planted) |
| `lots` | lot | product, dates, priority, status, wafer_count | no route/recipe version; no hold/disposition; status inconsistency verified: the two IN_PROGRESS lots are the *oldest* (started 2025-08-03/18, still open in Jan 2026 while later lots finished in 10–18 days) |
| `wafers` | wafer | slot (1–25), status | REWORK status exists but **no rework runs exist** in run_history; no wafer-level genealogy |
| `run_history` | wafer×step | the spine of the model: tool, chamber_id, operator, start/end, measured_value, pass_fail | one scalar measurement per run (no sensor traces / FDC summaries); `measured_value` is pure noise (no planted signal — verified ETCH-02 CD ≈ 45.05 vs others ≈ 44.85, same spread); `chamber_id` is random and causally inert (verified ETCH-02 chamber yields 63.6/64.9/64.4) |
| `inspections` | wafer×inspect-step | count + scan area; ties defects to a step/layer | inspection tool chosen randomly from all METROLOGY tools — verified CD-SEM-01 performs 256 of 742 *defect scans* (a CD-SEM is not a patterned-wafer defect inspector); no defect *bin/classification* review state |
| `defects` | defect | x/y coordinates (genuinely useful — enables real spatial analytics), size, type, layer, killer_flag | `killer_flag` is causally inert (yield uses raw counts only); defect_type doubles as the spatial-signature label — class and geometry are the same fact (see SYNTHETIC_DATA_AUDIT §3) |
| `yield_data` | wafer | total/good die, 3 fail bins, test date | fail bins are formulaic fractions, not linked to actual defects or parameter failures; no die-level bin map; no sort/bin taxonomy |
| `maintenance` | event | type (PM/UNSCHEDULED/REPAIR), duration, description text | REPAIR type is defined but never generated; descriptions are realistic strings but are the *only* alarm-like data; **not causally coupled to production** (verified: 34 wafer-runs overlap their own tool's downtime windows) |
| `fact_yield` | wafer | correct star-schema instinct: one row per yield-tested wafer with lot/product/gate-etch-tool conformed | dimension hard-wired to `step_id = 4`; would silently duplicate rows if a wafer ever saw two tools at step 4 (GROUP BY in the subquery guards the current data only) |

### View layer (12 views)

Verified all queryable. They fall into four groups:

- **Symptom:** `v_yield_by_product`, `v_yield_by_node`, `v_weekly_yield`, `v_loss_decomposition`
- **Suspect/confirm:** `v_etch_tool_yield`, `v_edge_ring_by_tool`, `v_tool_downtime`, `v_tool_rca` (the scorecard join)
- **Spatial:** `v_defect_zone` (radius + calibrated center/mid/edge zoning — the best view in the layer)
- **Peripheral:** `v_scrap_by_lot`, `v_shift_yield`, `v_defect_pareto`

Structural observations:

1. **`step_id = 4` is hard-coded in 9 places** across views/queries. "Gate etch" as the analysis anchor is baked in; there is no per-step generalization.
2. **No view is parameterized or time-windowed.** Every view aggregates all history; there is no "last 7 days," no baseline-vs-excursion window comparison.
3. **`v_weekly_yield` is a product-mix artifact** (verified): lots start ~14 days apart, so each ISO week bucket contains essentially one lot = one product. The 24-point week-to-week swings (82.3 → 57.9) are product-target differences, not process behavior. Any trend/monitoring use of this view is misleading without target normalization.
4. The `v_tool_rca` scorecard is genuinely the right *shape* (multi-signal join per tool) — this is the seed of the future diagnostic engine.

---

## 2. Can the model answer real operational questions?

Assessment of the §4 question families against the schema as it stands (not against the current views — against what the data could support at all).

### Process
| Question | Answerable? | Why |
|---|---|---|
| Did the process drift? | **No** | `measured_value` is i.i.d. Gaussian around target for every tool at every step (verified); there is no drift in the data and no time-series parameter structure worth monitoring |
| When did it begin? | **No** | the fault has no onset; ETCH-02 is identically bad from the first wafer (verified monthly yields fluctuate randomly around the same depressed mean) |
| Which parameters moved? | **No** | no parameter moves, ever |
| Which lots were affected? | Partial | derivable via tool-exposure joins (the exposure query exists) |
| Which tool/chamber was involved? | **Tool yes / chamber no** | chamber_id exists but is causally inert; the README's phrase "marginal etch chamber" is not supported at chamber grain |

### Equipment
| Question | Answerable? | Why |
|---|---|---|
| Which tool is degrading? | **No (only "which is bad")** | downtime totals identify a bad tool; nothing trends — no degradation trajectory exists |
| Which chamber behaves differently? | **No** | no chamber-level effects (verified) |
| Did maintenance change behavior? | **No** | maintenance events have no before/after effect on anything; production even continues during downtime (34 overlapping runs verified) |
| Are alarms correlated with excursions? | **No** | no alarm/event stream exists; `maintenance.description` is the only alarm-like text |
| Is a tool producing abnormal wafers? | Yes | defect counts and types by tool exposure — the strongest equipment-adjacent capability |

### Yield
| Question | Answerable? | Why |
|---|---|---|
| Where is yield being lost? | Partial | by product / by gate-etch tool; **not by step** — yield exists only at final test, and no step-level yield/inline-disposition concept exists |
| Which process step contributes most? | **No** | fail bins are formulaic; run-level pass_fail is noise disconnected from yield |
| Which product/lot is affected? | Yes | direct |
| Localized or systemic loss? | Partial | spatial zones on defects support this; die-level yield spatial data does not exist (no bin map) |

### Defects
| Question | Answerable? | Why |
|---|---|---|
| What defect types increased? | Partial | data supports time-sliced Pareto (inspection_date exists); no view does it, and there is no "increase" in the data to find |
| Is there a spatial signature? | **Yes — the model's best capability** | true x/y coordinates, calibrated radial zoning, per-type geometry |
| Are defects associated with a tool/process? | Yes | via run_history exposure joins (currently written only for etch/step 4) |

### Operations
| Question | Answerable? | Why |
|---|---|---|
| What happened before the excursion? | **No** | there is no excursion *event* (no onset) and no unified event timeline |
| What changed after maintenance? | **No** | nothing changes after maintenance |
| Which lots should be held/investigated? | Partial | the exposure ranking exists, but discriminates weakly (verified range: 28–64% exposure — every lot is heavily exposed because 51% of all wafers route to ETCH-02) |

**Summary: the model supports a *retrospective attribution* story well, and supports genuine spatial-defect analytics. It cannot support monitoring, drift/change detection, temporal reasoning, chamber-level analysis, maintenance-effect analysis, or step-level yield attribution — the core of an operations-intelligence platform.**

---

## 3. Missing entities and relationships (ranked by engineering value)

Priority feeds `GAP_MATRIX.md` and the roadmap. **None of these are to be implemented during the audit phase.**

### Tier 1 — required for the platform to be an ops system at all
1. **Chambers as an entity** (`chambers`: chamber_id, tool_id, install/PM dates). Chamber-level variation is where real etch/CVD problems live; the current story even claims "chamber" while the data is tool-grain.
2. **Recipes** (`recipes`, `recipe_versions`, run_history → recipe_version). Without recipes there is no "did the recipe change?" hypothesis — one of the first questions in any real RCA.
3. **Tool state / event log** (`tool_events`: state transitions PRODUCTIVE/IDLE/DOWN/QUAL per SEMI E10 flavor, alarms with codes). Enables: alarms-vs-excursion correlation, real utilization, downtime that actually blocks production.
4. **Time-consistent event semantics.** One clock: runs cannot overlap their tool's downtime; inspection times follow their runs; lot finish dates follow their last run. (Three violations verified in current data.)
5. **Parameter measurements with structure** (per-run summary statistics per named parameter — mean/range/σ, or a thin `fdc_summary` table). Without this, process intelligence has literally no substrate (current `measured_value` is one inert scalar).

### Tier 2 — required for credible yield/defect intelligence
6. **Die-level or grid-level yield map** (bin map per wafer, even coarse 10×10). Enables spatial yield-defect overlay, killer-defect validation, edge-loss quantification from data instead of a formula.
7. **Defect classification separated from geometry** (a defect has coordinates; a *classifier* assigns a class; class ≠ ground-truth signature). Enables realistic misclassification and honest spatial-signature detection.
8. **Metrology events distinct from inspection** (CD/overlay/thickness measurements per wafer×step with tool attribution) so parameter drift and metrology-tool bias are analyzable.
9. **Lot genealogy** (splits/merges/rework paths; rework runs actually present in run_history when wafer status says REWORK).

### Tier 3 — valuable, later
10. **Holds/dispositions** (lot_holds: reason, placed_by, released_at) — the operational action object.
11. **Excursion/investigation records** (first-class `excursions` table: detected_at, signal, scope, status) — the platform's own output becomes data.
12. **Shift calendar / crew** as a real dimension (current shift assignment is a function of hour with no calendar).
13. **Consumables/parts** on maintenance (what was replaced) — enables maintenance-effect analysis.

### Explicitly rejected (see TARGET_ARCHITECTURE §"What not to build")
- Multi-fab federation, MES transaction fidelity (queues, carriers, AMHS), full sensor-trace storage. Scale theater without analytical payoff at this project's size.

---

## 4. Star model verdict

`fact_yield` is the right instinct (BI-grain conformance) executed at the smallest useful scope. It currently encodes exactly one analytical question (yield by gate-etch tool). A future model needs at minimum: `fact_wafer_step` (exposure fact at wafer×step×tool×chamber×recipe grain), `fact_defect` (already nearly exists as `v_defect_zone`), and `fact_tool_day` (equipment states/downtime by day) — all buildable as SQL over the same SQLite store. Keep the pattern; widen the grain. **KEEP + EXTEND.**
