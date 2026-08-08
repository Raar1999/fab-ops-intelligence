# Phase 1 Acceptance — FabSim + Schema v2

**Status:** design-gate deliverable. These are the exact tests Phase 1 implementation must pass before the phase closes and before any Phase 2 work begins. Each criterion is automated unless marked (manual review).

---

## A. Acceptance criteria

### A1 — Reproducibility
Same config + same seed + same fabsim version ⇒ **byte-identical** `fab.db` and `fab_database.sql` (SHA-256 compare across two clean runs, including on CI's OS). `manifest.json` identical except `created_at` (excluded from hashes).

### A2 — Diversity
Three seeds of `chamber_edge_uniformity`: affected-wafer sets differ pairwise (Jaccard < 0.9); realized cohort yield deltas differ; all structural invariants (A4) hold in every realization; scenario semantics (mechanism, target, onset intent) identical in truth.

### A3 — No-fault validity
The null dataset passes the full integrity suite; leakage tests L7 (null blindness) and L10 pass; no latent departs its baseline band; benign distractors present at configured magnitudes (verified against truth's distractor list).

### A4 — Structural integrity (generator self-tests, every build)
All invariants of `SCHEMA_V2_DESIGN.md` §4 and `TEMPORAL_MODEL.md` §6: FK closure, run/step time ordering, zero runs during DOWN/PM, inspection/metrology/test time ordering, reconciliation (defect counts, die-bin sums, state-ribbon tiling), vocabulary closure.

### A5 — Temporal validity
For each fault scenario: truth `onset` lies strictly inside the horizon with ≥ 30% baseline period before it; affected-cohort series (metrology → defects → yield) depart baseline in causal order; scenario I additionally shows repair time < recovery, with residual ≈ configured (1 − recovery_fraction).

### A6 — Causal plausibility (reference recovery — leakage test L11)
Reference SQL (fixtures in `eval/`, not part of fabops) recovers each scenario's intended evidence at moderate severity: B/G chamber-grain yield split + edge-zone defect elevation + edge-CD shift, all temporally aligned with the window; C CD trend detectable before material yield movement; I before/after-maintenance defect-rate contrast. At subtle severity the same queries sit near the natural-variation floor (difficulty axis exists).

### A7 — Leakage resistance
Full anti-leakage suite L1–L11 green on all five library datasets. Highlighted: L3 mediation residual ≤ 2 pts (the audited 8-pt direct effect is dead), L4 no perfectly separating categorical, L5 classifier confusion in band, L8 seed sensitivity.

### A8 — Entity realism
On every dataset: ≥ 2 chambers per multi-chamber tool actually used; per-chamber run counts nonzero for qualified chambers; gate-etch vs metal-etch tool assignments independent (contingency association ≈ 0, breaking the audited collinearity); recipes resolve per product×step; measurable (benign) tool/chamber offsets exist in null data; product mix spread over lots and time (no one-lot-per-week artifact).

### A9 — Demo continuity (ADR-010) *(partly manual review)*
`demo_edge_uniformity` (scenario B, default seed) reproduces a **statistically equivalent** ETCH-02 story, defined as this checklist — not exact numbers:
- the affected chamber's tool is worst of the three etch tools on cohort yield; deficit in 4–10 pts;
- elevated edge-ring share and edge-zone defect concentration on the affected chamber's wafers;
- unscheduled maintenance present on the affected tool within the fault window;
- wafer maps visibly show the edge-ring signature (manual review of regenerated figures);
- the story is recoverable **only** through mediated channels (A7 holds on this dataset).

### A10 — Benchmark separation
L9 code-plane lint green; fabops/app/notebooks contain no fabsim import and no truth/scenario path references; truth files valid against `fabsim.truth/v1`; dataset directories contain observable artifacts + `truth/` only, with manifests free of scenario names.

### A11 — Backward compatibility
The legacy surfaces are untouched: `data/generate_fab_db.py` byte-identical, legacy `data/fab.db`/`fab_database.sql` unchanged, all 27 existing tests green, dashboard and notebook run exactly as at Phase 0 close. New code lives only in `src/fabsim/`, `scenarios/`, `eval/` (fixtures), and new tests.

## B. Phase 1 deliverables checklist

- [ ] `src/fabsim/` package per `FABSIM_DESIGN.md` §3, stdlib-only, with `fabsim-build` entry point
- [ ] `scenarios/worlds/baseline_fab_v1.*` + five scenario configs (A, B, C, G, I per `SCENARIO_SPECIFICATION.md` §4)
- [ ] Schema v2 DDL + emit path (SQLite + portable dump + manifest)
- [ ] Truth emitter (`fabsim.truth/v1`) + schema validator
- [ ] Generator self-test suite (A4) wired into every build
- [ ] Anti-leakage suite L1–L11 + reference-query fixtures in `eval/`
- [ ] Five library datasets generated deterministically in CI
- [ ] pytest coverage: rng substreams, routing, mechanism math, kill model, invariants
- [ ] `scenarios/README.md` maintainers' index (id ↔ slug ↔ answer summary)
- [ ] Documentation updates confined to: this design set marked "as implemented" deltas, README pointer to fabsim (no claims beyond what A1–A11 prove)

## C. Exit gate

Phase 1 closes when A1–A11 are green in CI and a human review confirms A9's manual items. Only then does Phase 2 (semantic layer v2) begin. Retirement of the legacy generator remains **out of scope** — it happens no earlier than the phase in which every consumer surface has migrated (ADR-010).
