# Phase 1 Acceptance — FabSim + Schema v2

**Status:** design-gate deliverable. These are the exact tests Phase 1 implementation must pass before the phase closes and before any Phase 2 work begins. Each criterion is automated unless marked (manual review).

---

## A. Acceptance criteria

### A1 — Reproducibility
Same config + same world + same seed + same fabsim version + same schema version ⇒ **the same dataset content**, everywhere. (The world template joined the inputs in Step 3.0: `build_fingerprint` previously omitted it, so two datasets built from one config against two different worlds were indistinguishable by their recorded identity — ADR-015 §5.) The oracle is a canonical content hash, not the raw bytes of the SQLite file: `fab.db` bytes depend on the SQLite library version, page size and free-list history, so a byte compare across operating systems tests the storage engine as much as it tests FabSim, and would fail for reasons that have nothing to do with determinism.

Four checks, in order of authority:

1. **Input fingerprint.** The five inputs canonicalize to one `build_fingerprint` (`fabsim.scenario.derive_build_fingerprint`), the world entering as `world_sha256` (`fabsim.world.world_sha256`: the template's semantic content, prose excluded, formatting and byte-order mark normalized away). Two runs claiming to be the same build must agree on it; changing any input — including any semantic field of the world template — must move it; and it must not move when anything environmental changes: path, machine, user, locale, clock, hash seed, or the order in which streams were drawn.
2. **Content hash — the portable guarantee.** Two clean runs must produce the identical `content_sha256`: a canonical row-level digest over every table of `fab.db` in a normalized form — tables in name order, rows in primary-key order, values in a fixed type-tagged text encoding (integers exact, floats shortest round-trip repr, NULL distinct from the empty string, text in NFC). This is what CI compares across operating systems and SQLite versions, and it is what the manifest records.
3. **Normalized text artifacts.** `fab_database.sql` is emitted deterministically (fixed statement order, fixed formatting, no environment-dependent preamble) and compared byte-for-byte; `truth/truth.json` is canonical JSON (sorted keys, fixed separators) and compared byte-for-byte. Text dumps are portable in a way the binary file is not.
4. **Additional check, controlled environment only.** On the CI reference image (pinned OS, Python and SQLite), `fab.db` is *also* compared by SHA-256. A mismatch here while (2) and (3) are green is a storage-layer difference, not a reproducibility failure: it fails the reference-image job and is investigated there, and it never gates the cross-platform result.

`manifest.json` is identical across runs except `created_at` — the only wall-clock value in the pipeline, excluded from every hash.

This does not weaken the requirement. Byte identity was only ever a proxy for "the same data"; the content hash tests that property directly, on every value in every row, and unlike a byte compare it names the table and row that diverged when it fails. What is dropped is the claim that a *binary storage format* is identical across environments FabSim does not control — a claim the design never needed and could not have kept.

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
On every dataset: ≥ 2 chambers per multi-chamber tool actually used; per-chamber run counts nonzero for qualified chambers; gate-etch vs metal-etch tool assignments independent (contingency association ≈ 0, breaking the audited collinearity); recipes resolve per product×step; measurable (benign) tool/chamber offsets exist in null data; product mix spread over lots and time (no one-lot-per-week artifact). On a dataset carrying a routing condition: the dedicated tool's share of the dedicated product's traffic rises inside the window and falls back outside it, while the dedicated product still reaches other qualified tools, other products still reach the dedicated tool, and every qualified chamber of the dedicated tool still carries traffic — dedication moved exposure probability, not eligibility (ADR-015).

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
