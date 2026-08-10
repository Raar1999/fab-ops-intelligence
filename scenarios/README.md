# Scenario library — maintainers' index

**This file is answer-key material.** It maps each scenario's slug to its
opaque identity and states what the dataset actually contains, which is
exactly what an emitted dataset must never disclose (`ADR-013`, anti-leakage
rule D5). It lives here beside the configs, and `src/fabops/`, `app/` and the
notebooks may not read this directory — a lint enforces that in both
directions.

Twelve members. All share the `baseline_fab_v1` world, an 84-day horizon, 20
lots and a default seed of 42 (anti-leakage rule D7: one world, so a scenario
is a short diff rather than a tuned constant set); the `dataset_id` below is
the one that seed produces.

The **role** column is the Phase 6 development / held-out split, declared in
`fabeval.population` and disjoint on scenarios as well as on seeds. It is what
makes a diagnosis number a claim rather than a measurement, so it is recorded
here too — but `fabeval.population` is the authority, and a test asserts the
two agree.

| # | Slug | Role | `scenario_id` | `dataset_id` (seed 42) |
|---|---|---|---|---|
| A | `null_baseline` | development | `scn-fe30d04834fa` | `scn-fe30d04834fa-s042` |
| B | `chamber_edge_uniformity` | development | `scn-d4c33a86deab` | `scn-d4c33a86deab-s042` |
| C | `parameter_drift` | development | `scn-f3d640b9b7e3` | `scn-f3d640b9b7e3-s042` |
| G | `confounded_chamber_vs_product` | development | `scn-f85dcb37b3d6` | `scn-f85dcb37b3d6-s042` |
| I | `fault_repair_recovery` | development | `scn-e04512c9efe4` | `scn-e04512c9efe4-s042` |
| K | `early_particle_excursion` | development | `scn-0ffc23f5007a` | `scn-0ffc23f5007a-s042` |
| D | `late_gas_flow_step` | development | `scn-2244c55c3981` | `scn-2244c55c3981-s042` |
| L | `tool_wide_drift` | development | `scn-4e3411a89f16` | `scn-4e3411a89f16-s042` |
| H | `benign_correlate` | **held out** | `scn-0aae54e83d03` | `scn-0aae54e83d03-s042` |
| M | `confounded_late_drift` | **held out** | `scn-a46c396a73e7` | `scn-a46c396a73e7-s042` |
| N | `intermittent_particle_load` | **held out** | `scn-d3e67de0e3e8` | `scn-d3e67de0e3e8-s042` |
| J | `multi_fault` | **held out** | `scn-8b1706db1b8d` | `scn-8b1706db1b8d-s042` |

Letters A–J are `SCENARIO_SPECIFICATION.md` §3's original catalogue; D, H and J
were deferred there and land in Phase 6. K, L, M and N have no letter in that
table because they answer questions Phase 6 raised rather than questions the
Phase 1 specification anticipated.

## What each one plants

### A — `null_baseline`   *(development)*

**Answer:** nothing. The correct diagnosis is *insufficient evidence*.

- events: none

### B — `chamber_edge_uniformity`   *(development)*

**Answer:** ETCH-02 chamber B — edge non-uniformity, onset day 35, 7-day ramp, moderate.

- event: `chamber_edge_uniformity` on `ETCH-02`/`B`, onset day 35, profile `ramp` over 7 d, severity `moderate`

### C — `parameter_drift`   *(development)*

**Answer:** ETCH-03 chamber A — delivered-parameter drift, onset day 30, 21-day ramp, subtle.

- event: `param_drift` on `ETCH-03`/`A`, onset day 30, profile `ramp` over 21 d, severity `subtle`

### G — `confounded_chamber_vs_product`   *(development)*

**Answer:** ETCH-01 chamber A — edge non-uniformity, onset day 35, moderate. The Mobile-28 routing shift over days 28–62 is a **distractor**, not the cause.

- event: `chamber_edge_uniformity` on `ETCH-01`/`A`, onset day 35, profile `ramp` over 7 d, severity `moderate`
- routing condition: `product_dedication` — `Mobile-28` to `ETCH-01` for `ETCH`, days 28–62, share 0.85 (observable in `runs`; a distractor, never the cause)

### I — `fault_repair_recovery`   *(development)*

**Answer:** CVD-01 chamber A — particle excursion, onset day 40, step, obvious. The repair that follows is the fab's own response, not configuration.

- event: `particle_excursion` on `CVD-01`/`A`, onset day 40, profile `step`, severity `obvious`

### K — `early_particle_excursion`   *(development)*

**Answer:** CVD-02 chamber B — particle excursion, onset day 12, step, moderate.

Onset at 14% of the horizon, which is the point: every Phase 1 fault begins at
day 30–40 of 84, and ADR-029 §2 recorded that clustering as "a benchmark
artefact [that] must not become architecture". Until this scenario existed no
measurement could distinguish one anchor rule from another. It reaches no
metrology channel — CD metrology in this world measures etch — so the evidence
is FDC, defects and alarms only.

- event: `particle_excursion` on `CVD-02`/`B`, onset day 12, profile `step`, severity `moderate`

### D — `late_gas_flow_step`   *(development)*

