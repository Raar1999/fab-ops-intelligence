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
- **What the fraction is a fraction *of* (ADR-020).** The two bullets above are two different physical acts, and they reach different parts of a latent. A **PM** cleans or calibrates: it reads what the chamber is doing now and corrects a fraction of *all* of it, wander included — trimming against ordinary variation is over-control, it is what a real calibration does, and it happens to every chamber on the same cadence. An **unscheduled repair** restores: it removes a fraction of the **persistent** departure — the mechanism's contribution plus any standing correction maintenance has already booked — and leaves the mean-reverting wander alone, because a fluctuation that reverts by itself is not something a technician can or does re-zero. An accumulating load has no self-correcting part, so both acts take a share of the whole load. Booking a permanent credit against a transient is what left repaired chambers several σ from their own baseline in a null world; ADR-020 records the measurement and the correction.

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

> **The "≈ 4–8 pts" in the paragraph above is RETIRED as a binding expectation (ADR-027 §6) and left here as a historical reference.** It is the same number as `PHASE_1_ACCEPTANCE.md` A9's "4–10 pts" and `SCENARIO_SPECIFICATION.md` §4 B's "≈ 4–8 pts", and all three trace to `docs/audit/SYNTHETIC_DATA_AUDIT.md` #5, whose decomposition is decisive: of the audited ETCH-02's ~12-point deficit, 8.0 points were the direct `−0.08 if bad_tool` label effect and **only ~3.7 points flowed through defects** — below the band's own floor. So it was never reachable through mediation *in the system it was measured from*, and requiring it is requiring back the term ADR-004 abolishes. The channel that would have to carry it in FabSim disposes of **0.80 yield points in total on a healthy fab** (ADR-026 §7). The number stays rather than being deleted so that retiring it cannot become erasing the record; `fabeval.acceptance.LEGACY_COHORT_BAND` keeps it on the code side and reports it without gating on it. Nothing was tuned toward it, and ADR-004's prohibition means nothing may be.
>
> **ADR-028 goes one step further and retires the yield *attribution* too, for the flagship demo gate.** Not because the effect is small but because the criterion has no discriminating power: on twelve fault-free worlds the worst etch tool on cohort yield is ETCH-01 four times, ETCH-02 four times and ETCH-03 four times, so "the affected tool is worst on yield" is satisfied by chance one time in three with no fault present. Chamber grain does not rescue it (the planted chamber ranks 1st, 1st and **6th of 7** across three seeds) and the standing does not move with severity (p ≈ 0.34 at every rung). **Yield remains a physically justified downstream consequence and stays in the simulator, the schema and the queries** — what it is not, in *this* scenario at *this* scale, is an attribution channel. A later scenario built around a defect- or parametric-dominated mechanism may make it one.
>
> *The rest of §8 — the σ ladder itself — is separately measured and holds; see ADR-026 §5 and ADR-027 §5.*

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

## 11. As implemented (Phase 1 Step 3A — `src/fabsim/latent.py`, `src/fabsim/mechanisms/`)

The latent plane of §1 and the mechanism library of §9 are built. Observations (§2), defects (§4), die kill (§5) and alarms (§7) are later slices and are **not** implemented; §6 is implemented for PM only. Deltas a reviewer should know about, all deliberate and all recorded in ADR-016:

