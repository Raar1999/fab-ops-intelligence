# Synthetic Data Audit

**Subject:** `data/generate_fab_db.py` (615 lines, `random.seed(42)`).
**Verification status:** generator re-run in an isolated environment; output byte-identical to the shipped dump after line-ending normalization (SHA-256 match). Every mechanism below cites the line where it lives. This is the most consequential file in the repository: everything the analytics "finds" was decided here.

---

## 1. How the data is actually generated (mechanics)

1. **Dimensions are literals** (lines 198–271): 6 products, 15 tools, 12 steps, 12 operators. `BAD_ETCH_ID = 4` (**ETCH-02, line 234**) is the single fault switch for the whole dataset. ETCH-02 is also given the oldest etcher install date (2017) — a nice touch of circumstantial realism.
2. **Lots/wafers** (276–318): 12 lots at ~14-day cadence starting 2025-08-03, statuses drawn from a shuffled list (9 COMPLETED, 2 IN_PROGRESS, 1 SCRAPPED); 25 wafers each. Per wafer, one draw decides everything: `etch_tool = random.choice([3, 4, 4, 5])` (line 308) — **50% routed to the bad tool** (verified: 154/74/72).
3. **Run history** (337–367): each wafer walks the 12-step route sequentially with uniform random durations and gaps. `measured_value = gauss(target, tol/2.5)` (line 353) — *independent of tool, chamber, time, and the fault flag*. The same `etch_tool` is used at **both** etch steps (line 347: steps 4 and 11).
4. **Inspections/defects** (371–443): each wafer gets one *signature* (EDGE_RING-weighted if bad tool: weights `[10,45,10,20,15]` vs `[18,12,12,28,30]`, lines 377–379); per inspection, defect count `n ~ gauss(base, 0.35·base)` where `base = 6 + 8·bad + 3·edge_slot` (line 407); 75% of defects take the wafer signature type; coordinates are generated *from the type* (EDGE_RING → annulus 138–149 mm; CENTER → |N(0,22)|; PARTICLE/RANDOM → area-uniform; SCRATCH → a line).
5. **Yield** (453–485), the load-bearing line (467):
   ```
   factor = target − 0.0016·defect_count − 0.08·bad_tool − 0.03·edge_slot + N(0, 0.03)
   ```
   Fail bins are then formulaic fractions of the miss (edge 35%/12%, defect 45%, param = remainder).
6. **Maintenance** (490–525): monthly PM for every tool; unscheduled events: **4–7 events × 3–14 h if bad tool, 0–2 × 1–6 h otherwise** — timestamps uniform over Aug 2025–Jan 2026, *uncorrelated with production, defects, or anything else*.

**The causal graph that actually exists:**

```
              ┌──────────────────────────────┐
              │  bad_tool flag (ETCH-02)     │        edge_slot (1,2,24,25)
              └──┬───────────┬───────────┬───┘             │
                 ▼           ▼           ▼                 ▼
        defect count &   yield −8pts   unscheduled    defects +3, yield −3pts
        EDGE_RING type   (DIRECT)      maintenance
                 │                     (count/duration only,
                 ▼                      random times)
        yield −0.16pts/defect
```

Everything else — operators, shifts, chambers, recipes(absent), parameters, killer flags, fail bins — is either noise or cosmetic.

---

## 2. Classification of every planted relationship

Classes: **A** realistic engineering relationship · **B** statistically useful synthetic relationship · **C** overly deterministic · **D** leakage risk · **E** unrealistic shortcut.

