# Expansion Roadmap

**Derivation of order.** The audit's dependency analysis dictates a sequence different from the naive "domain by domain" phasing in the brief:

1. Nothing analytical can be *claimed* until analytics can be *scored* → the scenario/evaluation capability must come immediately after the data engine, not at the end (the brief's "Phase 10 — Evaluation" would be too late).
2. Process/equipment intelligence cannot precede the data engine — today's data has no parameter signal, no temporal causality, no chamber effects (verified). Building SPC against noise is theater.
3. Diagnosis depends on detection (an excursion object to explain), which depends on monitors, which depend on the semantic layer, which depends on schema v2, which depends on `fabsim`.
4. The dashboard renders engine output, so it moves *late* — its current form keeps working meanwhile.
5. Hygiene (packaging/CI) blocks nothing conceptually but multiplies the cost of everything after it, so it is Phase 0.

Resulting critical path: **hygiene → fabsim+schema → semantic layer → monitors → detection → diagnosis ⇄ evaluation → impact/actions → dashboard → positioning.**

Rule for every phase: the existing demo (`ETCH-02` story, README, notebook) **must keep running** until its replacement is strictly better. No phase deletes a working surface before the new surface covers it.

---

## Phase 0 — Foundation hygiene (P0, small)
- **Objective:** a clean clone runs `pytest` green; the repo is a package under CI.
- **Modules/files:** `pyproject.toml` (src layout, extras: app/notebook/dev), move `src/` under packaging, pytest `pythonpath` config, `LICENSE`, `.github/workflows/ci.yml` (ruff + mypy + pytest + `python -m src.build_db` smoke), lockfile, declare `nbformat`, fix Makefile (`clean` no longer deletes committed `fab_database.sql`; portable targets), `fabops.config` for existing literals, stdlib logging in `db.py`/`build_db.py`.
- **Explicitly untouched:** all analytical logic, all SQL, generator, README claims.
- **Tests:** existing 26 must stay green (they pin today's behavior — that is their job during migration).
- **Acceptance:** fresh venv → `pip install -e .[dev]` → `pytest -q` green → `fabops-build` (console script) produces the DB → CI badge.
- **Value:** removes the first-run failure a reviewer hits today; every later phase inherits CI.

## Phase 1 — `fabsim`: answer-blind synthetic fab engine + schema v2 (P1, large — the pivotal phase)
- **Objective:** scenario-configured data generation with physics-mediated faults; the analysis code can no longer know the answer.
- **Modules:** `src/fabsim/` (scenario.py, models/{variation,degradation,defects,yieldmodel}.py, emit.py), `scenarios/{demo_etch02,null,random_template}.yaml`; schema v2 (chambers, recipes, tool_events, metrology, die-grid yield, clock invariants) per DATA_MODEL_AUDIT §3 Tier 1–2.
- **Dependencies:** Phase 0.
- **Key requirements (from SYNTHETIC_DATA_AUDIT §3):** no direct label→target terms; onset times; degradation + post-maintenance recovery; confounded routing; null scenario; misclassified defect labels; generator self-tests for reconciliation + clock invariants; determinism per seed.
- **Tests:** generator invariant suite; `demo_etch02.yaml` reproduces a *statistically equivalent* ETCH-02 story (same qualitative findings; exact-count tests from the legacy suite are retired here, replaced by invariant assertions).
- **Acceptance:** three datasets build deterministically (demo, null, one randomized); reference queries recover the demo fault only through mediated channels; no schema column encodes the fault.
- **Value:** converts the repository's central weakness (answer key) into its central strength (testbed). Everything after is measurable.

## Phase 2 — Semantic layer v2 (P1, medium)
- **Objective:** parameterized, mix-honest SQL layer over schema v2.
- **Modules:** `src/fabops/semantic/` — migrate the 12 views (they are correct; generalize `step_id=4`, add windowing), add `fact_wafer_step`, `fact_defect`, `fact_tool_day`, target-normalized yield views (kills the verified product-mix artifact in `v_weekly_yield`).
- **Dependencies:** Phase 1 schema.
- **Tests:** view reconciliation against base tables; normalization test (mix shift with constant per-product yield ⇒ flat normalized trend).
- **Acceptance:** dashboard and notebook run unchanged on the new layer (compat views kept during migration).
- **Value:** the analytical vocabulary every monitor and the diagnosis engine will speak.

## Phase 3 — Monitors: process + equipment + yield + defect (P1, medium each, parallelizable)
- **Objective:** the four monitor families emit comparable, timestamped signals.
- **Modules:** `src/fabops/monitors/{process,equipment,yield_,defect}.py` — SPC rules + EWMA/CUSUM on parameters; state/utilization/MTBF/MTTR and between-PM degradation trends; normalized yield trend; defect-rate movers + per-wafer spatial-signature scores (deepening the audited strength).
- **Dependencies:** Phases 1–2 (parameters/states now carry real signal).
- **Tests:** unit tests with synthetic series of known properties (planted drift found within k points; null series quiet).
- **Acceptance:** `fabops monitor` lists rule hits/trends on the demo scenario; quiet on the null scenario.
- **Value:** the platform starts *watching* the fab instead of retelling one story.

> **Superseded in part by ADR-029 (2026-08-09), for Phases 4 and 5 only. The text below is left exactly as written — it is the audit-era plan and it stays the record of what was planned.** Two things in it were measured and did not hold. (a) A *separable* detection stage is not a statistically real stage: a candidate-free fab-level detector fires on 1 of 25 held-out faults where the candidate-enumerating maximum fires on 7, and it needs roughly the candidate count in extra fault-free worlds to calibrate. (b) `investigate <excursion>` makes answer-blindness a property of the caller, since a hand-built excursion is a leakage channel through the argument list. So the **Excursion becomes a field of the `Investigation`** — window, onset, scope, as an *output* — and the public entry point is `diagnose(db_path)`, per `DIAGNOSIS_CONTRACT.md` §2. The dependency order this roadmap derives (you cannot explain a change you have not located) is unchanged in substance; locating and explaining are one deterministic computation behind one entry point. Phase 3 monitors remain a legitimate separate capability and are simply not a precondition of diagnosis.

## Phase 4 — Excursion detection (P1, medium)
- **Objective:** first-class Excursion objects: signal, onset estimate, scope (lots/wafers/steps/window), severity.
- **Modules:** `src/fabops/detection/excursion.py`; persistence into an `excursions` table.
- **Dependencies:** Phase 3 outputs.
- **Tests:** onset-estimate error bounds on scenarios with configured onsets; zero detections on null at default thresholds.
- **Acceptance:** demo scenario yields one excursion whose scope overlaps the configured fault window.
- **Value:** the "OBSERVATION → ANOMALY" half of the RCA target workflow becomes real.

## Phase 5 — Diagnosis engine (P1→P2, large — the headline capability)
- **Objective:** the generalized investigation: enumerate → evidence → correlate → score → rank, answer-blind.
- **Modules:** `src/fabops/diagnosis/{hypotheses,evidence,scoring}.py` per RCA_AUDIT §2; evidence families: exposure-split yield, defect rate + spatial signature, parameter shift, maintenance/alarm temporal alignment, recipe change.
- **Dependencies:** Phase 4 (excursion input); Phase 6 runs interleaved (see below).
- **Tests:** unit tests on scoring math; integration: demo scenario → ETCH-02 ranked #1 *by the engine*; confounded scenario → confounder rejected; null → "insufficient evidence."
- **Acceptance:** `fabops investigate <excursion>` prints ranked hypotheses with per-family evidence tables; no conclusion constant anywhere in `src/fabops` (lint rule).
- **Value:** the README's promise ("traces a yield miss to a marginal tool") becomes literally true for the first time.

## Phase 6 — Evaluation harness (P1, medium — interleaved with 5, not after it)
- **Objective:** the benchmark that scores Phases 3–5 and gates all future claims.
- **Modules:** `eval/` — scenario suite runner (≥10 scenarios: per fault class × severity + null + confounded), metrics (detection rate, attribution precision/recall@1, FP rate, onset error, time-to-detect), results table generator; CI job on the fast subset.
- **Dependencies:** Phase 1 (scenarios), Phase 4–5 (things to score). Start its scaffolding during Phase 3.
- **Acceptance:** one command emits the results table; README's future benchmark section is generated from it, never hand-written.
- **Value:** the project's differentiator — a diagnostic claim with a measured error rate is what separates engineering from storytelling.

> **Executed 2026-08-10; recorded in ADR-031. Annotated rather than rewritten, per ADR-001.** Three notes on what the phase turned out to be.
>
> **The harness was not the missing piece; the population was.** `src/fabeval/` has scored datasets since ADR-024 and has had a diagnosis adapter since ADR-029, so "build the runner" was largely already done. What blocked every claim was that the library had five members and all of them had chosen the method. Phase 6's substance is therefore the *library* (five → twelve, with a declared development/held-out split disjoint on scenarios as well as seeds) and the three decisions that library was the instrument for: `DIAGNOSIS_CONTRACT.md` §8.1's anchor rule, per-candidate statistic and level. `fabeval.benchmark` is the new module and it is the smallest of the deliverables.
>
> **"≥10 scenarios: per fault class × severity" understates what diversity has to mean.** Severity is one axis and it is not the binding one. The axis that was actually blocking a decision is **onset position**: every Phase 1 fault begins at day 30–40 of 84, which ADR-029 §2 flagged as "a benchmark artefact [that] must not become architecture", and no measurement could distinguish one anchor rule from another until the library contained a fault at day 12 and one at day 63. `fabeval.benchmark.diversity` measures eight axes and a test asserts each one varies, so the next scenario added cannot quietly be a twelfth copy of the same shape.
>
> **The CI job in the modules line stays unbuilt, and that is an owner decision rather than an omission.** GitHub Actions is unavailable to this repository (Final Acceptance D3, D6); the acceptance matrix records the CI-dependent verification as INFRASTRUCTURE-BLOCKED / NON-GATING and outstanding. The full suite runs locally and green.
>
> **The A1–A11 acceptance matrix deliberately still scores the Phase 1 five, not the twelve.** Those criteria were written about, and ratified against, the five members that existed then; pointing them at scenarios designed afterwards would let a Phase 6 configuration retroactively move a settled verdict. It is not hypothetical — A5 requires ≥30% of the horizon as baseline before onset, and the early-onset scenario sits at 14% *on purpose*. The anti-leakage suite L1–L11 is the opposite case and runs on all twelve, because those assert properties of a dataset rather than of a phase.

## Phase 7 — Impact, containment, recommendations (P1/P2, small)
- **Objective:** generalize the audited step-7/8 queries; templated actions.
- **Modules:** `src/fabops/impact/{loss,exposure}.py` (mix-aware benchmark), `src/fabops/actions/recommend.py` + local knowledge table (data file per BOUNDARY doc §3); investigation-artifact JSON writer (`fabops.investigation/v1`).
- **Tests:** counterfactual math unit tests; artifact schema validation.
- **Acceptance:** `fabops report <excursion>` emits the full artifact: conclusion, evidence, impact, actions.
- **Value:** closes the loop to "what should the engineer do next" — the question the platform exists to answer.

## Phase 8 — Dashboard as investigation workspace (P2, medium)
- **Objective:** rebuild per DASHBOARD_AUDIT §4: Fab Today / Process / Equipment / Yield / Investigation workspace / Wafer explorer; drill-through; renders engine output only.
- **Dependencies:** Phases 4–7 (there must be engine output to render).
- **Acceptance:** a user can go excursion → ranked hypotheses → evidence views → impact → actions without leaving the app; no hard-coded suspect anywhere in `app/`.
- **Value:** the demo becomes a usable instrument; also the best interview walkthrough surface.

## Phase 9 — Public positioning (P2, small)
- **Objective:** re-cut the public story per PROJECT_VISION.md §5: capability + benchmark first, demo scenario second; regenerate notebook case studies from artifacts; limitations section; keep every synthetic-data disclaimer.
- **Acceptance:** README benchmark table generated by `eval/`; the words "detects," "diagnoses," "ranks" are each backed by a measured number; nothing claims production use.

## Phase 10 — Optional / research (P3)
Candidates, strictly after the above and only with benchmark headroom to justify them: post-action validation analytics; die-grid pattern library (mixed-signature decomposition); ML detection/attribution *compared against* the statistical baseline on the benchmark; FabKG artifact export activation (BOUNDARY §4); predictive-maintenance study on degradation trajectories.

---

### Sequencing summary

```
P0 hygiene ─→ P1 fabsim+schema ─→ P2 semantic ─→ P3 monitors ─→ P4 detection ─→ P5 diagnosis ─→ P7 impact/actions ─→ P8 dashboard ─→ P9 positioning
                                                      └────────── P6 evaluation (scaffold early, gates P3–P5 claims) ──────────┘
```

The single highest-value increment if only one thing is ever built: **Phase 1 + Phase 5 + Phase 6** — randomized scenarios, an engine that finds the fault it wasn't told, and the table proving how often. That triad is the difference between this repository and every other "SQL portfolio project" in existence.
