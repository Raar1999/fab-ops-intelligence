# RCA Audit

**Subject:** how the project reaches the conclusion "ETCH-02 is the root cause," traced mechanism-by-mechanism through `src/investigation.py`, `sql/rca_queries.sql`, `sql/views.sql`, `src/charts.py`, `app/ops_dashboard.py`, and `tests/test_queries.py`. Plus the design direction for a real diagnostic engine.

---

## 1. The exact mechanism (what actually happens)

### 1.0 The conclusion precedes the analysis

`SUSPECT = "ETCH-02"` is a module-level constant in **four files**:

| File | Line | Used for |
|---|---|---|
| `src/investigation.py` | 20 | parametrizes steps 4, 5, 7, 8; interpolated into the narrative text |
| `src/charts.py` | 27 | red "alert" coloring; chart titles that state the conclusion ("three independent signals all point at ETCH-02") |
| `app/ops_dashboard.py` | 33 | row highlighting, default selectbox choice, captions ("Select ETCH-02 to see the dense edge-ring") |
| `tests/test_queries.py` | 22 | asserts ETCH-02 is worst on every signal |

No code path computes a suspect from data. The three views that *could* surface any tool (`v_etch_tool_yield`, `v_edge_ring_by_tool`, `v_tool_downtime`) feed tables whose accompanying narration is fixed text — e.g. `step6_converge` prints "*ETCH-02 is simultaneously worst on yield, highest on edge-ring %, and highest on unscheduled downtime*" unconditionally (`investigation.py:99–100`), and step 2 announces whichever tool sorts first as if it were the known suspect.

### 1.1 Step-by-step signal trace

| Step | Signal used | Computation | Threshold / rule | Reality check (verified) |
|---|---|---|---|---|
| 1 Symptom | yield vs target per product | `AVG(yield_pct) − target` GROUP BY product | none — prose claims "uniform ⇒ shared infrastructure" | Gaps −8.5…−9.9. The uniformity is *manufactured*: every product routes 50% of wafers to the fault (generator line 308), so uniform loss is a routing constant, not an inference |
| 2 Suspect | yield by gate-etch tool | `AVG(yield_pct)` GROUP BY tool at `step_id=4` | none — lowest sorts first | 64.29 vs 75.91 / 79.39. Gap ≈ 4σ of wafer noise; unmissable by construction |
| 3 Confirm #1 | edge-ring fraction by tool | share of `defect_type='EDGE_RING'` GROUP BY tool | none — highest sorts first | 43.7% vs 13.9 / 8.8. Driven by generator weights [45 vs 12] |
| 4 Confirm #1b | mean radius + edge-zone share, suspect vs rest | AVG(radius), % in `edge` zone | none | 106.7 vs 85.0 mm; 61.5 vs 38.6%. **Circular**: coordinates were generated *from* the type label; this verifies the generator's copy function, not an independent physical fact |
| 5 Confirm #2 | unscheduled downtime by etch tool | SUM(duration), COUNT GROUP BY tool | none | 30.53 h/4 events vs 0/0. The "0 for others" is a seed artifact (generator allows 0–2 for good tools). Event *timestamps* are causally meaningless — only totals are usable, and the narration's "a chamber drifting out of spec also breaks down" is asserted, not shown (no temporal linkage exists) |
| 6 Convergence | the three signals joined per tool (`v_tool_rca`) | visual/tabular convergence | none — no scoring, no ranking metric | The scorecard *shape* is right; the "finding" is that one tool tops all three columns — true, and guaranteed by the generator |
| 7 Impact | counterfactual die loss | `total_die × (good-etcher avg − wafer yield)` summed over suspect wafers | benchmark = mean of non-suspect wafers | 117 wafers, 46,575 die. Method is a reasonable first-order counterfactual; ignores product mix in the benchmark (fortunately unconfounded here — by construction) |
| 8 Exposure | % of lot's wafers on suspect | COUNT ratio per lot | none | Range 28–64%: weak discrimination because routing is 50% everywhere |
| Rec. | fixed text | none | none | Recommendation text is entirely static (`investigation.py:150–160`) |

### 1.2 Formal characterization (the brief's A–E)

- **A. Genuinely discovers a root cause? NO.** Nothing searches a hypothesis space. The candidate set (etch tools at step 4) and the culprit are both pre-specified.
- **B. Uses deterministic rules? YES**, implicitly: "lowest mean yield," "highest edge-ring %," "highest downtime" — each an argmax with no uncertainty, no threshold, no significance test, no effect-size floor.
- **C. Rediscovers a planted synthetic relationship? YES.** Every signal traces to `BAD_ETCH_ID = 4` (generator line 234). See `SYNTHETIC_DATA_AUDIT.md` §2.
- **D. Combines several forms of evidence? YES — the redeeming quality.** Three evidence families joined per-tool is the correct investigative *architecture*, even though combination = visual convergence rather than scoring.
- **E. Contains leakage? YES**, two kinds: (1) data-level — the generator injects an 8-point direct tool→yield effect (of a ~12-pt observed gap, only ~3.7 pts are defect-mediated; verified); (2) code-level — the conclusion constant ships inside the analysis, charts, dashboard, and tests.

