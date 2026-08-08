# Scenario Specification

**Status:** Phase 1 design for review. Companion to `FABSIM_DESIGN.md`.

---

## 1. What a scenario is

A **scenario** is a declarative description of one synthetic fab world and the events that happen in it:

> scenario = **world template** (static fab structure + baseline behavior) + **event program** (zero or more mechanism activations with onset/severity/response) + **distractors** (benign structure the diagnosis must not blame) + **seed policy**.

A scenario is *not* a dataset. One scenario × one seed × one fabsim version = one **dataset** (a reproducible realization). The benchmark runs many seeds per scenario.

### The three-way separation (the load-bearing design rule)

| Artifact | Contains | Visible to |
|---|---|---|
| **Scenario configuration** (`scenarios/*.yaml`) | The *intent*: which mechanisms, where, when, how severe, how responded to | fabsim only (and humans) |
| **Observable operational data** (`fab.db` + dump + manifest) | What a fab's MES/FDC/inspection/test systems would record: entities, runs, measurements, states, alarms, maintenance, defects, yield | everything (fabops, dashboard, humans, benchmark) |
| **Hidden ground truth** (`truth/truth.json`) | The *realization*: which runs/wafers/chambers were actually affected, latent trajectories, expected evidence — the answer key | fabsim (writer), `eval/` (reader). Never fabops. |

The config is hidden because it *is* the answer. The truth artifact exists separately from the config because the realized world depends on the seed (which wafers actually routed through the faulty chamber is a random outcome the benchmark must know exactly). Contract details: `GROUND_TRUTH_CONTRACT.md`.

## 2. Configuration format

Header-versioned (`fabsim.scenario/v1`), schema-validated on load. Reference example (the Phase 1 demo scenario):

```yaml
fabsim: scenario/v1
name: demo-edge-uniformity        # human slug — appears ONLY here and in truth.json
description: >
  Statistically equivalent successor of the legacy ETCH-02 demo: one etch
  chamber develops edge non-uniformity mid-window, is repaired, mostly recovers.
world: baseline_fab_v1            # world template (products, flow, tools, chambers,
                                  # recipes, baseline variation stack, PM cadence)
horizon_days: 84
lots: 20
default_seed: 42                  # overridable at build time

events:
  - mechanism: chamber_edge_uniformity      # from fabsim.mechanisms
    target: {tool: ETCH-02, chamber: B}     # names resolved against the world
    onset_day: 35
    profile: {type: ramp, ramp_days: 7}     # step | ramp | intermittent
    severity: moderate                      # subtle | moderate | obvious (see
                                            # CAUSAL_MECHANISM_MODEL.md §8)
    response:                               # how the simulated fab reacts
      alarm: true                           # latent crossing emits alarms
      repair_delay_days_mean: 4.0           # alarm → unscheduled maintenance lag
      recovery: partial                     # none | partial | full

distractors:
  - mechanism: benign_offset
    target: {tool: CVD-01}
    magnitude: small                        # permanent, harmless, must not be blamed
```

Notes:

- **World templates** live beside scenarios (`scenarios/worlds/baseline_fab_v1.yaml`) and hold everything scenario-independent: entity rosters, flow/recipes, variation-stack magnitudes, defect/yield model constants, PM cadence, breakdown hazards, routing policy. Scenarios stay short diffs against a shared world — which also prevents per-scenario constant tuning (anti-leakage rule D7).
- `events: []` is legal and is exactly the null scenario.
- Multiple events are legal (scenario J later); Phase 1 configs use at most one fault event plus distractors.
- The `target` uses ordinary entity names. Entity names are world vocabulary shared by all scenarios, so a name says nothing about fault status.

## 3. Scenario library evaluation (A–J)

Assessment of the full library against Phase 1 value, with the mechanism each would exercise:

