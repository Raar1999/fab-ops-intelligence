# Dashboard Audit

**Subject:** `app/ops_dashboard.py` (195 lines, Streamlit, 4 tabs). Assessed as an engineering product: does it support an investigation workflow, or display charts?

---

## 1. What it is today

| Tab | Content | Interactivity |
|---|---|---|
| Overview | 6 KPI tiles (wafers, lots, avg yield, scrap rate, killer defects, unscheduled hours) + weekly yield line with min–max band | none |
| Yield | product target-attainment bars + table; node table; loss-decomposition table | none |
| Tool RCA | three-signal scorecard table (suspect row pre-highlighted pink) + normalized bar chart + lot-exposure table | none |
| Defect Maps | wafer map of all defects for wafers seen by a chosen etch tool | **one selectbox** (the only interactive control in the app; defaults to ETCH-02) |

Mechanics: all reads go through `src.db.run_query/run_view` (good); `st.cache_data` on every query (never invalidated — a rebuilt DB shows stale data until process restart); charts are matplotlib re-implementations duplicated from `charts.py`.

## 2. Verdict: a presentation, not an instrument

The dashboard is a competent *rendering of a finished report*. Three observations make this precise:

1. **The conclusion is in the copy.** `SUSPECT = "ETCH-02"` (line 33) drives row highlighting and the default map selection; the RCA tab's prose announces what the scorecard means before the user reads it; the maps tab caption instructs: "Select ETCH-02 to see the dense edge-ring." The user is guided *to* a conclusion, never *toward* one.
2. **There is no navigation between facts.** Nothing is clickable-through: a lot in the exposure table cannot open its wafers; a wafer cannot open its defect map or run history; a downtime event cannot show the wafers processed around it. Every fact is a terminal screen.
3. **There is no time.** No date filter anywhere; every aggregate is all-of-history. Combined with the product-mix artifact in `v_weekly_yield` (verified: each week bucket ≈ one lot ≈ one product, swinging 24 pts on mix), the only temporal chart on the dashboard is actively misleading as a monitoring surface.

Also noted: the Overview "avg yield" KPI averages across products with different targets (a mix-dependent number); "killer defects" counts a causally inert flag (see SYNTHETIC_DATA_AUDIT #11).

## 3. Workflow coverage assessment

| Workflow (target) | Question | Today | Gap |
|---|---|---|---|
| Fab overview | "What is happening today?" | Static totals of all history | No time axis, no alerting, no "what changed"; KPIs not target-normalized |
| Process monitoring | "Which processes are drifting?" | Absent | No parameter data worth monitoring exists (data-engine gap), no SPC views |
| Equipment health | "Which tools/chambers are abnormal?" | Downtime totals inside RCA tab | No per-tool page, no states/utilization, no chamber grain, no trends |
| Yield | "Where are we losing yield?" | Product attainment (good) + formulaic loss bins | No step/tool/time decomposition beyond gate etch; bins are generator constants |
| Investigation | "What caused this excursion?" | The pre-solved ETCH-02 story | No excursion object, no candidate ranking, no evidence drill-down; unusable for any *other* excursion |
| Impact | "What lots/wafers/products are affected?" | Lot exposure table | No wafer/product drill-through, no die-loss on dashboard (CLI only) |
| Action | "What should the engineer do next?" | Static recommendation (CLI/notebook only, not even on the dashboard) | No action/disposition surface at all |

## 4. Recommended future structure (design only — not implemented)

Principle: **the dashboard is the read surface of the diagnostic engine** (see TARGET_ARCHITECTURE). It renders engine outputs — excursions, evidence tables, rankings, impact — and never computes or asserts a conclusion itself. Suspects are highlighted because the engine ranked them, not because a constant says so.

Proposed information architecture (maps 1:1 to the workflow table above):

1. **Fab Today** — target-normalized yield trend (mix-corrected), active excursions list (engine output), tools in DOWN/QUAL state, top defect movers vs trailing baseline. Every item links into a workspace below.
2. **Process** — per step×parameter control charts with rule violations flagged; recipe-change markers on the time axis. (Unlocked only after the data engine emits real parameter signal.)
3. **Equipment** — per tool: state timeline, utilization, MTBF/MTTR, defect rate of wafers processed, parameter health; chamber tabs once chambers are entities.
4. **Yield** — attainment by product; decomposition by step/tool exposure; lot table with drill-through to wafers.
5. **Investigation workspace** (the centerpiece) — select an excursion → see scope (affected lots/wafers/window) → ranked hypotheses with per-family evidence scores → evidence detail views (exposure splits, spatial signature comparison, downtime alignment) → impact estimate → recommended actions. The current RCA tab's scorecard and wafer maps become *evidence views* inside this workspace.
6. **Wafer explorer** — retained and extended from today's maps tab (it is the best existing screen): per-wafer defect map + run history + yield, reachable by click from any lot/tool context.

Explicitly not recommended: real-time streaming widgets, auto-refresh theatrics, a chatbot pane, or any "AI insights" text box. A batch-analytics platform honestly presented as batch.