**Verdict: B + C + D with E.** The honest description of the current system is: *a well-narrated, multi-signal verification of a planted fault, with the answer compiled in.* The README's own disclaimer ("deliberately seeded with one discoverable root cause") concedes this; the audit's addition is that the code never actually performs the discovery even on the planted data.

### 1.3 What is genuinely worth keeping

1. **The investigative arc** (symptom → suspect → independent confirmation → convergence → impact → exposure → action) — this is how excursions are actually worked; it should become the platform's *runtime* structure, not its narration.
2. **The multi-signal scorecard** (`v_tool_rca`) — the seed of an evidence table.
3. **Spatial verification instinct** — checking that a claimed signature is physically where it should be is real discipline; it just needs data where that check *can fail*.
4. **Impact + exposure quantification** — the step most analyses skip; keep both queries as the template for the impact engine.
5. **Tests that pin analytical findings** — asserting "the story holds" is a good pattern; it must evolve into "the *engine* finds the story" (assert on engine output for a scenario, not on the answer constant).

### 1.4 Identifiability failures to fix in the data before RCA can be real

- Gate etch vs metal etch is **unidentifiable**: every wafer uses the same tool at steps 4 and 11 (verified 0 exceptions), yet the conclusion names gate etch.
- Tool vs chamber is unidentifiable at chamber grain (chambers are cosmetic).
- Onset time does not exist; "when did it begin" has no answer.
- No confounders exist, so controlling for them — the actual hard part of commonality analysis — cannot be demonstrated.

---

## 2. Future RCA direction (design only — nothing implemented)

### 2.1 Target workflow, mapped from what exists

```
OBSERVATION            v_yield_by_product, v_weekly_yield (target-normalized)     [EXTEND]
   ↓
ANOMALY/EXCURSION      NEW: detection engine — SPC rules on target-normalized      [NEW]
                       yield & defect rates; change-point (CUSUM/EWMA);
                       emits a first-class Excursion object (what, when, scope)
   ↓
CONTEXT                NEW: assemble the excursion's world — affected lots/steps/   [NEW]
                       time window; recent maintenance, recipe changes, alarms
   ↓
HYPOTHESIS GENERATION  NEW: enumerate candidates mechanically — every tool/chamber/ [NEW]
                       recipe/operator dimension sharing exposure with affected
                       wafers (generalizes today's "etch tools at step 4")
   ↓
EVIDENCE COLLECTION    v_etch_tool_yield, v_edge_ring_by_tool, v_tool_downtime      [REFACTOR]
                       generalized: per-candidate exposure split on each evidence
                       family, parameterized by step/window instead of step_id=4
   ↓
EVIDENCE CORRELATION   NEW: per (hypothesis × evidence family): effect size,        [NEW]
                       significance (permutation test — no scipy needed),
                       exposure balance check, temporal alignment with onset
   ↓
HYPOTHESIS SCORING     NEW: transparent additive score — no black box:              [NEW]
                       score = Σ family_weight × normalized_evidence, with the
                       per-family table always shown (the v_tool_rca scorecard,
                       earned instead of asserted)
   ↓
ROOT-CAUSE RANKING     NEW: ranked hypotheses with score breakdown + explicit       [NEW]
                       "insufficient evidence" outcome (must be reachable)
   ↓
IMPACT ANALYSIS        step-7 counterfactual die-loss query, generalized            [KEEP/EXTEND]
   ↓
RECOMMENDATION         NEW: rule-templated actions from fault class + impact +      [NEW]
                       exposure (today's static text becomes a template filled
                       from engine output)
```

### 2.2 Evidence sources, prioritized by engineering value

| Priority | Source | Why / status |
|---|---|---|
| 1 | Yield by exposure (tool/chamber/recipe) | exists for one dimension; generalize |
| 1 | Defect rate + spatial signature by exposure | exists for one dimension; the project's differentiator — deepen (per-wafer signature scoring, not just type shares) |
| 1 | Process parameters (SPC/summary stats) | **empty channel today** (generator emits noise); becomes usable after the data engine rework; unlocks drift/onset evidence |
| 2 | Maintenance history with temporal alignment | totals exist; timing meaningless today; after data rework: "excursion began 2 days after ETCH-02 PM" becomes computable evidence |
| 2 | Tool states/alarms | requires new entities (DATA_MODEL_AUDIT Tier 1) |
| 3 | Recipe versions | requires new entities; cheap high-value hypothesis family |
| 3 | Lot genealogy, historical excursions | later; genealogy mostly matters once rework exists |
| — | Wafer-map die-bin patterns | after die-grid yield exists (Tier 2) |

### 2.3 Non-negotiable design rules for the future engine

1. **The engine never imports the answer.** No `SUSPECT` constant anywhere in `src/` (it may live in *scenario configs* and in *expected-outcome fixtures* under `eval/`).
2. **Every conclusion carries its evidence table** — effect sizes, counts, p-values/permutation ranks — rendered wherever the conclusion is shown.
3. **"No root cause found" is a first-class outcome**, exercised by a null scenario in CI.
4. **Scored, not just sorted:** convergence becomes a number with a visible decomposition, so two candidate tools can tie honestly.
5. **Evaluation before sophistication:** the scenario benchmark (detection rate, attribution precision/recall, false-positive rate on null scenarios, time-to-detect) exists *before* any scoring cleverness is added, so every increment is measured. See `EXPANSION_ROADMAP.md`.