- **The grid.** Latents are integrated hourly on the project's one clock (`LATENT_GRID_MINUTES = 60`). Every process constant is stated *per grid step* — the §1 "AR(1), φ≈0.98" is a per-hour φ, a ~2-day correlation time — so the grid is part of the dataset identity and moves only with a `fabsim` version bump.
- **Latent dynamics are world-template data.** §1's table is now a required `latents` block: `family` (`ar1` | `accumulation`), its parameters, `pm_recovery`, the benign-offset spreads, and `severity_reference`. Numbers live in the template, physics lives in `mechanisms/`.
- **`edge_uniformity` is signed.** §1 says "0 = ideal"; the implementation lets a healthy chamber wander either way around zero and has the activation push one way, which gives the later radial model a direction rather than a magnitude with an implied sign.
- **A mechanism returns a drive series, not `contribute(latent, t)`.** It is handed no latent state and no entity — a deliberate tightening. A mechanism that cannot read the state it drives cannot react to another mechanism, cannot see the benign offset it shares a chamber with, and cannot behave differently in a null world. For the `accumulation` family the drive is an added *growth rate*, which is what makes §9's "step + growth" a rate and not a teleport.
- **Realized effect is a counterfactual, not an assertion.** Every trajectory is integrated twice on identical draws, with and without the mechanism drives. The difference is the realized effect; before an onset the two series are bit-identical.
- **Benign offsets are baseline (F11).** Every tool and every chamber carries a permanent offset on every latent, drawn from the shared Gaussian family — signed for a wander latent, folded non-negative for an accumulation one, since residual contamination cannot be negative. A declared `benign_offset` distractor *widens* that offset; it never creates one, and the largest realized offsets sit within the subtle-severity band, so an offset and a subtle fault are not separable by size — only by shape in time.
- **Severity in practice.** For the `ar1` latents the realized weekly shift lands on the §8 ladder within about ±10% (`param_bias` a little low, because a PM recentres it between onset and measurement — §6 behaviour, not a miss). For `particle_load` an unattended excursion **exceeds** its nominal shift by roughly 3×, because a load climbs until something cleans it; severity there sets the escalation rate and `realized_shift_sigma` records the outcome.
- **PM only.** §6's PM semantics are implemented exactly (full clean of `particle_load`, `N(0.7, 0.1)` recentre of `param_bias`, `edge_uniformity` untouched, offsets never reset). §6's *unscheduled repair* is 3B — and 3B must recover latents on **background** breakdowns too, or "a repair that changed behaviour" becomes a fault fingerprint. *(Discharged in Step 3B; see §12.)*

## 12. As implemented (Phase 1 Step 3B — `src/fabsim/response.py`)

§6's unscheduled repair and §7's alarms are built. Observations (§2), defects (§4) and die kill (§5) remain later slices. Recorded in ADR-017:

- **§7 alarms.** A rule watches a signal and compares it against limits *that chamber set for itself* during a qualification window — an individuals chart, which is what a real FDC system keeps. The centre absorbs the permanent benign offset (§3) so an offset cannot become a permanent alarm; the spread is frozen after qualification so a slow ramp cannot widen the limits meant to catch it. A crossing is a *chance* to be noticed: `detection_probability` decides, so "the latent crossed" and "an alarm exists" are never the same statement. Every chamber also carries the background false-alarm rate, so a null world alarms on most of its chambers.
- **The chart matches the process, not the fault.** A stationary latent gets a fixed centre, so a sustained departure stays visible; an accumulating one gets a centre that climbs with the sawtooth, or every chamber would alarm at the top of every PM cycle. The choice comes from the latent's declared `family`.
- **§6 unscheduled repair, and the symmetry that matters.** Alarms escalate — *N* on a chamber inside a window, counted without regard to what raised them — into a work order, which becomes an `UNSCHEDULED` window after a delay ~ Exp(mean). Background breakdowns and requested repairs then go through **one** recovery machine: one quality draw ~ Beta(8, 2) with a 10% no-fix chance, spread across latents by each latent's `repair_efficacy`. A repair reaches the hardware a PM cannot touch. Nothing in the recovery path can tell the two kinds apart.
- **Severity, in practice.** Alarm response is monotone in severity for all three mechanisms, and a `subtle` fault stays at the null floor — §8's "some faults must be findable only analytically", now a measured property. The rates are calibrated against the **null world** and against nothing downstream.
- **Interim signal.** A `channel`-source rule is evaluated against a latent-space proxy — the channel's own declared sensitivities applied to the chamber's latents — until 3C provides real process observations. No FDC value is invented and no observable row is written.

## 13. As implemented (Phase 1 Step 3C — `src/fabsim/observation.py`)

§2's observation model is built. Defects (§4) and die kill (§5) remain later slices. Recorded in ADR-018:

