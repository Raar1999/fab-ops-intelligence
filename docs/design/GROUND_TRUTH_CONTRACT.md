# Ground Truth Contract — fabsim.truth/v1

**Status:** Phase 1 design for review. Made binding by ADR-013.

The hidden ground truth is the benchmark's answer key: a separate, versioned artifact that captures what *actually happened* in a generated world. It is written once by fabsim at emit time and read by exactly one consumer class: evaluation code.

---

## 1. Why a separate artifact (not the config, not DB rows)

- **Not the config:** the config states *intent* ("edge uniformity fault on ETCH-02/B from day 35"). The realized world depends on the seed: which runs actually went through the faulty chamber, which wafers are in the affected cohort, when the alarm actually fired, what the realized recovery fraction was. The benchmark must score against the realization without ever re-simulating.
- **Not in the database:** any truth table inside `fab.db` would be one careless JOIN away from the diagnostic engine. Physical separation (different file, different directory, different schema namespace) makes the leakage boundary auditable with a directory listing.

## 2. Storage and layout

```
data/scenarios/<dataset_id>/
├── fab.db  fab_database.sql  manifest.json     # observable plane
└── truth/
    └── truth.json                              # hidden plane (this contract)
```

- One truth file per dataset, JSON, schema-versioned (`"schema": "fabsim.truth/v1"`).
- Large per-wafer lists are inline JSON at this scale (~500 wafers); no side files.
- Committed to git for library scenarios (they are test fixtures); the observable/hidden split is a *read-discipline* boundary, not a secrecy mechanism — the threat model is accidental analytical coupling, not a human peeking.

## 3. Content (fabsim.truth/v1)

```json
{
  "schema": "fabsim.truth/v1",
  "dataset_id": "scn-3f9a1c7b2e4d-s042",
  "scenario_id": "scn-3f9a1c7b2e4d",
  "scenario_name": "demo-edge-uniformity",
  "config_sha256": "…64 hex — SHA-256 of the canonicalized config (name/description excluded); scenario_id is its first 12 chars…",
  "seed": 42,
  "fabsim_version": "1.0.0",
  "schema_version": "2.0",

  "events": [
    {
      "event_id": "E1",
      "mechanism": "chamber_edge_uniformity",
      "target": {"tool": "ETCH-02", "chamber": "B", "chamber_id": 7},
      "onset": "2026-04-06T00:00:00",
      "profile": {"type": "ramp", "ramp_days": 7},
      "severity": "moderate",
      "severity_realized": {"aggregate_shift_sigma": 3.1},
      "end": "2026-04-28T09:40:00",
      "causal_chain": ["latent.edge_uniformity", "metrology.cd_nm_edge",
                       "defects.edge_ring", "die_bins.edge", "wafer_yield"],
      "alarms_emitted": [811, 812, 815],
      "maintenance_response": {"maint_id": 61, "repair_time": "2026-04-28T02:00:00",
                                "recovery_fraction": 0.82},
      "affected_runs": [1042, 1057, "…"],
      "affected_wafers": [{"wafer_id": 233, "exposure": 1.0,
                            "expected_mechanism_share": "high"}, "…"],
      "expected_impact": {"cohort_yield_delta_pts": -5.4,
                           "cohort_size": 87}
    }
  ],

  "distractors": [
    {"kind": "benign_offset", "target": {"tool": "CVD-01"},
     "note": "permanent small offset; attribution to this entity is a false positive"},
    {"kind": "edge_slot_effect", "magnitude": "small"},
    {"kind": "product_dedication_window", "present": false}
  ],

  "latent_summaries": {
    "chamber_id:7": {"edge_uniformity_weekly": [0.0, 0.0, "…", 0.61, 0.12]}
  }
}
```

Field intent:

- **events[]** — one entry per configured mechanism activation, with intent (mechanism/target/onset/severity) *and* realization (realized aggregate shift, actual alarm/maintenance IDs, affected run/wafer sets with exposure degree, realized recovery, expected cohort impact). `expected_impact` is the generator's own accounting of the mediated effect (computed from the kill model, not injected), so the benchmark can score impact estimates too.
- **distractors[]** — every benign structure the scenario contains that a diagnostic engine might wrongly accuse. Listing them makes false-attribution scoring exact instead of judgmental.
- **latent_summaries** — weekly-resolution latent trajectories for affected entities: enough for onset-error scoring and post-hoc analysis without storing full state history.
- The null scenario emits `events: []` with distractors populated — an empty answer key is still an answer key.

## 4. Access rules (the separation that must hold)

| Actor | fab.db / dump / manifest | scenarios/*.json | truth/truth.json |
|---|---|---|---|
| `fabsim` | writes | reads | writes |
| `fabops` (all analytical code, dashboard, notebooks) | **reads** | **never** | **never** |
| `eval/` (Phase 6 benchmark; Phase 1 self-test fixtures) | reads | may read | **reads** |
| CI leakage suite | reads | reads | reads (it verifies the boundary) |

Enforcement (designed now, wired in Phase 1 implementation):

1. **Import lint:** no module under `src/fabops/` or `app/` may import `fabsim` or reference the strings `scenarios/`, `truth/`, `truth.json` (test L9 in `ANTI_LEAKAGE_DESIGN.md`).
2. **API shape:** the only supported entry point for analytical code is "open `<dataset_dir>/fab.db`"; fabops helpers take a DB path, never a dataset directory — so reaching truth requires deliberate circumvention, not accident.
3. **CI check:** the benchmark runner is the sole code path constructing truth paths; a grep-based CI job fails on new references.

## 5. Versioning and evolution

- `fabsim.truth/v1` is frozen by Phase 1 acceptance. Additive fields → v1.x (readers ignore unknowns); semantic changes → v2 with a migration note in this document.
- The truth schema is versioned independently of the observable schema: evaluation-side needs (new metrics) must not force observable-schema bumps, and vice versa.
- Every truth file self-identifies (`schema`, `fabsim_version`), so mixed-version scenario libraries remain scoreable.

## 6. Benchmark separation summary

The Phase 6 evaluator will join *diagnostic output* (from fabops, computed off fab.db alone) with *truth* (this artifact) on `dataset_id`. Nothing else ever joins the two planes. That single join point is the whole integration surface between the answer and the system being tested — and it lives in `eval/`, outside the diagnostic engine, by construction.
