# Target Architecture — Fab Ops Intelligence

**Derivation:** this design grows the audited repository's strongest verified idea — multi-signal convergence RCA on an MES-style relational model — into an operations-intelligence platform. It is deliberately *not* the generic reference architecture from the audit brief; every block below traces to a finding in the audit documents.

---

## 1. Design principles (from the audit)

1. **Answer-blind analytics.** The single most important change: analysis code never contains a conclusion. Faults live in scenario configs read only by the data engine; the platform must *earn* every suspect it names. (RCA_AUDIT §1.0, SYNTHETIC_DATA_AUDIT #5.)
2. **Evaluation is part of the product.** A platform that claims detection/attribution ships the benchmark that measures it (detection rate, attribution precision/recall, false-positive rate on null scenarios). This converts "portfolio narration" into "testable system" — the project's core differentiator.
3. **Keep the boring, working substrate.** SQLite + SQL views + pandas + matplotlib + Streamlit + pytest survived verification with zero correctness defects. No databases, services, brokers, or frameworks are added. Scale ambitions are rejected explicitly (§6).
4. **Explainable statistics only.** Every detector and score is a formula an engineer can recompute by hand: SPC rules, CUSUM/EWMA, permutation tests, effect sizes, additive evidence scores. No ML until the evaluation harness exists to justify it — and none is required for the target capability set.
5. **Operations focus; knowledge stays out.** Per FABOPS_VS_FABKG_BOUNDARY.md: relationships here are foreign keys and statistics, never graphs/ontologies. Domain knowledge enters as one small versioned data table.

## 2. Target system architecture

```
┌─────────────────────────── DATA PLANE ────────────────────────────┐
│                                                                    │
│  fabsim (synthetic fab engine)                                     │
│    scenario config (YAML: faults, onset, severity, seed)           │
│    physics-mediated models: variation stack, degradation,          │
│    maintenance/recovery, routing, defects→die-kill→yield           │
│         │  writes (only fabsim ever sees the scenario)             │
│         ▼                                                          │
│  SQLite  fab.db     entities: products lots wafers runs recipes    │
│                     chambers tool_events inspections defects       │
│                     metrology yield maintenance                    │
│         │                                                          │
│         ▼                                                          │
│  Semantic layer (SQL)   facts: fact_wafer_step fact_yield          │
│                         fact_defect fact_tool_day                  │
│                         views: monitoring / exposure / spatial     │
└──────────────┬─────────────────────────────────────────────────────┘
               ▼
┌────────────────────── INTELLIGENCE PLANE (python pkg) ─────────────┐
│                                                                    │
│  monitors/           process: SPC rules, EWMA/CUSUM, drift onset   │
│   (per domain)       equipment: states, MTBF/MTTR, degradation,    │
│                        maintenance-effect deltas                   │
│                      yield: target-normalized trend, decomposition │
│                      defect: rates, Pareto movers, spatial-        │
│                        signature scores per wafer                  │
│         │  emit                                                    │
│         ▼                                                          │
│  detection/          excursion detector → Excursion object         │
│                      (what signal, when, scope, severity)          │
│         ▼                                                          │
│  diagnosis/          hypothesis enumeration (exposure dimensions)  │
│                      evidence collection per family                │
│                      evidence correlation (effect size, permu-     │
│                      tation significance, temporal alignment)      │
│                      scoring + ranking (+ "insufficient evidence") │
│         ▼                                                          │
│  impact/             counterfactual loss, lot/wafer exposure,      │
│                      containment ranking                           │
│         ▼                                                          │
│  actions/            recommendation templates from fault class ×   │
│                      impact × local knowledge table                │
│         ▼                                                          │
│  investigation record (JSON artifact; optional FabKG export)       │
└──────────────┬─────────────────────────────────────────────────────┘
               ▼
┌──────────────── PRESENTATION ────────────────┐  ┌──── EVALUATION ────┐
│ dashboard (Streamlit): renders engine output │  │ scenario benchmark │
│ CLI: fabops build/monitor/investigate/report │  │ detection & attri- │
│ notebook(s): generated case studies          │  │ bution metrics,    │
│                                              │  │ null-scenario FP   │
└──────────────────────────────────────────────┘  │ rate; runs in CI   │
                                                  └────────────────────┘
```

The audit brief's conceptual stack (PROCESS/EQUIPMENT/YIELD intelligence → anomaly → diagnosis → impact → action) survives contact with the audit in modified form: the three intelligence domains become **monitor families feeding one shared detection→diagnosis→impact→action spine**, rather than three separate silos — because the audit shows the project's value is precisely the *convergence* of families on one conclusion.

## 3. Domain dispositions (audit brief §9)

| Domain | Capability set (target) | Disposition | Grounds |
|---|---|---|---|
| A. Process Intelligence | SPC/control charts, drift + change-point detection, parameter correlation, recipe comparison | **NEW** (data first) | Channel is empty today — generator emits noise (verified); nothing exists to refactor |
| B. Equipment Intelligence | tool/chamber health, states/utilization, downtime analytics, maintenance-effect analysis, degradation trends | **EXTEND → partially NEW** | Downtime totals + `v_tool_downtime` exist and are correct; states/chambers/degradation need data-model Tier 1 |
| C. Yield Intelligence | target-normalized monitoring, decomposition by step/tool/time, lot/product analysis | **REFACTOR + EXTEND** | Views exist but are mix-confounded (`v_weekly_yield` artifact, verified) and bins are cosmetic; the star-fact pattern is right |
| D. Wafer/Defect Intelligence | rates/Pareto/trends, spatial-signature scoring per wafer, tool association | **KEEP + EXTEND** | The verified strength: real coordinates, calibrated zones, the wafer-map showpiece. Deepen (per-wafer signature metrics, misclassification-tolerant) |
| E. Root-Cause Intelligence | excursion objects, hypothesis enumeration, evidence framework, scored ranking, temporal reasoning | **REBUILD** | Current RCA is narration of a constant (verified); the *arc* and the scorecard shape are kept as the design template |
| F. Decision Support | impact counterfactual, exposure/containment, recommended checks, post-action validation | **KEEP (impact/exposure math) + NEW (the rest)** | Step-7/8 queries are sound templates; recommendations are static text today |

## 4. Target repository structure (audit brief §13; create during roadmap, not now)

```
fab-ops-intelligence/
├── pyproject.toml            # src-layout package; fixes the broken-pytest P0
├── src/fabops/
│   ├── config.py             # paths, thresholds, zone cut-offs — the end of magic literals
│   ├── db.py                 # kept from today (access choke point), hardened
│   ├── semantic/             # SQL: schema, facts, views (today's sql/, generalized)
│   ├── monitors/             # process.py equipment.py yield_.py defect.py
│   ├── detection/            # excursion.py (objects + detectors)
│   ├── diagnosis/            # hypotheses.py evidence.py scoring.py
│   ├── impact/               # loss.py exposure.py
│   ├── actions/              # recommend.py + knowledge table (data file)
│   └── report/               # investigation artifact writer; chart module (single copy)
├── src/fabsim/               # synthetic fab engine (separate package: the only code
│   ├── scenario.py           #   allowed to see fault configs)
│   ├── models/               # variation, degradation, defects, yield physics
│   └── emit.py               # → SQLite + .sql dump (keep dual output)
├── scenarios/                # YAML: demo_etch02.yaml, null.yaml, randomized templates
├── eval/                     # benchmark runner + metrics + expected-outcome fixtures
├── app/                      # dashboard (renders engine outputs only)
├── notebooks/                # generated case studies (build_notebook pattern kept)
├── tests/                    # unit (monitors, scoring) + integration (scenario → conclusion)
├── data/                     # generated artifacts (committed demo build kept)
└── docs/                     # this audit + ADRs + user guide
```

Migration note: today's `data/generate_fab_db.py` remains untouched until `fabsim` reproduces its demo scenario; today's views migrate into `semantic/` largely intact (they are correct — they just need parameterization and mix-normalization); `sql-mastery-handbook/` stays a sibling artifact, out of this repo.

## 5. Fab-realism guardrails carried into design

From the data-model audit, the target schema adds exactly the Tier 1 + Tier 2 entities (chambers, recipes, tool_events, metrology, die-grid yield, genealogy) and enforces one event clock (no production during downtime; verified violated today). Chamber-grain analysis becomes possible — and the demo story finally matches its own wording ("marginal chamber").

## 6. What NOT to build (audit brief §16 — binding)

| Rejected | Why |
|---|---|
| Microservices, Kubernetes, Docker-compose stacks | One SQLite file and a Python package serve the entire honest scope |
| "Real-time" streaming (Kafka, websockets, auto-refresh theater) | The platform is batch analytics on generated data; pretending otherwise is fabrication |
| Cloud architecture / managed warehouses | Adds cost and setup friction; removes clone-and-run reproducibility — the project's verified strength |
| LLM wrappers, agents, RAG, chat interfaces | No semiconductor engineering question in scope needs them; they would blur the explainability guarantee (§1.4) |
| Knowledge graphs / ontologies / triple stores | FabKG's domain; boundary doc is binding |
| ML models for detection/attribution (initially) | The statistical baseline must exist and be benchmarked first; ML enters only if the benchmark shows headroom, and then as a measured comparison |
| Fabricated production claims ("deployed", "real fab data", "reduced scrap by X%") | Synthetic provenance is disclosed everywhere today — that honesty is a differentiator; keep it |
| Multi-fab / big-data scale theater | 300→~3,000 wafers per scenario is plenty to demonstrate every capability; SQLite handles it trivially |
| A REST API | No consumer exists; the artifact files are the integration surface |

**Definition of done for the target:** `fabops investigate` on a *randomized, unseen* scenario names the planted fault in its ranked output (or says "insufficient evidence" when the scenario is null), shows the evidence table that justifies it, quantifies impact — and `eval/` proves it does so across the scenario suite with reported precision/recall. Everything else (dashboard, notebooks, README) renders that capability.
