# Temporal Model — One Clock, Causal Order, Meaningful Onset

**Status:** Phase 1 design for review.
**Fixes:** the audited absences — "the fault is eternal," "maintenance timestamps are decorative," "34 runs overlap their tool's own downtime," "'when did it begin?' is unanswerable in principle."

---

## 1. The clock

One simulated timeline per dataset: `time_origin` (from the world template, e.g., 2026-03-02 00:00) plus `horizon_days` (default 84). Every timestamp in every table is derived from this clock during simulation. There is no post-hoc timestamp painting: an event's time is when the simulation produced it. Wall-clock time never enters generation (determinism, `FABSIM_DESIGN.md` §6).

Resolution: minutes. All durations are drawn from distributions in the world template.

The latent plane (Step 3A) is integrated on the **same** clock, sampled at a versioned 60-minute step (`fabsim.latent.LATENT_GRID_MINUTES`): grid point *i* is the hidden state during `[i·60, (i+1)·60)` minutes of this timeline, so a run and the latent state it saw are the same instant by construction. There is no second clock and no second horizon. Because every latent process is parameterized per grid step, the step is part of the dataset identity and moves only with a generator version bump (ADR-016).

## 2. How activity is generated

### 2.1 Lot release and wafer progression
- Lots are released at a staggered cadence (default: every ~4 days ± jitter, 20 lots over 12 weeks). Overlapping lots in the line are what give time series their resolution — v1's one-lot-per-fortnight cadence caused the audited weekly product-mix artifact.
- Each wafer walks its product's flow in step order. For each step: pick tool/chamber (§2.2), wait until the chamber is free and PRODUCTIVE, process for `duration ~ step-specific distribution`, then move on after a transport/queue delay. Wafer timelines are therefore interleaved and tool-load-dependent, not independent random walks.

### 2.2 Routing (the assignment mechanism, per the design gate §7)
For a wafer at a flow step:
1. **Candidates** = chambers of tools qualified for the step's operation type (from the world template's qualification map — a tool may serve several steps; a step may have several tools).
2. **Dedication preference** (ADR-015): if a dedication covers this (product, operation type, day) — from the scenario's `routing_conditions` or the world's standing policy — then with probability `share` *this decision* is restricted to the dedicated **tool**; otherwise the full candidate pool stands, that tool included. Dedication changes exposure probability, never eligibility, and it is never chamber-scoped. The shift is *visible in the data* (routing shares move observably).
3. **Stickiness**: with probability ~0.6 a lot reuses the chamber its previous wafer used at this step (realistic lot-tool clustering; creates mild, honest confounding).
4. Otherwise choose the **earliest-available** candidate chamber (load balancing that responds to downtime — a down tool naturally loses exposure, so downtime and exposure gaps correlate for a *reason*, not by coincidence).
- Gate etch and metal etch are **independent** assignments (fixes the audited 100% collinearity).

### 2.3 Equipment timeline
Per chamber, a contiguous state ribbon over the horizon (PRODUCTIVE/IDLE/DOWN/PM/QUAL) emitted to `tool_states`:
- **PM**: scheduled per tool at the template cadence (default ~30 days ± jitter, 2–6 h). During PM the chamber takes no runs.
- **Breakdowns**: background hazard per chamber (memoryless, small) → DOWN interval + `UNSCHEDULED` maintenance row. Present for *all* tools — the v1 pattern "unscheduled events ⇒ probably the bad tool" must not survive.
- **Fault-driven repairs**: mechanisms push latent state; threshold crossings emit alarms; an alarmed condition schedules an unscheduled maintenance after `repair_delay ~ Exp(mean from scenario response)` — modeling the human loop of noticing and reacting.
- All maintenance windows block production by construction (invariant §4.2 of `SCHEMA_V2_DESIGN.md`).

## 3. The fault lifecycle

Every fault event follows this template (phases may be degenerate depending on mechanism and severity):