| # | Relationship (line) | Class | Assessment |
|---|---|---|---|
| 1 | Bad tool → EDGE_RING-weighted defect signature (377–379) | **A/B** | The *kind* of relationship is real (chamber edge non-uniformity → edge-ring defects). Weight ratio (45% vs ~12%) is strong but not absurd |
| 2 | EDGE_RING type → coordinates in a 138–149 mm annulus (385) | **A**, with a caveat | Geometry is realistic and enables the project's best analytics. Caveat: type and geometry are the *same variable* — the "confirmation" that edge-ring defects sit at the edge (investigation step 4) verifies the generator's copy function, not an independent fact |
| 3 | Bad tool → +8 defects/inspection base rate (407) | **B** | Reasonable mechanism, reasonable size (verified: 40.3 vs 17.4 defects/wafer over ~3 inspections) |
| 4 | Defect count → yield, −0.16 pts/defect (467) | **B**, borderline A | The right *shape* (kill-rate-like linear model); produces the verified r = −0.550. A Poisson/killer-defect model would be class A |
| 5 | **Bad tool → yield −8 pts directly (467)** | **C + D** | The core leakage. The tool label reduces yield *bypassing every physical mechanism*. Verified decomposition: of ETCH-02's ~12-pt deficit, **8.0 pts are this direct label effect, only ~3.7 pts flow through defects**. Consequence: yield-vs-tool GROUP BY is guaranteed to "find" the tool, and the three "independent" signals are three readouts of one boolean |
| 6 | Bad tool → 4–7 unscheduled events at 3–14 h vs 0–2 at 1–6 h (509–514) | **B/C** | Usefully noisy in count, but this seed drew 0 events for both good etchers, making the README's "carries **all** unscheduled etch downtime" a fragile artifact of seed=42. Event *times* are uncorrelated with production — no temporal evidence possible |
| 7 | Edge slots (1,2,24,25) → +3 defects, −3 yield pts (406–407, 467) | **B** | A believable second-order effect; currently unused by any analysis — good material for a future "distractor" signal |
| 8 | 50% routing to the bad tool (308) | **E** | Real fabs balance load; a tool carrying half of gate etch while visibly marginal would be caught in days. This also compresses lot-exposure spread (verified 28–64%), making the containment ranking weakly discriminating, and it *manufactures* the "uniform ~9-pt miss across all products" symptom |
| 9 | Same tool at gate etch and metal etch per wafer (347) | **E + D** | 100% collinear exposure (verified: 0 wafers differ). The data cannot distinguish "gate etch fault" from "metal etch fault" — the investigation's claimed localization to *gate* etch is unidentifiable from the data |
| 10 | measured_value ~ N(target, tol/2.5) for all tools (353) | **E** (by omission) | The faulty tool has *no parameter signature* (verified: identical CD distributions). A real uniformity fault shows in CD/etch-rate metrology. Consequence: the process-parameter evidence channel is empty, and no SPC story is possible on this data |
| 11 | killer_flag = f(size, center position) (433) | **E** (cosmetic) | Killer defects have zero effect on yield (yield uses raw counts). Decorative column |
| 12 | Fail bins = fixed fractions of miss (473–476) | **E** (cosmetic) | `v_loss_decomposition` reads back the generator's constants, not a mechanism |
| 13 | Lot statuses shuffled independent of age (279–281) | **E** (minor) | Produces the verified anomaly that the two IN_PROGRESS lots are the oldest |
| 14 | Maintenance windows don't block production (490–525) | **E** | Verified: 34 runs overlap their tool's own downtime |

### Leakage / difficulty assessment (per the audit brief)

