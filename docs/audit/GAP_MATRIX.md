# Gap Matrix

Current state is what the audit *verified*, not what documentation claims. Priorities: **P0** foundation · **P1** core platform · **P2** advanced intelligence · **P3** optional research/product. Complexity: S (<1 wk-equivalent), M (1–3 wk), L (>3 wk) for a single engineer.

| Capability | Current State | Evidence (audit ref) | Gap | Target State | Priority | Complexity |
|---|---|---|---|---|---|---|
| **Foundation** |
| Packaging & runnable tests | Not a package; documented `pytest -q` fails (`No module named 'src'`) | SE_AUDIT "one outright defect" | No pyproject, no pytest config | src-layout package; `pytest` green from clean clone; CLI entry points | **P0** | S |
| CI | None; not even a git repo in bundle form | CURRENT_SYSTEM §1 | Everything | git init + GitHub Actions: lint, typecheck, tests, demo-scenario smoke run | **P0** | S |
| Configuration | Zero config; ~10 magic literals, several duplicated ×4 | SE_AUDIT scores (config 1/5) | No config layer | `fabops.config`: paths, thresholds, zone cut-offs, windows | **P0** | S |
| Logging / error handling | `print()`; one deliberate error path | SE_AUDIT | No diagnostics discipline | stdlib `logging`; typed exceptions at data-access + engine boundaries | **P0** | S |
| Lockfile / dep hygiene | Loose `>=` floors; `nbformat` undeclared | SE_AUDIT debt #8 | No reproducible env | pinned lock (pip-tools or uv); declared extras (app, notebook, dev) | **P0** | S |
| **Data** |
| Data model (ops entities) | 11 tables; no chambers/recipes/tool-states/metrology/genealogy; chambers & killer_flag cosmetic | DATA_MODEL §1, §3 | Tier 1+2 entities missing | Schema v2 with chambers, recipes, tool_events, metrology, die-grid yield | **P1** | M |
| Temporal consistency | 34 runs overlap own tool's downtime; WIP-aging anomaly; inspection clock separate from run clock | DATA_MODEL §1 | No single event clock | Generator enforces clock invariants; self-test asserts them | **P1** | M |
| Synthetic data engine | Single hard-wired scenario; fault = direct label effect (−8 pts); no onset; 50% routing; params pure noise | SYNTH_AUDIT §2 | Answer-blindness impossible | `fabsim`: scenario configs, fault library incl. null, physics-mediated effects, drift/degradation/recovery, confounded routing | **P1** | **L** |
| Semantic layer | 12 correct but static views; `step_id=4` ×9; mix-confounded weekly trend | DATA_MODEL §1; SE_AUDIT debt #5 | Parameterization + normalization | Fact tables (wafer_step, defect, tool_day) + windowed, target-normalized views for any step | **P1** | M |
| **Process intelligence** |
| SPC / control charts | Nothing (and no signal in data to chart) | SYNTH_AUDIT #10 | Entire capability + its substrate | Per step×parameter charts, Western-Electric/Nelson rule hits | P1 | M (after fabsim) |
| Drift / change-point detection | Nothing | RCA_AUDIT §1.4 | Entire capability | EWMA/CUSUM with estimated onset time (feeds diagnosis temporal alignment) | P1 | M |
| Recipe comparison | Recipes don't exist | DATA_MODEL §3 | Entity + analytics | Yield/defect/param splits by recipe version; change markers | P2 | S (after schema v2) |
| **Equipment intelligence** |
| Downtime analytics | Correct totals per tool (`v_tool_downtime`) | verified | Trends, states, blocking semantics | State timelines, utilization, MTBF/MTTR from tool_events | P1 | M |
| Chamber-level analysis | chamber_id random noise; verified flat | DATA_MODEL §1 | Chamber entity + planted chamber faults | Chamber-grain exposure splits & health | P1 | M |
| Degradation detection | Impossible (no trajectories in data) | SYNTH_AUDIT §3.1-5 | Data + detector | Between-PM degradation trends; alert before failure | P2 | M |
| Maintenance-effect analysis | Impossible (maintenance changes nothing) | DATA_MODEL §2 | Data + before/after stats | Post-PM shift detection; "did the fix work" validation | P2 | M |
| Alarm correlation | No alarms exist | DATA_MODEL §3 | Entity + analytics | tool_events alarms × excursion windows | P2 | M |
| **Yield intelligence** |
| Target-normalized monitoring | Absent; current weekly view is a product-mix artifact (verified 24-pt mix swings) | DATA_MODEL §1 obs 3 | Normalization | Attainment-vs-target trend; mix-corrected | **P1** | S |
| Yield decomposition | 3 formulaic bins read back from generator constants | SYNTH_AUDIT #12 | Mechanism-based loss | Loss attribution from die-grid + defect + param data | P2 | L |
| Step contribution | No step-level yield concept | DATA_MODEL §2 | Data + method | Exposure-based step attribution in diagnosis engine | P2 | M |
| **Wafer / defect intelligence** |
| Spatial signatures | **Strongest verified capability**: real x/y, calibrated zones, showpiece maps | DATA_MODEL §2 | Per-wafer scoring; robustness | Signature score per wafer (ring/center/scratch/cluster metrics), misclassification-tolerant | P1 | M |
| Defect trends/Pareto movers | Static Pareto view only | DASHBOARD §3 | Time dimension | Movers vs trailing baseline; per tool/step | P1 | S |
| Wafer-map die-bin patterns | No die-level yield data | DATA_MODEL §3 Tier 2 | Data + analytics | Die-grid kill maps; defect-overlay validation of killer model | P2 | L |
| **Root-cause intelligence** |
| Excursion detection | None — the "excursion" is eternal and pre-known | RCA_AUDIT | Entire capability | Detector over monitor outputs → Excursion objects (what/when/scope) | **P1** | M |
| Hypothesis enumeration | Candidate set hard-coded (etch tools at step 4) | RCA_AUDIT §1.1 | Generalization | Mechanical enumeration over all exposure dimensions | **P1** | M |
| Evidence framework | 3 correct single-dimension views + visual convergence; no scoring/stats | RCA_AUDIT §1.1–1.3 | Effect sizes, significance, temporal alignment, scoring | Per-hypothesis evidence table; permutation tests; additive transparent score; "insufficient evidence" outcome | **P1–P2** | **L** |
| Answer-blindness | `SUSPECT="ETCH-02"` in 4 files | RCA_AUDIT §1.0 | The core epistemic fix | No conclusion constants outside scenarios/ and eval fixtures; lint rule enforces | **P1** | S (rule) — engine makes it real |
| Impact quantification | Sound counterfactual query (46,575 die, verified) | RCA_AUDIT §1.3 | Generalize benchmark (mix-aware) | Impact module usable for any hypothesis | P1 | S |
| Containment / exposure | Query exists; weak discrimination (28–64%, routing artifact) | RCA_AUDIT §1.1 | Data realism + wafer grain | Lot/wafer exposure ranking with realistic routing | P1 | S |
| Recommendations | Static text | RCA_AUDIT §1.1 | Templated from engine output | Fault-class → checks/actions via local knowledge table | P2 | S |
| Post-action validation | Nothing | brief §9F | Data (recovery) + before/after stats | "Did yield recover after the fix" check on scenario timelines | P3 | M |
| **Presentation** |
| Dashboard as instrument | 4 static tabs, 1 selectbox, conclusion in copy, no time axis, stale cache | DASHBOARD §1–2 | Investigation workspace | Renders engine outputs; drill-through lot→wafer→map; excursion workspace | P2 | M |
| Case-study notebook | Executed, in sync (verified) — but narrates the plant | CURRENT_SYSTEM §3 | Regenerate from engine | Generated case study per scenario from investigation artifacts | P2 | S |
| **Evaluation** |
| Scenario benchmark | Nothing (impossible today: one scenario, answer known) | RCA_AUDIT §2.3 | The differentiator | `eval/`: detection rate, attribution P/R, FP rate on null, time-to-detect; runs in CI; results table in README | **P1** | M |
| Test suite quality | 26 pass; seed-locked exact counts; asserts the plant | SE_AUDIT | Re-target assertions | Unit tests for monitors/scoring + integration "engine finds scenario X" + generator invariant self-tests | P1 | M |
| **Positioning** |
| FabKG boundary | Implicit only | BOUNDARY doc | Formalization | Artifact export schema committed; anti-coupling rules in CONTRIBUTING | P2 | S |
| Public README / honesty | Excellent and verified honest | CURRENT_SYSTEM §5 | Reframe from "story" to "system + benchmark" | README leads with capability + eval table + limits | P2 | S |
| License / contrib files | Absent | CURRENT_SYSTEM §1 | Legal hygiene | MIT/Apache-2 + CONTRIBUTING | **P0** | S |

**Reading of the matrix:** P0 is a few days of hygiene. The critical path of the platform is the P1 column of the Data + RCA sections — `fabsim` (answer-blind scenarios) → semantic layer v2 → monitors → detection → diagnosis → **evaluation harness**. Everything in P2 (dashboard workspace, decomposition, maintenance-effect, recipes) becomes straightforward once that spine exists, and is wasted effort before it.
