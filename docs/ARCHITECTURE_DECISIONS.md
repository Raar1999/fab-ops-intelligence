# Architecture Decision Records

Format: lightweight ADRs. Status values: **Accepted** (binding now) · **Planned** (binding when its roadmap phase begins) · **Rejected** (binding prohibition). Decisions derive from the 2026-08-08 forensic audit (`docs/audit/`).

---

## ADR-001 — Audit before architecture before implementation
**Status: Accepted.** The repository was reverse-engineered and verified (every README number checked against the shipped database; suite executed on a clean environment; generator determinism re-proven) before any design was written. No implementation change ships before its phase in `docs/audit/EXPANSION_ROADMAP.md`. The audit documents are the baseline record; they describe the repo as of this date and are not silently rewritten.

## ADR-002 — SQLite + SQL views remain the analytical core
**Status: Accepted.** The four-layer shape (generator → SQLite → SQL semantic layer → thin Python consumers) survived verification with zero correctness defects and is the right scale for the project's honest scope. No client-server database, no warehouse, no ORM. Consequence: analytical logic stays in reviewable SQL; Python adds statistics SQL can't express (permutation tests, change-point detection, scoring).

## ADR-003 — Answer-blind analytics (the central decision)
**Status: Planned (Phase 1/5); lint-enforceable immediately after Phase 1.** Fault identities live exclusively in `scenarios/` configs (read only by `fabsim`) and in `eval/` expected-outcome fixtures. No module under `src/fabops/`, `app/`, or `tests/` (except eval fixtures) may name a suspect. The current `SUSPECT = "ETCH-02"` constants (4 files, audited) are grandfathered only until the diagnosis engine replaces them. Rationale: RCA_AUDIT shows the project's core weakness is that the conclusion is compiled in; this rule is the fix and is mechanically checkable.

## ADR-004 — Faults must be physics-mediated in synthetic data
**Status: Planned (Phase 1).** The data engine may not write a fault label's effect directly into a target variable (the audited `−0.08 if bad_tool` yield term is the canonical violation). Faults act through mechanisms: parameter shifts → defect generation → die kill → yield. If a fault is undetectable through its mechanisms, the mechanism is tuned — never the target. Null (no-fault) and confounded scenarios are mandatory members of the scenario library.

## ADR-005 — Evaluation is a first-class subsystem and gates all claims
**Status: Planned (Phase 6, scaffolded from Phase 3).** The scenario benchmark (detection rate, attribution precision/recall, false-positive rate on nulls, onset error) runs in CI; the README's benchmark table is generated from its output. A capability without a benchmark number may not be claimed in any public surface. This subsumes test strategy: seed-locked exact-count assertions (audited debt #4) are replaced by invariant tests + benchmark expectations.

## ADR-006 — No LLMs, agents, RAG, or knowledge graphs in this repository
**Status: Accepted (binding prohibition).** None of the in-scope engineering questions require them; each would compromise the explainability guarantee (every conclusion decomposes into hand-recomputable statistics) or duplicate FabKG. This includes "AI insight" text generation in the dashboard. Revisit requires a new ADR demonstrating a measured gap on the Phase 6 benchmark that a proposed method closes.

## ADR-007 — Statistical baseline before ML, ML only as benchmarked comparison
**Status: Accepted.** Detection and attribution ship first as transparent statistics (SPC rules, EWMA/CUSUM, permutation tests, additive evidence scores). Any ML proposal must beat that baseline on the Phase 6 benchmark and report the comparison; it becomes an alternative engine, never a replacement of the explainable path.

## ADR-008 — FabKG integration is optional, file-based, one-way per direction
**Status: Accepted (contract), Planned (activation, Phase 10).** Exchange happens only through schema-versioned flat files: `fabops.investigation/v1` JSON export; optional knowledge-prior data files import (validated against this repo's schema, ignored if absent). No shared code, no runtime dependency, no FabKG library in requirements, CI independent of FabKG assets. Full contract: `docs/audit/FABOPS_VS_FABKG_BOUNDARY.md`.

## ADR-009 — Packaging: src-layout Python package with console entry points
**Status: Planned (Phase 0).** `pyproject.toml`, `pip install -e .[dev]`, pytest configured so the documented test command works from a clean clone (fixes the audited P0 defect: bare `pytest` fails with `ModuleNotFoundError: No module named 'src'`). CLI verbs (`fabops build|monitor|investigate|report`) via entry points; Makefile becomes a thin convenience wrapper, portable.

## ADR-010 — The demo scenario is preserved through every migration
**Status: Accepted.** The ETCH-02 case (README, notebook, figures, PDF) is the project's narrative front door and regression anchor. Each phase must keep an equivalent demo working: first on the legacy generator, then as `scenarios/demo_etch02.yaml` producing a statistically equivalent story that the *engine* solves. The demo is deleted from no surface until its replacement is strictly better on that surface.

## ADR-011 — Single source of analytical truth; presentation surfaces are generated
**Status: Planned (Phases 5–9).** The audit found five hand-synchronized copies of the investigation (Python, SQL library, notebook, README, PDF). Target: the diagnosis engine's investigation artifact is the single source; the notebook case study, README benchmark/table sections, and dashboard views are generated or read from it. Hand-written duplicates of engine output are retired as each generator lands.

## ADR-012 — Scale ceiling is declared, not apologized for
**Status: Accepted.** Target scale is one fab, ~10–20 lots × 25 wafers per scenario, full route of ~10–20 steps, SQLite single-file storage, seconds-level batch runtimes. This covers every capability in scope. Kubernetes, streaming, services, multi-fab federation, and cloud deployments are rejected (TARGET_ARCHITECTURE §6); a future need would arrive as a new ADR with a workload that demonstrably exceeds this ceiling.