| ID | Scenario | Exercises | Phase 1? | Rationale |
|---|---|---|---|---|
| A | Normal / no fault | False-positive floor; natural variation | **Yes** | Mandatory (ADR-004). Every detector claim is meaningless without it; cheapest to build (empty event list). |
| B | Equipment fault (chamber) | Equipment/chamber attribution | **Yes** | The continuity scenario — reproduces the ETCH-02 demo answer-blindly (ADR-010) and validates chamber-grain realism. |
| C | Process drift | Temporal drift, onset estimation | **Yes** | The audit's #1 empty channel (parameters are noise today). Exercises ramp onset + metrology substrate; unlocks Phase 3 SPC work. |
| D | Sudden excursion | Step-change detection | Deferred | Structurally scenario C with `profile: step`; the mechanism library supports it on day one, so a dedicated benchmark scenario can wait — no new code, only a config. |
| E | Maintenance-induced event | Post-PM behavior shift | Deferred | Valuable, but temporal maintenance semantics are already exercised by I (recovery); E adds the *harmful* PM variant later — a small mechanism (`post_pm_shift`). |
| F | Defect mechanism | Multi-signal RCA | Partially in B | B *is* a defect mechanism (uniformity → edge defects → die kill). A separate particle-shower scenario (CVD) is deferred; it needs no new architecture. |
| G | Confounded scenario | Competing hypotheses | **Yes** | The scientifically critical one: without a confounder, attribution is trivially easy (audit: "no confounders exist"). Ships in Phase 1 so the diagnosis engine is never developed against unconfounded data. |
| H | Benign correlation | False attribution | Partially in G+A | Every Phase 1 scenario carries `benign_offset` distractors (permanent tool offsets, edge-slot effect), so benign-correlate pressure exists everywhere; a dedicated H scenario (correlate engineered to track yield) is deferred. |
| I | Recovery | Causal/temporal validation | **Yes** | Completes the fault lifecycle (onset → alarm → repair → partial recovery); makes "did the intervention resolve it?" answerable — a capability the audit found impossible in principle today. |
| J | Multiple simultaneous effects | Robustness | Deferred | Needs a stable single-fault baseline benchmark first; otherwise failure analysis is uninterpretable. Config format already permits it. |

**Initial set: A, B, C, G, I** — five scenarios. This is the smallest set that covers: false-positive control (A), the continuity demo + chamber attribution (B), temporal drift (C), competing-hypothesis discipline (G), and full temporal lifecycle (I). D/E/F/H/J are library growth, not architecture.

## 4. The five Phase 1 scenarios, specified

Common world: `baseline_fab_v1` — 6 products, 1 flow (14 steps), 15 tools (3 etch tools × 2–3 chambers), recipes per product×step, 20 lots / 84 days. All five include the standing benign distractors: permanent small tool/chamber parameter offsets, the edge-slot defect effect, and product-dependent baseline defectivity.

### A — `null` (no fault)

- **Observable mechanism:** none. All variation comes from the baseline stack (fab-week wander, lot AR(1), tool/chamber benign offsets, wafer noise, background defects, background breakdowns and PMs).
- **Hidden truth:** `events: []`; truth lists the benign distractors so the benchmark can score false attributions precisely.
- **Temporal behavior:** stationary (no onset anywhere).
- **Affected entities:** none.
- **Expected evidence:** reference queries show no tool/chamber/time effect beyond configured benign magnitudes.
- **Diagnostic challenge:** say "insufficient evidence" and mean it.
- **Benchmark purpose:** false-positive rate; the natural-variation yardstick that calibrates every severity level.

### B — `chamber_edge_uniformity` (equipment fault; the demo successor)

- **Observable mechanism:** one etch chamber (ETCH-02/B) develops edge non-uniformity: post-etch CD at edge sites drifts and its within-wafer spread grows (metrology); edge-zone defect intensity rises on wafers etched in that chamber (inspections); edge die die-kill grows (die_bins, wafer_yield); chamber pressure/endpoint FDC summaries shift subtly; alarms fire as the latent crosses thresholds; an unscheduled repair follows.
- **Hidden truth:** mechanism, chamber, onset day 35, ramp 7 days, severity moderate, realized affected run/wafer list with per-wafer exposure, repair time, recovery fraction.
- **Temporal behavior:** baseline (35 d) → ramp (7 d) → sustained fault → alarm(s) → repair (≈ day 55–60) → partial recovery (≈ 80% of the shift removed).
- **Affected entities:** one chamber; wafers whose gate-etch or metal-etch runs used it during the window (routing makes this a *changing subset*, not a fixed 50%).
- **Expected evidence:** chamber-grain yield split (≈ 4–8 pts, vs σ≈3 wafer noise), elevated edge-zone defect share, edge-site CD deviation, temporal alignment of all three with the window, repair→improvement.
- **Diagnostic challenge:** attribute to the *chamber*, not the tool as a whole and not the co-routed products; localize onset within a few days.
- **Benchmark purpose:** attribution precision at chamber grain; onset error; the ADR-010 continuity anchor (`demo_etch02` successor).

