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
| **Scenario configuration** (`scenarios/*.json`) | The *intent*: which mechanisms, where, when, how severe, how responded to | fabsim only (and humans) |
| **Observable operational data** (`fab.db` + dump + manifest) | What a fab's MES/FDC/inspection/test systems would record: entities, runs, measurements, states, alarms, maintenance, defects, yield | everything (fabops, dashboard, humans, benchmark) |
| **Hidden ground truth** (`truth/truth.json`) | The *realization*: which runs/wafers/chambers were actually affected, latent trajectories, expected evidence — the answer key | fabsim (writer), `eval/` (reader). Never fabops. |

The config is hidden because it *is* the answer. The truth artifact exists separately from the config because the realized world depends on the seed (which wafers actually routed through the faulty chamber is a random outcome the benchmark must know exactly). Contract details: `GROUND_TRUTH_CONTRACT.md`.

## 2. Configuration format

**JSON**, header-versioned (`fabsim.scenario/v1`), schema-validated on load by `fabsim.scenario` — the only module that ever reads a configuration. JSON rather than YAML because FabSim is stdlib-only (`FABSIM_DESIGN.md` §8) and a hand-written YAML subset parser would be a second, weaker JSON; ADR-014 records the decision. The header field keeps its documented spelling, so `"fabsim": "scenario/v1"` *is* the `fabsim.scenario/v1` contract.

Reference example (the Phase 1 demo scenario):

```json
{
  "fabsim": "scenario/v1",
  "name": "demo-edge-uniformity",
  "description": "Statistically equivalent successor of the legacy ETCH-02 demo: one etch chamber develops edge non-uniformity mid-window, is repaired, mostly recovers.",
  "world": "baseline_fab_v1",
  "horizon_days": 84,
  "lots": 20,
  "default_seed": 42,

  "events": [
    {
      "mechanism": "chamber_edge_uniformity",
      "target": {"tool": "ETCH-02", "chamber": "B"},
      "onset_day": 35,
      "profile": {"type": "ramp", "ramp_days": 7},
      "severity": "moderate",
      "response": {"alarm": true, "repair_delay_days_mean": 4.0,
                   "recovery": "partial"}
    }
  ],

  "distractors": [
    {"mechanism": "benign_offset", "target": {"tool": "CVD-01"},
     "magnitude": "small"}
  ]
}
```

`name` is the human slug — it appears only here and in `truth.json`. `description` is prose. `world` names the world template (products, flow, tools, chambers, recipes, baseline variation stack, PM cadence). `default_seed` is overridable at build time.

### 2.1 The contract, field by field

| Field | Required | Type / vocabulary | Notes |
|---|---|---|---|
| `fabsim` | yes | exactly `"scenario/v1"` | any other value is rejected, including a future version |
| `name` | yes | non-empty string | documentation; excluded from identity |
| `description` | no (`""`) | string | documentation; excluded from identity |
| `world` | yes | `[a-z][a-z0-9_]*` | resolved against the world template registry at build time |
| `horizon_days` | yes | integer ≥ 1 | |
| `lots` | yes | integer ≥ 1 | |
| `default_seed` | yes | integer in [0, 2⁶⁴−1] | |
| `events` | no (`[]`) | array of events | order is significant |
| `distractors` | no (`[]`) | array of distractors | order is significant |

Event: `mechanism` (required, `[a-z][a-z0-9_]*`), `target` (required), `onset_day` (required, number in [0, `horizon_days`)), `severity` (required, `subtle` \| `moderate` \| `obvious`), `profile` (default `{"type": "step"}`), `response` (default `{"alarm": false, "repair_delay_days_mean": 0.0, "recovery": "none"}`).

Target: `tool` (required, entity name), `chamber` (optional). An absent `chamber` means the target is the whole tool and is *not* defaulted — tool-wide and chamber-scoped are different declarations.

