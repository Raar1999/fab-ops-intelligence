# Current System Audit

**Audit date:** 2026-08-08
**Method:** every file in the repository was read; the database was rebuilt from the generator in an isolated environment; the test suite and the investigation driver were executed; every headline claim in the README was checked against the shipped `data/fab.db`. Nothing in this document is taken from documentation on trust — each claim is marked **VERIFIED** (reproduced here) or **CLAIMED** (asserted by docs, not independently checked).

---

## 1. What this repository actually is

`fab-ops-analytics` is a **small, self-contained, honest demonstration of a semiconductor yield root-cause investigation on synthetic data**. It is not a platform, not a monitoring system, and not a general analysis engine. It is a single, pre-authored investigation — "trace a fab-wide yield miss to one marginal etch tool" — executed cleanly across four presentation surfaces (CLI script, notebook, dashboard, README/PDF) on top of a well-organized SQLite + SQL-view analytical layer.

Its two genuinely strong properties, both verified:

1. **The investigation method is professionally structured.** Symptom → suspect → two independent confirmations (defect spatial signature + maintenance log) → convergence → impact quantification → exposure ranking → recommendation. That is the real arc of a yield excursion investigation.
2. **The engineering hygiene around reproducibility is real.** The generator is deterministic (verified byte-identical output modulo line endings when rebuilt on a different OS/Python), the notebook is genuinely executed and in sync with `src/`, all 26 tests pass, and every number in the README reproduces exactly.

Its central weakness, also verified: **the system does not discover anything.** The root cause `ETCH-02` is hard-coded as a constant in four separate files, the data generator plants the fault as a direct label effect, and the "investigation" is a narration that verifies a known answer. Details in `RCA_AUDIT.md` and `SYNTHETIC_DATA_AUDIT.md`.

### Bundle inventory (working tree root)

| Item | What it is | Verified role |
|---|---|---|
| `project5-fab-operations-analytics/` | The main repo (audited here) | Runnable end-to-end; 26/26 tests pass |
| `Fab_Operations_Analytics_README.pdf` | 587 KB PDF | Stated to be the README + charts as a shareable document (content not rendered in this environment; size consistent with 6 embedded PNGs) |
| `sql-mastery-handbook/` | 3,620-line, 13-phase SQL textbook + a copy of the same dataset | Separate educational artifact; teaches SQL against this schema; not part of the ops system |
| `README_BUNDLE.txt` | Bundle description | Accurate except one nuance: the two `generate_fab_db.py` copies differ by one line (the handbook copy hard-codes output path `/home/claude`, a leftover from its original build sandbox); the data logic is identical |

Not present anywhere: git metadata (`Is a git repository: false` — this is an exported bundle), CI configuration, `pyproject.toml` / `setup.py`, lockfile, `LICENSE`, `CHANGELOG`, `docs/` (until this audit).

---

## 2. Complete repository map

```
project5-fab-operations-analytics/
├── README.md                  # narrative + claims (all headline numbers VERIFIED, see §5)
├── requirements.txt           # pandas, numpy, matplotlib, streamlit, pytest, jupyter, nbclient (loose >= pins)
├── Makefile                   # setup / investigate / app / test / charts / notebook / clean (POSIX-only: rm -rf)
├── .gitignore                 # notes that data/fab.db + .sql are committed intentionally
├── build_notebook.py          # programmatic notebook assembly (nbformat); imports nbformat (NOT in requirements.txt)
├── data/
│   ├── generate_fab_db.py     # 615-line deterministic synthetic-data generator, seed=42
│   ├── fab.db                 # committed SQLite DB, 794,624 B (tables + fact_yield + 12 views + 3 indexes)
│   └── fab_database.sql       # committed portable dump, 739,748 B (DDL + INSERTs)
├── sql/
│   ├── star_model.sql         # fact_yield (one row per yield-tested wafer, pre-joined dims) + 3 indexes
│   ├── views.sql              # the 12 analytical views (all the analytical logic in the project)
│   └── rca_queries.sql        # the 8-step investigation as documented SQL (human-readable duplicate of investigation.py)
├── src/
│   ├── __init__.py            # empty
│   ├── db.py                  # 51 lines: connect / run_query / run_view (SQL → pandas)
│   ├── build_db.py            # 74 lines: run generator → apply star_model.sql → apply views.sql
│   ├── charts.py              # 229 lines: 6 matplotlib figures → reports/figures/
│   └── investigation.py       # 184 lines: prints the 8-step story, renders charts. SUSPECT="ETCH-02" hard-coded (line 20)
├── notebooks/
│   └── investigation.ipynb    # executed narrative (outputs verified consistent with src/ and DB)
├── app/
│   └── ops_dashboard.py       # 195-line Streamlit app: Overview / Yield / Tool RCA / Defect Maps
├── tests/
│   ├── conftest.py            # session fixture: builds DB if missing
│   └── test_queries.py        # 14 test functions → 26 collected (12 parametrized view checks)
└── reports/figures/           # 6 committed PNGs (regenerated identically by charts.py)
```

