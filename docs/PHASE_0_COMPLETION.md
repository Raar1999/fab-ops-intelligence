# Phase 0 Completion Report

**Date:** 2026-08-08 · **Scope:** hygiene and foundation only, per the Phase 0 gate. No Phase 1 work (no scenario engine, no answer-blind generator, no diagnosis engine, no benchmark, no temporal fault injection, no dashboard redesign) was performed. Scope rulings and contradiction resolutions were fixed **before** implementation in `docs/PHASE_0_SCOPE_DECISIONS.md`.

---

## 1. What changed

1. **Packaging (the audited P0 defect).** The uninstallable `src` folder became a proper src-layout package `src/fabops/` with `pyproject.toml`, console scripts (`fabops-build`, `fabops-investigate`), and a single dependency source (requirements.txt now delegates to pyproject and finally declares the notebook stack including the previously undeclared `nbformat`). The `sys.path` hacks in the dashboard and the generated notebook cell are gone. A CI workflow encodes the verified sequence (install → test → full regeneration → re-test).
2. **Configuration centralization.** New `fabops/config.py` is the single definition site for repository paths (previously computed independently in four modules), the chart theme and wafer radius (previously duplicated between `charts.py` and the dashboard), the radial zone cut-offs (now shared with the tests), and the demonstration suspect.
3. **Semantic gate-etch anchor.** `step_id = 4` — hard-coded in 9 places per the audit — no longer appears in any analytical code. Gate etch is defined exactly once: the new `v_gate_etch_runs` view resolves the step **by name** (`ETCH-GATE`) against `process_steps`; every view, query, chart, dashboard panel, and test joins that view (`star_model.sql` resolves the same name inline because it is applied before the view layer exists).
4. **RCA honesty.** The suspect constant is now `fabops.config.DEMO_SUSPECT_TOOL`, documented in code as the *known, planted conclusion that the demonstration narrates and verifies*. The investigation banner and the README now state explicitly that this is a **demonstration RCA, not a discovery engine**, with pointers to `docs/audit/RCA_AUDIT.md`.
5. **Makefile correctness.** `make clean` no longer deletes the intentionally committed `data/fab.db` / `data/fab_database.sql` (that destructive behavior moved to an explicit `clean-db`); an `install` target was added; module paths updated.
6. **Committed artifacts regenerated** once, in a dedicated commit: `fab.db` (same tables — generator untouched — plus the 13-view layer), the executed notebook (rebuilt from updated templates), and the figures.

## 2. Why it changed