- **The stack, term for term.** `setpoint + fab_week + tool_offset + chamber_offset + latent_effect + lot_effect + run_noise`, every term in units of the channel's declared `scale`, every one from its own named substream. The fab-week term is shared across chambers, which is what lets "the whole fab moved" be told apart from "this chamber moved"; the lot term is an AR(1) across lots; the tool and chamber offsets are permanent and are never reset by maintenance. This is the *second* benign layer — the latent plane already gives each chamber a permanent offset on each latent (F11), and it reaches the channels through the sensitivity matrix.
- **Units meet through the severity reference.** A `sensitivity` is read as channel scales per latent severity-σ, so §8's ladder means the same thing on both sides of the boundary. Without the normalization the raw product of latent magnitude and channel scale sits four orders of magnitude below the run noise.
- **Which channels a run reports is the recipe's business.** A channel appears for a run because the recipe gave it a setpoint, so an FDC delta always has a reference (`SCHEMA_V2_DESIGN.md` §2.14).
- **§2's radial term.** Each latent declares `radial_weight`; a zone's radial position is its index in `observation.wafer_zones` normalized onto [0, 1]. A purely radial latent leaves `cd_nm_center` **exactly** untouched, moves `cd_nm_mid` by half and `cd_nm_edge` by the full amount — measured, not asserted. A uniform latent moves every zone identically and no wafer's spread at all.
- **Metrology attribution.** Referenced to the recipe's `metric_target`, which a fault never moves; driven by the **measured run's chamber at the measured run's time**, resolved through the world's `measures` relation; the metrology tool contributes only its own permanent bias and its own noise.
- **Calibrated, not amplified.** One latent σ moves a channel's weekly aggregate by ≈0.6 of that channel's own weekly σ — within a factor of ~1.6 of parity, so the declared sensitivities were left alone. A moderate fault moves a single wafer by well under one run-noise σ and the per-run distributions of affected and healthy chambers overlap heavily. That is §8's difficulty axis, and amplifying it would be tuning for detectability.
- **A signed latent has a signed consequence.** Because `edge_uniformity` is signed, a chamber whose benign radial offset opposes a uniformity fault sees its within-wafer *spread* narrow while the fault cancels that offset. The edge-versus-centre contrast still moves in the fault's direction everywhere; only the spread's direction is chamber-dependent, so `cd_nm_sigma` is corroborating evidence rather than a guaranteed signature.

## 14. As implemented (Phase 1 Step 3D — `src/fabsim/defects.py`)

§4 is built. Die kill and yield (§5) remain a later slice. Recorded in ADR-019:

- **§4.1 count.** `Poisson(Λ × scan_area)` with `Λ = Σ_origin (base_rate × product defect_scale + Σ_latent sensitivity × magnitude)`. The Gaussian count model is gone; the product term is a new required per-product `defect_scale`, which is §3's standing distractor made explicit. Every origin carries a positive base rate, so the null world produces the whole vocabulary: 22 defects per wafer on the baseline, no spotless wafers, all five origins present.
- **§4.2 origin.** The mixture's components *are* the classifier's declared origins, and the loader rejects any mismatch in either direction. An inspection accumulates propensity from every covered step's run, so "which of the five chambers this layer touched did it?" is a question with evidence rather than a lookup. The origin is hidden and lives in its own record.
- **§4.3 geometry from origin.** Uniform over the disc *by area* (verified against the area law to within 0.02 in every radial fifth), an annulus with radial jitter that reaches inward so the ring overlaps the background, a centre Gaussian, clumps around a shared seed, and a stroke along a seed direction (verified anisotropic: median minor/major axis ratio < 0.2). Wafer radius comes from the product; nothing lands off-wafer.
- **§4.4 size.** Lognormal, then thinned by each inspection step's own `sensitivity_threshold_um` — a scanner reports only what it can see.
- **§4.5 classifier.** A draw through the confusion row, measured at 0.880 agreement for `particle_cluster` against a declared 0.88. Every class arises from more than one origin, and origin and class disagree on more than 40% of defects. The audited type ⇒ coordinates ⇒ "confirmed" circularity has no successor: geometry comes from the hidden origin, and the label is a noisy read of it.
- **A signed latent contributes its magnitude.** `abs()` for the `ar1` family, `max(0, ·)` for `accumulation` — an intensity may not go negative, and either direction of non-uniformity leaves a ring.
- **What 3D guarantees, and what it does not.** It guarantees *directional* mediation: where the magnitude a component reads rises, its defects rise; where it falls, they fall — verified in both directions at three chambers against the mechanism-free counterfactual. It does **not** guarantee that a configured fault raises defectivity, because that depends on the latent magnitude actually rising, and ADR-019 §5 records an upstream recovery defect that can prevent it. That is reported rather than tuned around.

## 15. As corrected (Phase 1 gate — `src/fabsim/latent.py`, ADR-020)

Not a new plane: a correction to §6 that §14's last bullet reported. 3E remains unstarted.

