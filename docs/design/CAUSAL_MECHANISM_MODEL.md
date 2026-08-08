# Causal Mechanism Model — How Faults Become Data

**Status:** Phase 1 design for review. Implements ADR-004 (physics-mediated faults only).
**Replaces:** the audited direct chain `bad_tool → yield −8 pts` with mediated chains carrying independent noise at every stage. All default magnitudes below are world-template values (tunable), stated here so the design is concrete enough to implement and to review for plausibility.

---

## 1. Latent state: the hidden middle layer

Each chamber carries a small latent state vector, evolved on the event clock, **never emitted**:

| Latent | Meaning | Baseline dynamics | Reset by |
|---|---|---|---|
| `edge_uniformity` | radial process non-uniformity (0 = ideal) | ~0 with tiny wander | repair (partial) |
| `param_bias` | delivered-vs-set deviation of one delivery subsystem (e.g., MFC flow) | AR(1), φ≈0.98, σ small | PM (partial), repair |
| `particle_load` | chamber particle accumulation | linear-ish growth between cleans + noise | PM (full), repair |

Latents are per **chamber** (chambers become real, per the audit). Tools additionally carry a benign permanent offset vector drawn once per dataset (see §3). Mechanisms (faults) act by *adding trajectories to latents* — nothing else. Observables never read the scenario config; they read latent state.

## 2. Observation model for process data

For a run of recipe *r* on chamber *c* at time *t*, each FDC parameter *p*:

```
value(p) = setpoint(r, p)                       # recipe_settings
         + fab_week(t)                          # slow fab-wide wander, shared
         + tool_offset(tool, p)                 # benign, permanent, small
         + chamber_offset(c, p)                 # benign, permanent, small
         + latent_effect(c, t, p)               # sensitivity matrix × latents
         + lot_effect(lot)                      # AR(1) across lots
         + run_noise                            # iid
```

Metrology (e.g., post-etch CD center/edge/σ) follows the same stack referenced to `recipes.metric_target`, plus a within-wafer radial term driven by `edge_uniformity` (that is what makes `cd_nm_edge` and `cd_nm_sigma` respond to the demo fault while `cd_nm_center` barely moves), plus metrology-tool bias (small) and measurement noise.

**Default magnitude ladder (CD example, nm):** run noise σ≈0.45 · lot AR(1) σ≈0.25 · chamber offset σ≈0.20 · tool offset σ≈0.15 · fab-week σ≈0.15 · metrology noise σ≈0.15. Tool/chamber differences therefore exist *everywhere without being faults* — the audit's requirement that "tool differences are not automatically faults."

## 3. Benign variation is a feature, not noise dressing

Every dataset — including the null scenario — contains: permanent tool/chamber offsets, the edge-slot defect effect (+small), product-dependent baseline defectivity, background breakdowns on all tools, false alarms, and PM-cycle sawtooth in `particle_load`. These are the standing distractors: the world must contain honest structure that a naive analysis could wrongly accuse (design-gate scenario H pressure, present everywhere).

## 4. Defect generation

Per inspection of wafer *w* at step *s*:

1. **Count:** `n ~ Poisson(area × Λ)` with intensity `Λ = λ_base(s, product) + λ_particle(chamber upstream, t) + λ_mechanism(...)`. The audit's Gaussian count model is replaced; the product term makes baseline defectivity product-dependent (distractor).
2. **True origin:** each defect draws a *hidden* origin component from the active intensity mix: `background_uniform`, `edge_ring(c)`, `center`, `particle_cluster`, `scratch` (rare background). Origin is truth-side only.
3. **Geometry from origin** (not from label): uniform-over-area, edge annulus with severity-dependent width/density, center Gaussian, cluster around a seed point, line segment. Radial profiles get jitter so signatures overlap distributions rather than partitioning them.
4. **Size:** lognormal as today.
5. **Classifier channel:** `classified_type = confusion(true origin)` with a confusion matrix (default 88% correct, mass spread over plausible confusions). Spatial "confirmation" of a classified type can now genuinely fail — the audited circularity (type ⇒ coordinates ⇒ "confirmed") is gone.

## 5. Die-kill and yield

Per wafer at final test, over its die grid (from `die_size_mm2`, usable radius):

```
P(die dies) = 1 − (1−p_bg)(1−p_defect)(1−p_param)
```

