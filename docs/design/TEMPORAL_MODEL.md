# Temporal Model — One Clock, Causal Order, Meaningful Onset

**Status:** Phase 1 design for review.
**Fixes:** the audited absences — "the fault is eternal," "maintenance timestamps are decorative," "34 runs overlap their tool's own downtime," "'when did it begin?' is unanswerable in principle."

---

## 1. The clock

One simulated timeline per dataset: `time_origin` (from the world template, e.g., 2026-03-02 00:00) plus `horizon_days` (default 84). Every timestamp in every table is derived from this clock during simulation. There is no post-hoc timestamp painting: an event's time is when the simulation produced it. Wall-clock time never enters generation (determinism, `FABSIM_DESIGN.md` §6).

Resolution: minutes. All durations are drawn from distributions in the world template.

## 2. How activity is generated

### 2.1 Lot release and wafer progression
- Lots are released at a staggered cadence (default: every ~4 days ± jitter, 20 lots over 12 weeks). Overlapping lots in the line are what give time series their resolution — v1's one-lot-per-fortnight cadence caused the audited weekly product-mix artifact.
- Each wafer walks its product's flow in step order. For each step: pick tool/chamber (§2.2), wait until the chamber is free and PRODUCTIVE, process for `duration ~ step-specific distribution`, then move on after a transport/queue delay. Wafer timelines are therefore interleaved and tool-load-dependent, not independent random walks.

### 2.2 Routing (the assignment mechanism, per the design gate §7)
For a wafer at a flow step:
1. **Candidates** = chambers of tools qualified for the step's operation type (from the world template's qualification map — a tool may serve several steps; a step may have several tools).
2. **Dedication filter**: if the scenario declares a dedication window (product → tool during [t1,t2]), restrict candidates accordingly. The restriction is *visible in the data* (routing shares shift observably).
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

## 7. As implemented (Phase 1 Step 2 — `src/fabsim/world.py`, `src/fabsim/timeline.py`)

The world and timeline slice is built; mechanisms, latent state, alarms and every observable measurement are later slices. Deltas a reviewer should know about, all of them deliberate:

- **Dedication windows live in the world template, not in the scenario.** §2.2 describes dedication as something "the scenario declares", but the implemented `fabsim.scenario/v1` contract has no dedication field, and that contract is closed. Dedication is therefore a **routing policy** (`routing.dedications` in the world template: product × operation type × day window), which is where the other routing constants already live. The baseline world declares none; scenario G will need either a world variant or a scenario-contract extension, and that choice belongs to the scenario slice.
- **PM is tool-scoped, breakdowns are chamber-scoped**, matching the nullable `maintenance.chamber_id` of `SCHEMA_V2_DESIGN.md` §2.18. A tool-wide PM blocks every chamber of the tool.
- **QUAL follows every PM and every unscheduled window** and blocks production like the window itself, so the state vocabulary has support everywhere without a mechanism having to mint it.
- **Runs are placed whole into gaps** between blocking windows rather than being interrupted by one: a wafer that cannot finish before the chamber goes down waits for it to come back. This is what makes invariant §6.2 structural rather than a post-hoc correction.
- **Ties among equally-available chambers are broken by a seeded draw, not by entity id.** In a lightly loaded fab nearly every candidate is free at once, so id order would hand almost all work to the lowest-numbered chamber and starve the rest — an exposure pattern dictated by template order rather than by availability.
- **Run durations never depend on the chamber that ran them** (they are drawn per wafer × flow step), so timing cannot become a chamber fingerprint.
- **The queue/transport delay between steps is the dominant cycle-time term**, which is what makes the ~4-day release cadence produce genuinely overlapping lots rather than one lot at a time.
- **The timeline never reads `events` or `distractors`.** At a fixed seed, a null scenario and a fault scenario over the same world produce the identical schedule; the mechanism layer changes what that schedule *means*, never where the wafers went.