**Answer:** ETCH-01 chamber B — delivered-parameter step, onset day 63, obvious.

`SCENARIO_SPECIFICATION.md` §3's deferred scenario D ("sudden excursion"),
which that table already noted is scenario C under `profile: step` and needs no
new mechanism code. Placed at 75% of the horizon so the post-onset segment is
the short one.

- event: `param_drift` on `ETCH-01`/`B`, onset day 63, profile `step`, severity `obvious`

### L — `tool_wide_drift`   *(development)*

**Answer:** ETCH-03, the **whole tool** — parametric drift, onset day 40, 14-day ramp, moderate. Not a chamber.

The target names a tool and no chamber, which the scenario contract treats as a
different declaration rather than a defaulted one, so the drive reaches both
chambers. Every other fault in the library is chamber-scoped, so this is the
only member on which naming a single chamber is the *wrong* answer. It pairs
with C, which puts the same mechanism on one chamber of this same tool.

*Retargeted during Phase 6 design, and the reason is worth keeping.* It was
first written on `PVD-01`, to add a fourth equipment family. Measured across
three development seeds, a moderate `param_drift` there produced **no
recoverable reference-query evidence at all** — PVD's parametric sensitivities
(`dc_power_w` 0.25, `deposition_time_s` 0.4) are a fraction of etch's
`gas_flow_sccm` at 1.0, and PVD's only alarm rule sits at 3σ. The scenario's
purpose is *grain*, not PVD, so the target moved to a tool family where the
mechanism has a channel; that is scenario design, not tuning, and no world
constant, sensitivity or threshold was touched. PVD coverage is retained by J.
Even on etch it is a hard member — the drive splits across two chambers and a
PM recentres `param_bias` continuously, so the realized shift is 1.29–2.92σ
against a nominal 3.0.

- event: `param_drift` on `ETCH-03` (tool-wide), onset day 40, profile `ramp` over 14 d, severity `moderate`

### H — `benign_correlate`   *(held out)*

**Answer:** nothing. The correct diagnosis is *insufficient evidence* — again,
but this time with something to point at.

`SCENARIO_SPECIFICATION.md` §3's deferred scenario H. ETCH-02 chamber C carries
a declared `large` benign offset (1.4 of the latent's healthy weekly σ, inside
the `subtle` band). Rule F11 already gives every chamber an offset, so this
widens a distribution rather than introducing one, and what separates it from a
fault is only its shape in time.

- events: none
- distractor: `benign_offset` on `ETCH-02`/`C`, magnitude `large`

### M — `confounded_late_drift`   *(held out)*

**Answer:** ETCH-02 chamber A — parametric drift, onset day 45, 14-day ramp, moderate. The Logic-14 routing shift over days 40–75 is a **distractor**.

A second confounder, unlike G in three ways: the mechanism is a drift rather
than an edge fault, so no metrology channel reads it directly; the window is
late; and the dedication **opens before the fault starts and closes after it**,
so an engine that reads the routing shift as the change point finds a product
and a tool that moved before the chamber did.

- event: `param_drift` on `ETCH-02`/`A`, onset day 45, profile `ramp` over 14 d, severity `moderate`
- routing condition: `product_dedication` — `Logic-14` to `ETCH-02` for `ETCH`, days 40–75, share 0.8

### N — `intermittent_particle_load`   *(held out)*

**Answer:** CMP-01 chamber B — particle excursion, onset day 21, **intermittent**, obvious.

The only scenario using `profile: intermittent`, which the contract has carried
since v1 and no library member had ever exercised; and the only fault on a
polish tool. A duty-cycled fault raises the post-anchor mean less than it raises
the post-anchor variance, which is the case a two-sample contrast is weakest at.

- event: `particle_excursion` on `CMP-01`/`B`, onset day 21, profile `intermittent` (3-day period, 45% duty, drawn phase), severity `obvious`

### J — `multi_fault`   *(held out)*

**Answer:** **two** entities — ETCH-03 chamber B (edge non-uniformity, onset day 25, 7-day ramp, moderate) *and* PVD-01 chamber A (particle excursion, onset day 55, step, obvious).

`SCENARIO_SPECIFICATION.md` §3's deferred scenario J, which that table held back
until a single-fault baseline existed. Two planted entities cannot both be rank
one, so the honest outcomes are that both appear near the top or that one masks
the other.

- event 1: `chamber_edge_uniformity` on `ETCH-03`/`B`, onset day 25, profile `ramp` over 7 d, severity `moderate`
- event 2: `particle_excursion` on `PVD-01`/`A`, onset day 55, profile `step`, severity `obvious`

## Regenerating

```python
from fabsim.emit import build_dataset
from fabsim.scenario import load_scenario

build_dataset(load_scenario("scenarios/chamber_edge_uniformity.json"))
```

Datasets land in `data/scenarios/<dataset_id>/` — `fab.db`,
`fab_database.sql` and `manifest.json` are the observable plane, and
`truth/truth.json` is the hidden one. Renaming a scenario or editing its
prose does not change its identity; changing any other field does.

For a whole population at once, `fabeval.benchmark.build_population` takes a
root, a list of slugs and a list of seeds, and builds them in a process pool.
It writes only where the caller points it.
