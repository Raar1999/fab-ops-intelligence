# Software Engineering Audit

**Method:** full read of all source; suite executed on a clean venv with current library versions (Python 3.11.9, pandas 3.0.5, numpy 2.4.6, pytest 9.1.1); determinism re-verified from scratch. Scores are 1–5 against the bar of "a small professional analytics repository," not against enterprise software.

## Scorecard

| Dimension | Score | Justification (verified specifics) |
|---|---|---|
| Architecture | **3** | Clean four-layer shape (generator → SQLite → SQL views → thin consumers) with a single data-access choke point — genuinely good for the size. Held back by: conclusion constant compiled into all four consumer layers; analysis anchored on hard-coded `step_id=4` in 9 places; `rca_queries.sql` duplicating `investigation.py` query-for-query (two sources of truth) |
| Modularity | **3** | `src/` modules have single responsibilities and no circular imports. But `charts.py` and `ops_dashboard.py` re-implement the same figures (scorecard, wafer map) with copy-pasted styling constants (`ALERT/OK/ACCENT`, `TYPE_COLORS`, `WAFER_RADIUS_MM` duplicated in both) |
| Separation of concerns | **4** | The stated principle — "logic lives in SQL views, front-ends stay thin" — is actually followed; dashboard/notebook are `SELECT * FROM v_…` plus rendering. The exception: 3 substantive queries are inlined in the dashboard and 4 in investigation.py instead of being views |
| Type safety | **2** | `from __future__ import annotations` + signatures on `db.py` only; no annotations on chart/investigation functions; no mypy/pyright config; DataFrames untyped everywhere (no schema validation on query results) |
| Error handling | **2** | Exactly one deliberate error path (missing-DB `FileNotFoundError` with a helpful message — good). Everything else propagates raw: a malformed view, an empty DataFrame (`df.iloc[0]` in several steps), a zero-division in `v_defect_zone`-consuming code would all crash with framework tracebacks |
| Logging | **1** | `print()` only; no logging module, no levels, no timestamps. Acceptable for a demo script, disqualifying for a platform |
| Testing | **3** | 26 real tests with a smart idea (pin the analytical findings, not just execution). Weaknesses: seed-locked exact assertions (`fact_yield == 223`); `SUSPECT` hard-coded so tests verify the plant, not an engine; no tests for generator invariants, charts, build_db, or dashboard; no negative/null-case tests |
| Test coverage | **3** | View layer: fully covered (all 12 queryable + finding assertions). `src/db.py`: exercised indirectly. `charts.py` (229 lines), `build_notebook.py`, `app/` (195 lines), generator (615 lines — the most important logic in the repo): zero coverage |
| Configuration | **1** | No config mechanism at all. Paths, suspect, colors, wafer radius, zone cut-offs (50/110 mm), step ids — all literals, several duplicated across files. The zone cut-offs appear in `views.sql` and prose but nowhere as a named constant |
| Reproducibility | **4** | The strongest dimension. Verified: seed=42 generator is byte-deterministic cross-platform (SHA-256 match vs shipped dump after CRLF normalization); committed DB matches regeneration; notebook executed and consistent; figures regenerate identically. Docked one point: the documented test command is broken (below) and there is no lockfile, so "works on a fresh clone" is not actually guaranteed |
| Dependency management | **2** | `requirements.txt` with `>=` floors only; no lockfile; no `pyproject.toml`; **`build_notebook.py` imports `nbformat`, which is absent from requirements.txt** (transitively present via jupyter, but undeclared); generator itself is admirably stdlib-only |
| Data management | **3** | Committed DB + portable `.sql` dump + regeneration path, with the tradeoff documented in `.gitignore` — a defensible choice at 0.8 MB. No schema versioning/migration story; `build_db.py` silently *reuses* a stale `fab.db` if present (docstring claims idempotence; true only for the view layer) |
| CLI | **2** | `python -m src.build_db` / `python -m src.investigation` work; Makefile is a reasonable front door but POSIX-only (`rm -rf`) on a project otherwise Windows-clean. No arguments, no `--help`, no exit-code discipline, no way to point at a different DB despite `db.py` supporting it |
| Documentation | **4** | README is excellent: honest synthetic-data disclaimers, verified numbers, design notes, résumé framing. Module docstrings explain *why*. Docked: no docs/ (until this audit), the `pytest -q` instruction is wrong, Makefile `clean` deletes `fab_database.sql` which `.gitignore` says is intentionally committed (a clean → commit cycle would delete a tracked artifact) |
| CI/CD readiness | **1** | No CI config of any kind; no lint/format config (ruff/black absent); not a git repository in this bundle form (no history to hook CI onto) |
| Portability | **3** | Path handling is properly `Path(__file__)`-relative (works from any CWD); SQLite is universal; verified clean run on Windows + newest library stack. Docked: bare `pytest` broken everywhere (packaging), Makefile POSIX-only, handbook copy of the generator carries a hard-coded `/home/claude` path |
| Scalability | **2** | Full-table views over a 0.8 MB SQLite file — instant today, and honestly scoped to a demo. Nothing is incremental; every view scans history; `st.cache_data` never invalidates (a rebuilt DB shows stale dashboards until restart). Fine at 300 wafers; the *architecture* (SQL views + star facts) can scale, the *implementation* hasn't needed to |
| Maintainability | **3** | Small, readable, consistently styled, well-commented. Working against it: five surfaces of one story to keep in sync (py/sql/ipynb/README/PDF — a change to any query touches up to five places), duplicated constants, and the seed-locked tests that make any generator change a mass test-rewrite |