- `p_bg`: background Poisson defectivity per die, product-calibrated so E[yield] ≈ `target_yield_pct` on a healthy fab (targets *emerge*; they are not written).
- `p_defect`: defects landing on/near a die kill it with probability by size and layer (kill radius small); clusters kill locally — this is what couples defect maps to die_bins maps spatially.
- `p_param`: parametric kill from process deviation, spatially resolved: a die's kill probability rises with the local |CD − target| implied by the wafer's radial CD profile — the edge-uniformity fault kills mostly edge die *through geometry*, not through an edge coefficient.
- Tester bin assignment: killed die get a bin code from a symptom distribution conditioned on kill cause (defect kills mostly OPEN_SHORT, parametric kills mostly PARAM/LEAK) **with 20% cross-assignment noise** — bins are evidence, not labels (replaces v1's formulaic bin fractions).
- `wafer_yield` = die grid summed. No term anywhere reads fault identity, tool identity, or slot. The audited `−0.08 if bad` and `−0.03 if edge` terms have no successor; slot/edge effects flow through defect intensity only.

Natural yield variation budget (healthy world): wafer-level σ ≈ 2.5–3.5 pts around product mean, lot-to-lot σ ≈ 1–1.5 pts — matching the scale the current demo established (σ≈3), so the demo remains statistically comparable.

## 6. Maintenance and recovery

- **PM:** scheduled; fully resets `particle_load`, partially recenters `param_bias` (draw ~N(0.7, 0.1) fraction), does not touch `edge_uniformity` (hardware, not cleaning).
- **Unscheduled repair:** triggered by alarm escalation (§7) or breakdown; targets the alarmed subsystem's latent; recovery fraction ~ Beta(8,2) (mean 0.8) under `recovery: partial`, 1.0 under `full`, 0 under `none`; 10% chance the repair fixes nothing (honest ambiguity). Residual offsets after partial recovery are what make "did the intervention work?" a real question with a nontrivial answer.
- Maintenance rows carry only action codes/templated text shared fab-wide — the *effect* is in the subsequent data, never in the description.

## 7. Alarms

Latent crossing its alarm threshold emits coded alarms with per-check probability (alarms are noisy, not guaranteed); plus a background false-alarm Poisson on every chamber (default ~1/chamber/month). Severity `subtle` is calibrated to stay *below* alarm thresholds — some faults must be findable only analytically.

## 8. Severity calibration (the difficulty axis)

Severity is defined in units of the natural variation of the **weekly aggregated statistic** the fault most directly moves (e.g., weekly mean `cd_nm_edge` per chamber; weekly defect rate per chamber):

| Severity | Aggregate shift | Intent |
|---|---|---|
| subtle | ≈ 1.5σ_weekly | near the detection floor; no alarms; benchmark headroom |
| moderate | ≈ 3σ_weekly | detectable with competent statistics; the demo level |
| obvious | ≈ 6σ_weekly | unmissable; sanity anchor |

Per-wafer effects are correspondingly much smaller than v1's 4σ single-GROUP-BY giveaway. Expected demo (moderate) outcome: affected-chamber cohort ≈ 4–8 pts yield deficit vs σ≈3 wafer noise, elevated edge-zone defect share (≈ 35% vs 22% baseline, overlapping distributions), visible `cd_nm_edge`/`cd_nm_sigma` shift — three convergent mediated channels, none individually deterministic. Calibration is verified by acceptance tests (reference-query recoverability at moderate/obvious; near-floor at subtle), so tuning is measured, not aesthetic.

## 9. Phase 1 mechanism library

| Mechanism | Latent target | Observable footprint (all mediated) | Used by scenario |
|---|---|---|---|
| `chamber_edge_uniformity` | `edge_uniformity` ↑ | edge CD deviation/spread; edge-ring defect intensity; edge die kills; pressure/endpoint FDC side-effects; alarms at moderate+ | B, G |
| `param_drift` | `param_bias` ramp | FDC flow summary trend; CD mean walk; late gentle parametric yield loss | C |
| `particle_excursion` | `particle_load` step+growth | particle defect counts up (clustered + uniform); PARTICLE_HI alarms; defect-kill yield dip; post-repair recovery | I |
| `benign_offset` | permanent offset only | small stable parameter/defectivity difference; **no** temporal change, no yield path beyond noise floor | distractor, all |

Each mechanism implements one interface: `contribute(latent, t)` plus `truth_record()` — adding a mechanism never touches emit code, keeping the library growable (D, E, F, H, J later) without architectural change.

## 10. As configured (Phase 1 Step 3.0 — `scenarios/worlds/baseline_fab_v1.json`)

Step 3.0 turned §1–§8 into world-template contracts. Nothing here is *implemented* — no latent evolves, no channel is computed, no alarm fires, no defect is classified — but every constant those slices will read now has a declared, validated home, and none of it can be keyed to an entity:

- **§1 latents** — `observation.latents`, a closed vocabulary (`edge_uniformity`, `param_bias`, `particle_load`). Sensitivity maps and latent-sourced alarm rules are validated against it, so a "sensitivity" keyed to a tool name is a rejection rather than a subtle bug.
- **§2 observation model** — `observation.channels`: each channel declares its kind (`fdc`, grounded in a recipe setting; `metrology`, grounded in a step metric), its qualifying operation types, its natural `scale`, and its latent → channel sensitivities. A channel that reports something the world does not declare is rejected: FDC deltas must have a reference.
- **§2–§3 variation stack** — `observation.variation_stack`, as dimensionless multiples of a channel's `scale`. The baseline reproduces the §2 CD ladder with run noise as the unit (run 1.0 · lot AR(1) 0.55 · chamber 0.45 · tool 0.33 · fab-week 0.33 · metrology 0.33), which is what makes tool and chamber differences exist everywhere without being faults.
- **§4.2/§4.5 defect origins and classifier** — `observation.classifier`: the observable `classes`, the hidden `origins`, and a confusion matrix whose rows must be distributions over declared classes. The template spells the uniform-background origin `uniform`; §4.2's `background_uniform` is the same thing (the shorter name keeps the world's vocabulary clear of the token the L1 scan reserves for ground truth).
- **§5 die grid** — `die_grid`: edge exclusion, street width, aspect ratio and the coordinate conventions, validated against every product's wafer and die size.
- **§7 alarms** — `alarms`: severity vocabulary, per-chamber background false-alarm rate, per-check detection probability, and generic threshold rules over declared channels and latents. A rule cannot name an event, a mechanism, a tool or a chamber; the fields do not exist.
- **§8 severity calibration** — `observation.severity_calibration`, in σ of the weekly aggregate (1.5 / 3.0 / 6.0), required to increase strictly so severity stays a detectability setting rather than a label.