### C — `parameter_drift` (process drift)

- **Observable mechanism:** one etch chamber's delivered gas flow (MFC) drifts slowly from ~day 30 (no step change): post-etch CD mean walks off target on that chamber over weeks; FDC flow summary trends; defect behavior barely changes; yield declines late and gently via parametric die kills.
- **Hidden truth:** mechanism, chamber, onset, drift rate, severity; realized per-week latent trajectory.
- **Temporal behavior:** the slowest scenario — no alarm until late (drift is below alarm thresholds by design), no repair inside the window at subtle severity.
- **Affected entities:** one chamber; growing exposure over time.
- **Expected evidence:** monotone CD trend on one chamber, temporal onset estimable from metrology alone; yield evidence weak and late.
- **Diagnostic challenge:** detect from parameters *before* yield confirms; distinguish drift from lot-to-lot wander.
- **Benchmark purpose:** drift detection and onset estimation (time-to-detect metric); rewards the process-monitor channel that today has no substrate.

### G — `confounded_chamber_vs_product` (competing hypotheses)

- **Observable mechanism:** identical fault to B (different chamber, e.g., ETCH-01/A), but the routing policy adds a **dedication window** overlapping the fault: one product (e.g., Mobile-28, a low-target product) is preferentially routed to the faulty chamber during the fault window. Product identity and chamber exposure become correlated; naive GROUP BYs implicate both.
- **Hidden truth:** the chamber fault is causal; the product correlation is declared as a distractor with its realized correlation strength.
- **Temporal behavior:** as B, plus the dedication window recorded observably in `runs` (the routing shift is visible data, not hidden).
- **Affected entities:** one chamber; disproportionately one product's wafers.
- **Expected evidence:** the chamber effect survives within-product comparison (the faulty chamber is worse *within* Mobile-28 and *within* other products it still occasionally runs); the product effect does not survive within-chamber comparison.
- **Diagnostic challenge:** control for exposure imbalance; rank chamber above product; not the reverse.
- **Benchmark purpose:** attribution under confounding — the actual hard part of commonality analysis (audit: absent today). Scores hypothesis *ranking*, not just top-1 detection.

### I — `fault_repair_recovery` (temporal validation)

- **Observable mechanism:** a particle-load excursion on one CVD chamber: particle-type defect intensity rises fast (steeper than normal between-PM accumulation), alarms fire, unscheduled maintenance follows within days, defect rates return to ~baseline afterward — but with a configured 15% residual (imperfect recovery).
- **Hidden truth:** mechanism, chamber, onset, alarm/repair times, recovery fraction, residual.
- **Temporal behavior:** the full arc compressed: onset (day ~40) → escalation (days) → repair → recovery; the *sequence* is the signal.
- **Affected entities:** one CVD chamber; wafers deposited there during a ~1–2 week window.
- **Expected evidence:** before/after-maintenance defect-rate contrast on one chamber; affected-wafer cohort bounded in time; yield dip confined to the cohort.
- **Diagnostic challenge:** temporal reasoning — "did maintenance precede recovery?", "which wafers are inside the window?"; avoid blaming the tool for the *post*-repair period.
- **Benchmark purpose:** maintenance-effect analysis and affected-window scoping; validates that maintenance is finally causally coupled to production (audit violation #14).

## 5. Identity, naming, reproducibility

- **`scenario_id`** = `scn-` + first 12 hex chars of SHA-256 over the canonicalized config (comments and `name`/`description` fields excluded from the hash — they are documentation, not semantics). Renaming a file or editing prose does not change identity; changing any semantic field does.
- **`dataset_id`** = `<scenario_id>-s<seed>`, e.g., `scn-3f9a1c7b2e4d-s042`.
- **Filenames** of configs may be descriptive (`demo_edge_uniformity.yaml`) because fabops never reads `scenarios/`. Emitted dataset directories and manifests use only opaque IDs (anti-leakage rule D5); the slug ↔ id mapping lives in the truth artifact and a maintainers' index in `scenarios/README.md`.
- **Reproduction statement:** (config file ⇒ scenario_id) + seed + fabsim version + schema version fully determine the dataset; all four are in the manifest; `PHASE_1_ACCEPTANCE.md` A1 pins byte-stability.
