# Scenario library — maintainers' index

**This file is answer-key material.** It maps each scenario's slug to its
opaque identity and states what the dataset actually contains, which is
exactly what an emitted dataset must never disclose (`ADR-013`, anti-leakage
rule D5). It lives here beside the configs, and `src/fabops/`, `app/` and the
notebooks may not read this directory — a lint enforces that in both
directions.

The five members are the Phase 1 set of `SCENARIO_SPECIFICATION.md` §4. All
share the `baseline_fab_v1` world, an 84-day horizon, 20 lots and a default
seed of 42; the `dataset_id` below is the one that seed produces. Scenarios
D/E/F/H/J are not Phase 1.

| # | Slug | `scenario_id` | `dataset_id` (seed 42) |
|---|---|---|---|
| A | `null_baseline` | `scn-fe30d04834fa` | `scn-fe30d04834fa-s042` |
| B | `chamber_edge_uniformity` | `scn-d4c33a86deab` | `scn-d4c33a86deab-s042` |
| C | `parameter_drift` | `scn-f3d640b9b7e3` | `scn-f3d640b9b7e3-s042` |
| G | `confounded_chamber_vs_product` | `scn-f85dcb37b3d6` | `scn-f85dcb37b3d6-s042` |
| I | `fault_repair_recovery` | `scn-e04512c9efe4` | `scn-e04512c9efe4-s042` |

## What each one plants


### A — `null_baseline`

**Answer:** nothing. The correct diagnosis is *insufficient evidence*.

- events: none

### B — `chamber_edge_uniformity`

**Answer:** ETCH-02 chamber B — edge non-uniformity, onset day 35, 7-day ramp, moderate.

- event: `chamber_edge_uniformity` on `ETCH-02`/`B`, onset day 35, profile `ramp` over 7 d, severity `moderate`

### C — `parameter_drift`

**Answer:** ETCH-03 chamber A — delivered-parameter drift, onset day 30, 21-day ramp, subtle.

- event: `param_drift` on `ETCH-03`/`A`, onset day 30, profile `ramp` over 21 d, severity `subtle`

### G — `confounded_chamber_vs_product`

**Answer:** ETCH-01 chamber A — edge non-uniformity, onset day 35, moderate. The Mobile-28 routing shift over days 28–62 is a **distractor**, not the cause.

- event: `chamber_edge_uniformity` on `ETCH-01`/`A`, onset day 35, profile `ramp` over 7 d, severity `moderate`
- routing condition: `product_dedication` — `Mobile-28` to `ETCH-01` for `ETCH`, days 28–62, share 0.85 (observable in `runs`; a distractor, never the cause)

### I — `fault_repair_recovery`

**Answer:** CVD-01 chamber A — particle excursion, onset day 40, step, obvious. The repair that follows is the fab's own response, not configuration.

- event: `particle_excursion` on `CVD-01`/`A`, onset day 40, profile `step`, severity `obvious`

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