Every change maps to a verified audit finding: the broken documented test command (SE audit, "the one outright defect"), duplicated constants (debt #9, config score 1/5), fragile paths (debt #1), the scattered step id (debt #5), the destructive clean target (debt #7), the undeclared dependency (debt #8), and the gate's Step 6 requirement that documentation honestly separate the demonstration RCA from the future discovery engine. Nothing was changed that did not trace to an audit finding or a gate requirement.

## 3. Files changed (baseline `1acc1a8` → completion)

- **Added:** `pyproject.toml`, `.github/workflows/ci.yml`, `src/fabops/config.py`, `docs/PHASE_0_SCOPE_DECISIONS.md`, this report.
- **Moved (git-tracked renames):** `src/*.py` → `src/fabops/*.py`.
- **Modified:** `sql/views.sql` (+`v_gate_etch_runs`, 4 views re-anchored), `sql/star_model.sql`, `sql/rca_queries.sql`, `src/fabops/{db,build_db,charts,investigation}.py`, `app/ops_dashboard.py`, `tests/{conftest,test_queries}.py`, `build_notebook.py`, `Makefile`, `requirements.txt`, `README.md`, `.gitignore`.
- **Regenerated:** `data/fab.db`, `notebooks/investigation.ipynb`, `reports/figures/*.png`.
- **Untouched by design:** `data/generate_fab_db.py` (generator changes are Phase 1), `data/fab_database.sql` (byte-identical), all `docs/audit/*` (audit is the frozen baseline record), everything outside this repository except two quickstart lines in the bundle's `README_BUNDLE.txt`.

## 4. Tests run

| Check | Environment | Result |
|---|---|---|
| `pytest -q` (bare — the previously broken documented command) | **Fresh venv #2**, `pip install -e ".[dev]"` only | **27 passed** |
| `fabops-build` console script | fresh venv #2 | Works; "13 views created; fact_yield has 223 rows" |
| `fabops-investigate` console script | fresh venv #2 | Full 8-step run, exit 0 |
| Full-regeneration determinism | venv #1 (delete `fab.db` → rebuild) | Same row counts and planted-signal printouts as shipped baseline |
| Dashboard, streamlit bare-mode execution (all queries + charts execute) | venv #1 + streamlit 1.61 | Exit 0 |
| Notebook rebuild + headless execution (nbclient) | venv #1 | All cells execute; key numbers present |
| Library stack | pandas 3.0.5 / numpy 2.4.6 / matplotlib 3.11.1 / pytest 9.1.1 / Python 3.11.9 | All green |

Test count is 27 (was 26): the parametrized queryable-view check now covers `v_gate_etch_runs`. All 26 original assertions are unchanged and passing.

## 5. Baseline comparison

A pre-change baseline was captured from the untouched code (commit `1acc1a8`): DB rebuilt from scratch, all 12 views + `fact_yield` dumped sorted to CSV, full investigation stdout, figure hashes. Post-change, the same capture was repeated and diffed:

| Surface | Result |
|---|---|
| 12 original views + `fact_yield` (sorted CSV dumps) | **Byte-identical, all 13 files** |
| Investigation stdout (123 lines) | **Identical except exactly one added line** — the intended demonstration-RCA banner (verified by encoding-normalized diff: 1 changed line) |
| All 6 figures | **Byte-identical** renders between baseline code and final code under the same matplotlib (pixel differences vs the originally shipped PNGs are toolchain-version-only) |
| Key numbers (yield split 64.29/75.91/79.39, 117 wafers, edge-ring 43.7/13.9/8.8%, downtime 30.53 h/4 events, r = −0.55, 46,575 est. die, exposure table) | **Unchanged everywhere** — stdout, notebook outputs, views |
| `data/fab_database.sql` | Untouched (generator not run in-repo; dump unchanged) |

Justified deltas, each pre-declared in `PHASE_0_SCOPE_DECISIONS.md`: the one banner line; the 13th view; 26→27 tests; documented commands (`python -m src.*` → `python -m fabops.*` + install step); README counts and honesty note.

## 6. Remaining technical debt (deliberately deferred)

| Debt | Deferred to | Reason |
|---|---|---|
| `print()` → `logging` | Phase 1 | Changes demo output for no defect fix; engine work will restructure output anyway |
| ruff + mypy in CI | Phase 1 gate review | New dev dependencies; gate forbade unnecessary additions |
| Dependency lockfile | Phase 1 (with live CI) | No consumer until CI runs on a remote |
| `LICENSE` file | ~~Owner decision~~ **closed 2026-08-10** | The owner chose **MIT** at the Final Integration gate. `LICENSE` is committed and `pyproject.toml` declares it. |
| Seed-locked test assertions (`fact_yield == 223`) | Phase 1 | They are the baseline pin during hygiene; retired when the data engine changes (scope decision C6) |
| Five hand-synced story surfaces (py/sql/ipynb/README/PDF) | Phases 5–9 (ADR-011) | Resolved by generating surfaces from engine output, not by more synchronization |
| `st.cache_data` never invalidates after rebuild | Phase 8 | Dashboard rework renders engine output |
| Bundle PDF now shows pre-rename commands | Owner regeneration | Cannot regenerate a PDF faithfully here; bundle README_BUNDLE.txt was corrected |
| CI workflow unexercised | First push to a GitHub remote | No remote exists yet; workflow encodes exactly the locally verified commands |

## 7. FabKG boundary confirmation

Intact. Phase 0 introduced no knowledge graph, no ontology, no graph library, no FabKG dependency, import, or reference in any code path — `pyproject.toml` dependencies are unchanged in substance (pandas/numpy/matplotlib + the same optional extras that `requirements.txt` always listed). No LLM, agent, RAG, API, microservice, cloud, or streaming component exists anywhere in the repository. The boundary contract (`docs/audit/FABOPS_VS_FABKG_BOUNDARY.md`) is untouched.

## 8. Recommended next step — Phase 1 (for review before execution)

Per `docs/audit/EXPANSION_ROADMAP.md` Phase 1: **build `fabsim`, the answer-blind scenario engine, together with schema v2** — scenario configs (YAML: fault type, location, onset, severity, seed) read only by the generator; physics-mediated faults (delete the direct `−0.08 if bad_tool` yield term); chambers/recipes/tool-events/metrology/die-grid entities; one event clock; a null scenario; generator self-tests; and `scenarios/demo_etch02.yaml` reproducing a statistically equivalent ETCH-02 demo. Acceptance: three deterministic datasets (demo, null, randomized), fault recoverable only through mediated channels, no schema column encoding the fault. That phase is the pivot that makes every later capability (detection, diagnosis, benchmark) measurable — and it is **not started** until this Phase 0 gate is reviewed.

---

## Phase 0 acceptance checklist

- [x] Clean environment can install the project (fresh venv #2: `pip install -e ".[dev]"`)
- [x] Documented test command works (`pytest -q` → 27 passed, bare, from install alone)
- [x] Existing tests pass (all 26 original assertions unchanged and green)
- [x] Existing analytical outputs reproducible (13/13 dumps byte-identical; stdout identical except the declared banner line)
- [x] Hard-coded `step_id=4` removed from analytical code (single semantic anchor `v_gate_etch_runs`; remaining literals are the untouchable generator, a historical comment, and audit docs)
- [x] Duplicated constants centralized (`fabops/config.py`)
- [x] Documentation distinguishes current demonstration RCA from future discovery (README, banner, config docstring)
- [x] No new unnecessary dependencies (dependency set unchanged; `nbformat` was already used, now declared)
- [x] No FabKG architecture introduced
- [x] No LLM/agent/RAG/knowledge graph introduced
- [x] Git history separates the hygiene work (7 focused commits: baseline → scope decisions → packaging → config → step anchor → artifacts → docs)
- [x] Repository ready for Phase 1