**Line-count reality check:** the entire Python surface is ~1,350 lines (generator 615, app 195, charts 229, investigation 184, build_db 74, db 51, conftest/tests ~200, build_notebook 249). The analytical logic proper is the 204-line `views.sql`. This is a compact demo, not a platform — which is fine, and the honest starting point for the expansion plan.

---

## 3. The complete pipeline (reconstructed from code, not docs)

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ DATA GENERATION      data/generate_fab_db.py     (seed=42, deterministic)     │
│   dimensions: 6 products, 15 tools (3 etchers; tool_id 4 = ETCH-02 flagged    │
│     BAD_ETCH_ID at line 234), 12 steps, 12 operators                          │
│   entities:  12 lots → 300 wafers → 3,145 runs → 742 inspections →            │
│              7,366 defects → 223 yield rows → 105 maintenance events          │
│   planted:   ETCH-02 → more EDGE_RING defects, −8 pts yield (direct),        │
│              4–7 unscheduled maintenance events; edge slots −3 pts;          │
│              yield −0.16 pts per defect                                       │
│   outputs:   data/fab.db (tables only) + data/fab_database.sql (dump)         │
└──────────────────────────────────┬────────────────────────────────────────────┘
                                   ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ DATA STORAGE         SQLite single file  data/fab.db                          │
│   11 normalized tables; TEXT timestamps; FKs declared (enforced only when     │
│   src/db.py connects: PRAGMA foreign_keys=ON)                                 │
└──────────────────────────────────┬────────────────────────────────────────────┘
                                   ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ SQL MODEL            src/build_db.py applies:                                 │