- **Target leakage: YES** — relationship #5 writes the answer into the target with an 8-point direct effect.
- **Feature leakage: YES (subtle)** — `defect_type` labels encode the wafer's signature; a classifier "predicting" edge-ring from coordinates would be learning the generator's inverse function (#2).
- **Artificially easy prediction: YES** — a single GROUP BY separates classes by ~12 points against σ≈3 noise; no confounding exists (routing is uniform-random over products, lots, time), so the naive estimator is unbiased by construction. Real commonality analysis is hard *because* of confounders; none are present.
- **Unrealistic class balance: YES** — 50% exposure to the fault (#8).
- **Unrealistic temporal behavior: YES** — the fault is eternal (no onset, no drift, no recovery); maintenance timestamps are decorative; "when did it begin?" is unanswerable in principle.
- **Unrealistic tool behavior: YES** — no chamber effects (verified flat), no degradation trajectory, production during downtime.
- **Unrealistic yield behavior: PARTIAL** — the defect→yield coupling is decent (#4); the direct label effect (#5) and formulaic bins (#12) are not.

**Overall verdict:** the dataset is *honestly labeled* as planted, is internally consistent (verified: defect counts, yield arithmetic, bin sums all reconcile exactly), and is deterministic — the engineering hygiene is real. But as an analytics substrate it is a **single-scenario answer key**: one fault, always on, unconfounded, over-exposed, with the conclusion additionally injected directly into the target. It can demonstrate *method narration*, and cannot demonstrate — or evaluate — *detection or attribution skill*.

---

## 3. Requirements for the future synthetic fab engine (design only — not implemented)

The successor's one-sentence spec: **generate scenarios the analysis code has never seen, from a config the analysis code cannot read.** That single property (answer-blindness) converts the project from a narration into a testable system, and it is what makes an evaluation harness (`EXPANSION_ROADMAP.md` Phase 1/7) possible.

### 3.1 Structural requirements
1. **Scenario configuration as data** (YAML/JSON): fault type(s), affected tool/chamber, onset time, severity, drift rate, plus a seed. The default demo scenario can remain "ETCH-02 edge-ring" for continuity — but it must be *one config among many*, and CI must also run randomized scenarios.
2. **Physics-mediated faults only.** A fault may influence yield **only through mechanisms**: fault → parameter shift → defect generation → die kill → yield. Delete every `−0.08 if bad` direct term. If the mediated path is too weak to detect, tune the mechanism, not a label coefficient.
3. **Fault library** (each with parameter signature + defect signature + spatial pattern): chamber edge non-uniformity (the current story), CVD particle shower, litho overlay drift, CMP scratch cluster, implant dose drift, metrology bias (a *false-positive* generator — invaluable for evaluation), and "no fault" (the null scenario every detector must survive).
4. **Variation decomposition, explicit and layered:** fab-wide week effects → product effects → lot-to-lot (slow AR(1)-style wander) → wafer-to-wafer → within-wafer (radial + slot). Tool-to-tool and chamber-to-chamber offsets in *parameters* (small, permanent, mostly benign — so that tool differences are not automatically faults).
5. **Temporal causality:** one event clock. Onset times for faults; degradation trajectories (e.g., particle rate rising between PMs); maintenance that (a) blocks production during the window, (b) usually *resets* degradation (recovery), (c) occasionally fails to fix or even worsens (realistic ambiguity); alarms emitted when internal states cross thresholds.
6. **Parameter streams that carry signal:** per-run measured parameters must respond to tool state (drift, post-PM shift, chamber offsets) with realistic noise, so SPC/FDC analytics have a real substrate. Sensor *summaries* (mean/σ/range per run) are enough; full traces are out of scope.
7. **Defect model:** count ~ Poisson(rate(tool state, step, wafer position)); classification as a *noisy channel* over true signature (misclassification 5–15%); killer probability by size/layer/position feeding a die-grid kill model → yield becomes an *output* of geometry, not a formula.
8. **Routing realism:** load-balanced with mild preferences (e.g., 40/35/25), occasional dedication windows; product↔tool correlations introduced deliberately as *confounders* the analysis must control for.
9. **Preserved virtues (non-negotiable):** determinism per seed; internal reconciliation invariants (counts, sums, foreign keys, clock ordering) asserted by generator self-tests; honest labeling.

### 3.2 What the current generator gets right (keep)
- Deterministic, self-contained, dependency-free (stdlib only) — keep this bar.
- Portable dual output (`.db` + `.sql` dump) — keep.
- Coordinate-level defect geometry with calibrated spatial classes — keep and deepen (it powers the project's showpiece analytics).
- The instinct to plant *converging multi-channel evidence* for one cause — keep; generalize from "one hard-wired cause" to "one *configured* cause per scenario."

### 3.3 Acceptance tests for the future engine (definition of done)
- Re-running with the same config+seed is byte-stable; changing only the seed changes realizations but not scenario semantics.
- A null-scenario dataset produces no detectable fault at the platform's default thresholds (false-positive control).
- For each library fault at "obvious" severity, the *intended* mediated signals are recoverable by reference queries; at "subtle" severity they are near the detection floor (so the benchmark has a difficulty axis).
- No column in any emitted table encodes the fault label directly or via a trivial proxy (leakage lint: the scenario config never leaves the generator).