```
 BASELINE          latents at equilibrium; only natural variation
    │  onset_day (+ profile: step | ramp(ramp_days) | intermittent)
    ▼
 DISTURBANCE       latent state departs baseline (hidden)
    ▼  immediate, per affected run
 PROCESS EFFECT    FDC summaries + metrology on affected runs shift/spread
    ▼  hours→days (next inspection of affected wafers)
 DEFECT EFFECT     inspection counts / spatial mix change on affected wafers
    ▼  days→weeks (final test of affected wafers)
 YIELD EFFECT      die-kill → wafer_yield / die_bins degrade for the cohort
    ▼  latent crosses alarm threshold (severity-dependent; may never happen)
 ALARMS            coded alarms on the affected chamber (+ background false alarms)
    ▼  repair_delay
 MAINTENANCE       unscheduled maintenance interval; production blocked
    ▼
 RECOVERY          latent reset toward baseline by recovery fraction
                   (none | partial | full; occasionally worsening later, deferred)
```

Two structural consequences, both intended:

1. **Lags are physical, not scripted.** The defect effect appears when affected wafers *reach their next inspection*; the yield effect when they *reach final test*. Cohort membership — not narrative timing — produces the propagation delays an investigator will measure.
2. **DETECTION is absent from the generator.** The fab's *reaction* (alarms, repair) is simulated; *analytical detection* belongs to fabops. The generator must not know what the platform will notice.

## 4. Timestamp relationships (what the data lets an investigator establish)