Profile: `type` ∈ `step` \| `ramp` \| `intermittent`; `ramp_days` (number > 0) is required for `ramp` and rejected for the others. `intermittent` takes no parameters until the mechanism that interprets it lands.

Response: `alarm` (boolean), `repair_delay_days_mean` (number ≥ 0), `recovery` ∈ `none` \| `partial` \| `full`.

Distractor: `mechanism` (required), `target` (required), `magnitude` (required, `small` \| `moderate` \| `large`).

Notes:

- **World templates** live beside scenarios (`scenarios/worlds/baseline_fab_v1.json`) and hold everything scenario-independent: entity rosters, flow/recipes, variation-stack magnitudes, defect/yield model constants, PM cadence, breakdown hazards, routing policy. Scenarios stay short diffs against a shared world — which also prevents per-scenario constant tuning (anti-leakage rule D7).
- `"events": []` is legal and is exactly the null scenario; so is omitting the key, which canonicalizes to the same thing.
- Multiple events are legal (scenario J later); Phase 1 configs use at most one fault event plus distractors.
- The `target` uses ordinary entity names. Entity names are world vocabulary shared by all scenarios, so a name says nothing about fault status.
- Every categorical value comes from a closed vocabulary defined above; a configuration shifts frequencies, it never mints vocabulary (anti-leakage rule D2).

### 2.2 Loader strictness

The loader rejects, rather than repairs: an unsupported header, a missing required field, a wrong type (including `84.0` where an integer count is required, and `true` where a number is), a value outside a closed vocabulary, an unknown or misspelled field at any level, an event whose onset falls outside the horizon, a `ramp` without `ramp_days`, a duplicate JSON key (valid JSON, two stated intents), and the non-finite literals `NaN`/`Infinity`. Every rejection names the offending path (`events[0].profile.ramp_days`).

What the loader deliberately does **not** check: whether `world` and `mechanism` actually exist. Those registries belong to the world and mechanism slices, and resolution happens at build time.

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

**Canonicalization comes first.** A configuration is normalized before it is hashed: optional fields filled with their defaults, counts as integers and days as floats (`35` and `35.0` are the same day), strings stripped, keys sorted, compact separators, ASCII-escaped, list order preserved because event order is semantic. Two files that mean the same thing therefore produce the same canonical text and the same identity, whatever their formatting, key order, indentation, or byte-order mark.

- **`config_sha256`** = SHA-256 over the canonical configuration with `name` and `description` removed — they are documentation, not semantics. Renaming a file or editing prose does not change identity; changing any other field, including `default_seed` or the order of `events`, does.
- **`scenario_id`** = `scn-` + first 12 hex chars of `config_sha256`, e.g. `scn-3f9a1c7b2e4d`.
- **`dataset_id`** = `<scenario_id>-s<seed>`, e.g. `scn-3f9a1c7b2e4d-s042`.
- **`build_fingerprint`** = SHA-256 over the four reproducibility inputs (`config_sha256`, seed, fabsim version, schema version). `dataset_id` names a (scenario, seed) pair; the fingerprint additionally pins *which generator and schema* produced it, so two datasets that share a `dataset_id` but were built by different FabSim versions are distinguishable. It is the input side of acceptance test A1.
- **Nothing environmental** enters any of these: no wall clock, no file path, no machine or user name, no locale, no environment variable, no Python hash salt.
- **Filenames** of configs may be descriptive (`demo_edge_uniformity.json`) because fabops never reads `scenarios/`, and because the path has no influence on identity. Emitted dataset directories and manifests use only opaque IDs (anti-leakage rule D5); the slug ↔ id mapping lives in the truth artifact and a maintainers' index in `scenarios/README.md`.
- **Reproduction statement:** (config file ⇒ `config_sha256`) + seed + fabsim version + schema version fully determine the dataset; all four are in the manifest, and `build_fingerprint` is their single comparable form. `PHASE_1_ACCEPTANCE.md` A1 defines what "the same dataset" is checked against.