│   sql/star_model.sql → fact_yield (223 rows; wafer grain; lot/product/        │
│                        gate-etch-tool pre-joined) + 3 indexes                 │
│   sql/views.sql      → 12 views (v_yield_by_product … v_loss_decomposition)   │
│   NOTE: if data/fab.db exists, build_db REUSES it (regeneration is manual     │
│   delete-first; docstring's "idempotent" is only true for the view layer)     │
└──────────────────────────────────┬────────────────────────────────────────────┘
                                   ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ DATA ACCESS          src/db.py                                                │
│   run_query(sql, params) / run_view(name) → pandas DataFrame                  │
│   single choke point used by charts, investigation, dashboard, tests          │
└───────┬──────────────────────────┬──────────────────────────┬─────────────────┘
        ▼                          ▼                          ▼
┌───────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ ANALYTICS + RCA   │  │ VISUALIZATION           │  │ DASHBOARD               │
│ investigation.py  │  │ charts.py → 6 PNGs      │  │ app/ops_dashboard.py    │
│ 8 steps, prints   │  │ (suspect always drawn   │  │ 4 tabs, reads views +   │
│ tables + fixed    │  │  in alert red)          │  │ 3 inline queries;       │
│ narrative;        │  │                         │  │ st.cache_data           │
│ SUSPECT hard-coded│  │ SUSPECT hard-coded      │  │ SUSPECT hard-coded      │
└───────┬───────────┘  └─────────────────────────┘  └─────────────────────────┘
        ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ TESTS                tests/test_queries.py (26 collected)                     │
│   structural: tables/views exist, every view queryable, fact_yield == 223     │
│   analytical: ETCH-02 worst on all 3 signals; edge-ring radially at edge;    │
│               r < −0.2; all products miss target      SUSPECT hard-coded      │
└───────────────────────────────────────────────────────────────────────────────┘
```

Parallel presentation surfaces of the *same* content: `sql/rca_queries.sql` (SQL-client version), `notebooks/investigation.ipynb` (generated by `build_notebook.py`, executed), README (prose + embedded PNGs), PDF. **Five surfaces, one story — and the story's conclusion is compiled into every one of them.**

### Stage-by-stage notes (inputs / outputs / assumptions / limits)

| Stage | Inputs | Outputs | Key assumptions & hard-coded logic | Limits found |
|---|---|---|---|---|
| Generation | none (seed=42) | fab.db, fab_database.sql | `BAD_ETCH_ID=4`; routing `choice([3,4,4,5])` → 50% of wafers to the bad tool; yield formula at gen line 467 | fault is a static label, no temporal onset; process measurements are pure noise (see SYNTHETIC_DATA_AUDIT) |
| Storage | generator rows | SQLite | TEXT dates; FK enforcement optional | single file; fine at this scale |
| SQL model | fab.db | fact_yield + 12 views | gate etch is `step_id = 4` (hard-coded in 9 places across views/queries) | views are static aggregates; no parameters, no time windows |
| Access | SQL strings | DataFrames | trusted-input assumption (`run_view` interpolates the view name into SQL) | no caching, connection-per-call; fine at this scale |
| Analytics/RCA | views | printed narrative | `SUSPECT="ETCH-02"` at investigation.py:20 | narrative text asserts conclusions unconditionally (e.g. step 6 prints "ETCH-02 is simultaneously worst…" regardless of data) |
| Charts | views | 6 PNGs | `SUSPECT` at charts.py:27; titles state conclusions ("all point at ETCH-02") | chart titles would be wrong if data changed |
| Dashboard | views + 3 queries | Streamlit UI | `SUSPECT` at ops_dashboard.py:33 | display-only; one interactive control (see DASHBOARD_AUDIT) |
| Tests | views | pass/fail | `SUSPECT` at test_queries.py:22; `fact_yield == 223` seed-locked | tests pin the *current seed's* story, so any generator change breaks them by design |

---

## 4. Execution-flow verification (what was actually run)

All runs were performed on a **copy** of the repo in an isolated scratch directory with a fresh venv (Python 3.11.9, pandas 3.0.5, numpy 2.4.6, matplotlib 3.11.1, pytest 9.1.1 — deliberately newer than the requirement floors):

| Command | Result |
|---|---|
| `python -m src.build_db` (after deleting fab.db) | **Works.** Regenerates DB; all planted-signal printouts match shipped DB exactly |
| Determinism check | Regenerated `fab_database.sql` is **byte-identical to the shipped file after CRLF normalization** (SHA-256 `a231d751…` both). The generator is fully deterministic across OS and Python 3.11 |
| `pytest -q` (the README/Makefile command) | **FAILS on a clean environment**: `ModuleNotFoundError: No module named 'src'`. There is no packaging and no `pythonpath` config; bare `pytest` does not put the repo root on `sys.path` |
| `python -m pytest -q` | **26 passed in 0.32 s** (module mode puts CWD on `sys.path`) |
| `python -m src.investigation` | **Works end-to-end**; prints all 8 steps, renders all 6 figures |
| `streamlit run app/ops_dashboard.py` | Not executed (would require installing streamlit; code review + shared `run_view` path gives high confidence; the app imports only from `src.db` and matplotlib) |

Forward-compatibility note: the code runs clean on pandas 3.0 / numpy 2.4 — the loose `>=` pins are currently harmless, but nothing guards this (no CI, no lockfile).

---

## 5. Claimed vs verified

| README claim | Verdict | Evidence |
|---|---|---|
| "all six products miss their yield target by ~9 points" | **VERIFIED** | gaps −8.48 … −9.89 across the 6 products |
| "64.3% yield vs 79.4% for the best etcher" | **VERIFIED** | ETCH-02 64.29 (117 yield-tested wafers), ETCH-01 79.39 (51), ETCH-03 75.91 (55) |
| "3× the edge-ring defect rate" | **VERIFIED** (understated) | 43.7% vs 13.9% / 8.8% — 3.1× vs next, 5× vs best |
| "**all** of the fab's unscheduled etch downtime" | **VERIFIED for etch, fragile** | ETCH-02: 30.53 h / 4 events; ETCH-01/03: zero. But the generator allows 0–2 unscheduled events for good tools — the "all" is luck of the seed, and non-etch tools do have unscheduled downtime (CLN-01 8.1 h, CMP-02 6.5 h, …) |
| "mean defect radius 107 mm vs 85 mm; 62% edge zone vs 39%" | **VERIFIED** | 106.7 vs 85.0 mm; 61.5% vs 38.6% |
| "Pearson r = −0.55" | **VERIFIED** | computed −0.550 (n=223) |
| "~117 yield-tested wafers ran through ETCH-02" | **VERIFIED** | 117 (of 154 routed; the rest scrapped/in-progress) |
| "26 tests" | **VERIFIED** | 26 collected, 26 pass — but only via `python -m pytest` (documented command broken, see §4) |
| "12 analytical views" | **VERIFIED** | 12 views + fact_yield + 3 indexes present in shipped DB |
| "reproduces exactly via python -m src.investigation" | **VERIFIED** | outputs match notebook and README |
| "EDGE_RING … mean radius of ~143 mm and CENTER at ~18 mm" (views.sql header) | **VERIFIED** | 143.4 / 18.2 mm |
| "deliberately seeded with *one* discoverable root cause" | **ACCURATE AND IMPORTANT** | the honesty is real; but "discoverable" ≠ "discovered" — the code never performs discovery (see RCA_AUDIT) |

**Conclusion of the claims audit: the README is unusually honest and every checkable number reproduces.** The gap between claim and reality is not in the numbers — it is in the words "investigation," "isolates," and "traces," which describe a narrated verification of a planted answer, not an analytical procedure that could run on data whose answer is unknown.

---

## 6. Original engineering intent (reconstructed)

Evidence: the README's "Why this project" section ("a yield/process/data interview is really testing … can you drive an investigation?"), the résumé-bullet section, the recruiter-oriented PDF, and the companion SQL handbook.

**The project is a portfolio artifact whose engineering identity is *yield engineering / root-cause analysis*, expressed through *analytics engineering* craft** (SQL view layer, star model, thin consumers, tests that pin findings). It is not primarily data engineering (no pipelines/ingestion), not equipment engineering (maintenance data is a prop for one signal), not process engineering (process parameters carry no information), and not dashboarding (the dashboard is the thinnest surface).

**The strongest existing idea — the thing to build the platform around — is the multi-signal convergence investigation**: independent evidence families (yield by equipment exposure, defect spatial signature, maintenance/downtime) triangulating one physical cause, with impact and containment quantified. Every expansion decision in the companion documents grows this idea (make detection real, make attribution earn its conclusion, make evidence a first-class object, make the answer *not known in advance*) rather than replacing the identity with something else.

---

## 7. Cross-references

- Data model detail and operational-question coverage → `DATA_MODEL_AUDIT.md`
- Generator mechanics, planted-relationship classification, leakage → `SYNTHETIC_DATA_AUDIT.md`
- Exact RCA mechanism trace and verdict → `RCA_AUDIT.md`
- Engineering-quality scores and debt register → `SOFTWARE_ENGINEERING_AUDIT.md`
- Dashboard workflow assessment → `DASHBOARD_AUDIT.md`
- Capability gaps → `GAP_MATRIX.md`; future design → `TARGET_ARCHITECTURE.md`; sequencing → `EXPANSION_ROADMAP.md`
- Scope boundary vs the separate FabKG project → `FABOPS_VS_FABKG_BOUNDARY.md`