| Investigator question | Data that answers it |
|---|---|
| When did the abnormality begin? | first departure of FDC/metrology series for the affected chamber (vs truth's `onset_day`, giving the benchmark an onset-error metric) |
| What changed first? | process measurements move before defect rates, which move before yield — ordered by construction with realistic jitter |
| Did defects follow the process excursion? | per-wafer join: affected run time < inspection time; cohort defect rates rise after onset |
| Did yield degradation follow the defect increase? | cohort test_time ordering; unaffected cohorts flat |
| Did maintenance precede recovery? | maintenance interval < return of series toward baseline; residual gap if recovery is partial |
| Was production really stopped? | `tool_states` DOWN/PM intervals contain zero runs (invariant, not accident) |

## 5. Windows and exposure

Because routing is availability-driven and lots overlap, the affected-wafer set is a **time-bounded, partial cohort**: wafers through the faulty chamber during the fault window. Exposure fraction varies by lot (some lots miss the window entirely — unlike v1's uniform 50%), which restores discriminating power to lot-exposure/containment analysis (audited as "weakly discriminating" today).

## 6. Temporal invariants (asserted by selftest)

1. Per wafer: step order = time order; no overlapping runs.
2. No run overlaps DOWN/PM/QUAL of its chamber.
3. `inspection_time` and `metrology.meas_time` ≥ end of the step's run for that wafer; ≤ start of the wafer's next run.
4. `wafer_yield.test_time` ≥ last run end + test delay distribution.
5. Alarm times for mechanism-driven alarms lie within [onset, repair] of their event (background false alarms exempt).
6. Maintenance intervals coincide with DOWN/PM state intervals exactly.
7. Lot `finish_time` = last activity of its wafers (+ closeout delay); lot status consistent with progress at horizon end (fixes the audited oldest-lots-IN_PROGRESS artifact).

## 7. As implemented (Phase 1 Steps 2–3.0 — `src/fabsim/world.py`, `src/fabsim/routing.py`, `src/fabsim/timeline.py`)

The world and timeline slice is built, and Step 3.0 settled the routing contract; mechanisms, latent state, alarms and every observable measurement are later slices. Deltas a reviewer should know about, all of them deliberate:

- **Dedication is layered, and it is a share (ADR-015).** Step 2 put dedication in the world template and implemented it as a hard candidate restriction. Step 3.0 changed both halves. The world keeps the **standing policy** (`routing.dedications`); a scenario declares **time-bounded experimental conditions** (`routing_conditions`, `SCENARIO_SPECIFICATION.md` §2.1.1), because a window belongs to the experiment and not to the fab that every scenario shares. `fabsim.routing` composes the two, scenario first. And a dedication now carries `share ∈ (0, 1)`: it raises the probability that covered traffic lands on the dedicated tool, and never removes another qualified tool from the pool. The hard filter had to go because it made product and chamber exposure the same variable inside the window, leaving scenario G's within-product comparison nothing to compare. The dedication draw is taken only where a dedication is actually in force and has a candidate, so a world that declares none — every world in the library today — routes exactly as it did before the rule existed. The baseline world declares no standing dedication.
- **PM is tool-scoped, breakdowns are chamber-scoped**, matching the nullable `maintenance.chamber_id` of `SCHEMA_V2_DESIGN.md` §2.18. A tool-wide PM blocks every chamber of the tool.
- **QUAL follows every PM and every unscheduled window** and blocks production like the window itself, so the state vocabulary has support everywhere without a mechanism having to mint it.
- **Runs are placed whole into gaps** between blocking windows rather than being interrupted by one: a wafer that cannot finish before the chamber goes down waits for it to come back. This is what makes invariant §6.2 structural rather than a post-hoc correction.
- **Ties among equally-available chambers are broken by a seeded draw, not by entity id.** In a lightly loaded fab nearly every candidate is free at once, so id order would hand almost all work to the lowest-numbered chamber and starve the rest — an exposure pattern dictated by template order rather than by availability.
- **Run durations never depend on the chamber that ran them** (they are drawn per wafer × flow step), so timing cannot become a chamber fingerprint.
- **The queue/transport delay between steps is the dominant cycle-time term**, which is what makes the ~4-day release cadence produce genuinely overlapping lots rather than one lot at a time.
- **The timeline never reads `events` or `distractors`.** At a fixed seed, a null scenario and a fault scenario over the same world produce the identical schedule; the mechanism layer changes what that schedule *means*, never where the wafers went. `routing_conditions` is the one scenario field routing does read, and reading it is the point: a dedication window is declared policy whose effect is plainly visible in `runs`, so two scenarios differing in their conditions differ in their schedules because the fab was run differently — not because anything leaked. This still holds after Step 3A: `fabsim.latent` is the first slice to read `events`, and it reads them *downstream* of the schedule, so the fault-blindness of the timeline is unchanged.

- **Maintenance reaches latent state — every kind of it (Step 3B).** Step 3A implemented §3's PM effects (`CAUSAL_MECHANISM_MODEL.md` §6): a PM cleans `particle_load` completely, recentres `param_bias` by a drawn fraction, and leaves `edge_uniformity` and the benign offsets alone. Step 3B adds the fault-driven repairs of §2.3 — threshold crossings emit alarms, *N* alarms inside a window escalate into a work order, the order becomes an `UNSCHEDULED` window after a delay ~ Exp(mean) — and discharges the requirement that came with them: **background breakdowns move latent state through the same machine**, one quality draw ~ Beta(8, 2) with a 10% no-fix chance, spread by each latent's `repair_efficacy`. Nothing in the recovery path can tell a breakdown from a requested repair, so "a repair that changed behaviour" is not a fault fingerprint (ADR-017).

- **Response maintenance blocks production, so the *pipeline's* schedule is no longer fault-blind — deliberately.** `simulate()` accepts a complete maintenance calendar; the response layer walks the clock, places its repairs in each chamber's free time, and the unchanged scheduler then lays production out around the whole calendar. Routing therefore sees a repaired chamber as unavailable and §6.2 holds over response maintenance too. The Step 2 property "a null and a faulted scenario schedule identically" now describes `simulate_scenario` — the timeline slice, still blind to `events` and still tested that way — rather than the full pipeline. That is the honest position: a chamber taken out of service loses exposure, and background breakdowns already do that to every chamber in the fab.

- **What a window does when it ends is decided by the kind of work, not by its cause (ADR-020).** The bullet above puts every maintenance window through one recovery machine; this says what that machine reaches. A **PM** is service work — a clean or a calibration — and corrects a fraction of the chamber's whole current departure. An **`UNSCHEDULED`** window is a repair, and corrects a fraction of the *persistent* departure only, leaving the mean-reverting wander to revert on its own. §4's "did maintenance precede recovery?" therefore has a sharper answer than before: after a repair the series returns toward baseline by the realized fraction of what was actually standing, and on a healthy chamber whose only departure was ordinary wander it does not move at all — which is the shape the null world should have had all along. Both unscheduled kinds still go through the same call with the same distribution and are told nothing about a cause.