**Not scored — correctness of what exists:** everything the code claims to compute, it computes correctly (all 26 tests pass; every README number reproduces; internal reconciliations verified exact). The defects found are architectural and epistemic, not bugs.

## The one outright defect

**The documented test command fails on a clean environment.** README step 5 and `make test` say `pytest -q`; with no packaging and no `pythonpath` setting, collection dies with `ModuleNotFoundError: No module named 'src'`. It passes only as `python -m pytest` (CWD lands on `sys.path`). Root cause: the project is not a package (`src/__init__.py` empty, no `pyproject.toml`, no `[tool.pytest.ini_options] pythonpath`). This is P0 debt: it breaks the very first thing a reviewer runs.

## Technical debt register (ranked)

| # | Debt | Where | Risk | Fix shape (future phase — not now) |
|---|---|---|---|---|
| 1 | No packaging → broken `pytest`, `sys.path` hacks in app/notebook | repo root, `app/ops_dashboard.py:29`, notebook cell 1 | first-run failure | `pyproject.toml` with src-layout install + pytest config |
| 2 | Conclusion constant in 4 files | investigation.py:20, charts.py:27, ops_dashboard.py:33, test_queries.py:22 | epistemic (see RCA_AUDIT); also a sync hazard | suspect becomes engine *output* |
| 3 | Five duplicated story surfaces | src/, sql/rca_queries.sql, notebook, README, PDF | silent divergence (already: README "~9 pts" vs actual −8.5…−9.9 is fine, but nothing checks) | single source (engine output) renders the others |
| 4 | Seed-locked test assertions | test_queries.py:60–63 (`== 223`) | any data change breaks unrelated tests | assert invariants and reconciliations, not magic numbers |
| 5 | `step_id = 4` in 9 places | views.sql, rca_queries.sql, investigation.py, dashboard | blocks generalization to other steps | parameterized semantic layer |
| 6 | Stale-DB reuse in build | build_db.py:48–51 | stale analytics after generator edits | content-hash or explicit `--rebuild` |
| 7 | `make clean` deletes a committed artifact | Makefile:33 | repo state corruption | exclude `fab_database.sql` |
| 8 | Undeclared `nbformat` dependency | build_notebook.py:9 | breaks `make notebook` on minimal installs | declare it |
| 9 | Duplicated chart code + constants | charts.py vs ops_dashboard.py | drift between README figures and dashboard | shared chart module |
| 10 | SQL-injection-shaped API (`run_view` f-string) | db.py:47 | negligible today (local, trusted); bad habit | whitelist view names |
| 11 | No lint/format/typecheck config | repo root | style drift as code grows | ruff + mypy in CI |
| 12 | `st.cache_data` without invalidation | ops_dashboard.py:46–53 | stale dashboard after rebuild | key cache on DB mtime |

## What is *not* debt (deliberate, defensible choices to keep)

- SQLite as the only store; no services, no Docker, no orchestration — right-sized and portable.
- Committed 0.8 MB DB for instant clone-and-run, with regeneration documented.
- Matplotlib + Streamlit — boring, appropriate technology.
- Stdlib-only generator.
- Tests that assert analytical *content* — the pattern is right even where today's target (the plant) is wrong.