- **The defect that was found.** Recovery booked a permanent credit against `drive + state + carry` for every kind of maintenance alike, and `state` is the mean-reverting AR(1) term. On the null baseline world over 12 seeds, repaired chambers ended the horizon at rms 1.67σ from their own benign offset against 1.06σ for unrepaired ones, reaching 4.6σ — so a background repair on a healthy chamber could look like a process excursion once §4 read `|edge_uniformity|` as a magnitude.
- **The correction.** A repair takes the *persistent* departure (`drive + credit`); a PM takes the whole departure (`drive + credit + state`). Repaired null chambers now sit at rms 1.05σ against 1.06σ for unrepaired ones — statistically the same chamber — and on `edge_uniformity`, which no PM touches, a null repair is a bit-identical no-op.
- **What did not change.** PM semantics, the Beta(8, 2) quality model and its 10% no-fix, per-latent `repair_efficacy`, the accumulation family's clean-and-climb behaviour, the RNG derivation, and every declared constant in the world template. The realized *departure* recursion is algebraically identical, so §8's severity ladder and every `realized_shift_sigma` are untouched.
- **What did change, and by how much.** Every realization moves, because latent values feed alarms which feed repairs. Measured on the baseline over three seeds: the null defect population went from 21.7 to 21.4 defects per wafer with the origin mix intact (`edge_ring` share 0.146 → 0.134, the artificial part of it removed); the mean `|edge_uniformity|` of repaired null chambers no longer climbs over the horizon (1.62σ early against 1.74σ over the whole horizon before, flat at 1.57σ now, while unrepaired chambers were flat in both); and scenario B's edge-ring lift on the affected chamber went from ×1.55 / ×1.67 / ×1.72 to ×1.53 / ×1.85 / ×1.84 across subtle / moderate / obvious, above 1.0 in all nine runs and with the gap between `subtle` and the rest of the ladder now open rather than flat. **No sensitivity, scale or reference was retuned to obtain that** — it is what removing the artifact left behind.

## 16. As implemented (Phase 1 Step 3E — `src/fabsim/die.py`)

§5 is built. Every physical plane of Phase 1 now exists; the observable emitters, the truth artifact and the benchmark remain later gates. Recorded in ADR-021:

- **§5 die grid.** A real lattice on a real disc, from the product's own `wafer_size_mm` and `die_size_mm2` and the world's `die_grid` policy: pitch is the die footprint plus one street, the lattice is centred on the wafer, rows are numbered downward from the top, and **edge exclusion is applied to the die footprint rather than to its centre** — all four corners must clear the usable radius. No seed reaches the layout, so a die position means the same physical place in every dataset. Realized on the baseline: 633 die (Sensor-90, 96 mm²) to 3,504 (IoT-65, 18 mm²), with 120–244 partial die excluded per product.
- **§5 kill model.** `P(dead) = 1 − (1−p_bg)(1−p_defect)(1−p_param)` with **one** Bernoulli per die, so two risks reaching one die is one dead die. `p_bg` is the classical `1 − exp(−D₀·A)` over the die's own area, `D₀` a new required per-product `killer_density_per_mm2`; `p_defect` comes from the reported defects whose own radius plus a declared halo overlaps the die, rising with the defect's cross-section and weighted by its declared layer; `p_param` reads the wafer's own metrology, interpolated to the die's radius, against a functional limit stated in multiples of the step's control tolerance. Realized cause mix on the null: ≈87.5% pass, 11.5% background, 0.6% defect, 0.5% parametric.
- **§5 bins.** Drawn through a declared symptom row conditioned on the cause, with the cross-assignment noise §5 asks for. Every cause reaches more than one bin and every bin arises from more than one cause; the cause itself lives in a hidden `DieOutcome` record and the observable `DieBin` has no field for it.
- **§2's variation budget, and what it took.** Wafer-level σ 1.1–4.4 points and lot-to-lot σ 0–2.9 points against the declared 2.5–3.5 and 1–1.5. Reaching it required a new `die_kill.background` block: each lot and each wafer draws its own killer density about the product mean, because a fab's background defectivity is not a constant and without it the only wafer-to-wafer variation is binomial noise under a point. The spread does not reach 2.5 points on every product — the products with a large `metric_scale` see almost no parametric fallout, since the observation model's noise is absolute while their tolerances are wide — and that is reported rather than tuned away.
- **§8, at the far end of the chain.** An edge-uniformity activation raises `cd_nm_edge`, which raises the parametric risk of the die at large radius on exactly the wafers that chamber processed — verified by counterfactual subtraction and monotone across `subtle`/`moderate`/`obvious`. The magnitude is small (outer-fifth parametric risk 0.0065 → 0.0115 from null to `obvious`, cohort yield deficit under a point), for two identified reasons that ADR-021 records. Nothing was amplified to change it.
