# Diagnosis Contract — what the RCA engine may see, and what it must produce

**Status:** binding contract, **implemented** in `src/fabops/diagnosis/` and
executed by `tests/fabops/`. It began as a design gate because the benchmark
gate found that the contract was not defined anywhere: ADR-003 states the *rule* (answer-blind analytics), ADR-005 states
that evaluation gates the claim, ADR-007 states that statistics come before
ML, and ADR-008 names an output artifact (`fabops.investigation/v1`) — but
nothing said what the engine is handed, what it returns, or how a conclusion
is scored. Implementing against that gap would have meant inventing
architecture. The gap is closed: ADR-029 settles the five open decisions and
records the three parameters the implementation itself falsified.

Read with `ANTI_LEAKAGE_DESIGN.md` (what may not reach it),
`GROUND_TRUTH_CONTRACT.md` §4 (who may read what) and
`SCHEMA_V2_DESIGN.md` (the only data it gets).

---

## 1. The three planes, and where diagnosis sits

```
    fabsim ──▶ observable dataset ──┬──▶ fabops / diagnosis ──▶ candidate RCA
           └─▶ truth.json ──────────┼──────────────────────────────┐
                                    │                              ▼
                                    └──────────────────────▶ fabeval ──▶ score
```

Diagnosis is a **fourth** actor, and it has the *narrowest* privilege of the
four: strictly less than `fabeval`, and exactly the same as `fabops`. It is
placed inside the fabops plane because that is what it is — the analytical
engine ADR-003 says must be answer-blind, and the successor to the
`DEMO_SUSPECT_TOOL` constant that is grandfathered only until it lands.

| Actor | observable | scenario config | truth | benchmark metadata |
|---|---|---|---|---|
| `fabsim` | writes | reads | writes | — |
| `fabops` / diagnosis | **reads** | never | never | never |
| `fabeval` | reads | may read | reads | writes |

## 2. The input: one database path, and nothing else

The engine's entry point takes **a path to `fab.db`** and returns a report. Not
a dataset directory, because `truth/` is a sibling of `fab.db` and a function
given the directory could reach it; not a `Dataset` object, because that holds
both planes. This mirrors the rule `GROUND_TRUTH_CONTRACT.md` §4 already
applies to `fabops.db`: reaching truth must require deliberate circumvention,
not an accident of signature.

```python
def diagnose(db_path: Path) -> Investigation: ...
```

Everything the engine knows therefore comes from the 22 tables of schema v2.
That includes the things a real engineer has and the audited v1 lacked: the
route, the recipes and their setpoints, chamber-grain exposure, FDC summaries,
zonal metrology, defect coordinates, tool states, alarms, maintenance, die
bins and wafer yield.

**Forbidden inputs, explicitly.** `truth.json`; any `scenarios/*.json`; the
scenario slug; a mechanism or event identifier; the planted target; the hidden
`Realization`, `DefectOrigin`, `DieOutcome`, `AlarmRealization` or
`LatentReset`; any latent value or counterfactual series; `severity` in any
form; `expected_impact`; the benchmark's own matrix or expectations. None of
these has a column in schema v2, so the prohibition is mostly structural
already — what the checks in §6 add is that it stays that way.

## 3. The output: an investigation, not a label

A conclusion that cannot be recomputed by hand from the emitted rows is not a
conclusion this project accepts (PROJECT_VISION's explainability guarantee,
ADR-007). The report is therefore a *ranked set of candidates each carrying
its own evidence*, not a suspect name.

```
Investigation
  schema                "fabops.investigation/v1"
  dataset_id            copied from dataset_meta; the only id it needs
  generated_by          engine name + version
  window                the period examined
  anchors[]             the shared change points every candidate was scored at
  candidates[]          ranked, best first
  considered[]          hypotheses examined and rejected, with why
  insufficient_evidence bool - a first-class answer, not a failure
  abstention            {p_familywise, alpha, permutations, reason}
```

Each candidate:

```
Candidate
  entity        {kind: tool|chamber|product|recipe|operator|step, id, name}
  status        assessed | not_assessable
  score         a number the engine defines and the report explains
  onset         {day, interval: [lo, hi], anchor} - null if not estimable
  evidence[]    {family, channel, statistic, value, comparison, support}
  confounders[] competing explanations considered and how they were controlled
  narrative     generated from the evidence, never free-form prose
```

Five requirements on the output, each answering a specific audited failure:

* **Every candidate must be falsifiable from the dataset.** An `evidence`
  entry names a family, a channel, a statistic and a comparison an analyst can
  re-run.
* **`considered[]` is mandatory and must be non-empty** when a candidate is
  offered. The audited v1 narrated one conclusion and checked nothing else;
  scenario G exists precisely to punish an engine that ranks the first
  correlation it finds.
* **`insufficient_evidence` is a correct answer.** On scenario A it is the
  *only* correct answer, and an engine that always names someone scores worse
  than one that abstains. It is decided by one family-wise statement per
  dataset (§5.1), not by a per-candidate threshold.
* **`not_assessable` is a state, not an omission** (ADR-029 §4). A hypothesis
  the observable plane cannot score — a product with too few time bins to
  carry a temporal contrast, a chamber whose exposure never reaches the
  support floor — must appear in `candidates[]` with `status:
  not_assessable` and the reason it could not be scored. Dropping it silently
  is what turns scenario G from a comparison into a walkover: the product
  hypothesis would be "rejected" by never having been asked. Measured on the
  library: **0 of 448** product-candidate channels carry ≥ 8 usable weekly
  bins at the shipped scale, so on scenario G today the product hypothesis is
  `not_assessable` and the report must say so.
* **Onset is a separate estimate, reported as an interval.** It is not the
  argmax of the evidence statistic — measured at 19–33% of the horizon and
  therefore unusable as a point estimate (ADR-029 §2) — and a candidate may
  legitimately carry more than one change point: scenario I's arc needs two,
  and a pulse fit beats a single change point at every horizon measured.

## 4. The ten questions the report must be able to answer

From the gate's own list, each mapped to the observable substrate that
answers it. This is the acceptance shape for the engine, not a feature list:

| # | Question | Substrate |
|---|---|---|
| 1 | What changed? | `run_measurements`, `metrology` vs `recipes` targets |
| 2 | Where? | `runs` → `chambers`/`tools`; defect coordinates |
| 3 | When? | run/measurement timestamps; change-point over the window |
| 4 | Which entity is implicated? | chamber-grain exposure via `runs` |
| 5 | What evidence supports it? | the `evidence[]` entries |
| 6 | What else could explain it? | `considered[]` |
| 7 | What confounders were controlled? | product mix, routing shifts in `runs` |
| 8 | Maintenance/recovery evidence? | `maintenance`, `tool_states`, before/after |
| 9 | How strong is the evidence? | effect size against a same-dataset control |
| 10 | Final conclusion and why? | ranked candidates + narrative |

## 5. Scoring — and why it is not in this document

How well a report does is `fabeval`'s question, and the metrics
(`FABSIM_DESIGN.md` §2: detection rate, attribution precision/recall,
false-positive rate on nulls, onset error) are the benchmark's. The engine
must not import `fabeval`, must not know the metric, and must not be tuned
against a specific scenario's answer. `fabeval` gains one adapter that reads
an `Investigation` and joins it to truth on `dataset_id`; that adapter is
evaluator code, not engine code.

**No benchmark number may be claimed yet, and that is a decision rather than
an omission (ADR-029 §5).** Nothing measured across four gates distinguishes
the candidate statistics at the size of the current library — the anchor
comparison's paired tests sit at p = 0.22–0.38, and the spread across random
anchors (8 to 14 of 25) is wider than the effect being estimated. Until the
scenario library reaches `EXPANSION_ROADMAP` Phase 6's ≥ 10 members with a
declared development/held-out split, `fabeval` reports diagnosis metrics as
*measured on a named population* and never as a capability claim.

### 5.1 The abstention decision, and the null it is read against

The engine makes **one family-wise statement per dataset**, not one per
candidate: *is any candidate more extreme than relabelling the candidates
produces?* The null is computed **inside the dataset being diagnosed** —
there is no external calibration artifact, and there is no path by which
`fabeval` could supply a threshold (ADR-029 §3).

```
for each evidence family f:            p_f(c) = min over that family's
                                       channels of c's rank among its
                                       contemporaneous peers, Sidak-corrected
                                       over the channels tried
candidate score                        F(c) = -2 * sum_f ln p_f(c)
null                                   permute the candidate label JOINTLY
                                       within a family, INDEPENDENTLY across
                                       families; recompute F
abstention                             p_familywise = P(max_c F_perm >= max_c F_obs)
                                       insufficient_evidence iff p > alpha
```

Permuting jointly within a family is load-bearing: the channels inside one
family read the same latents (metrology↔fdc r = 0.445 on fault-free worlds),
so an independent per-channel permutation destroys dependence that exists
under the null and reads anti-conservative. Measured on 65 fault-free worlds,
independent-per-channel gives .015 / .092 / .169 / .292 against a nominal
.01 / .05 / .10 / .20, while family-joint gives .000 / .062 / .092 / .231 —
valid within binomial noise at every level.

**The null-validity criterion.** The permutation null is valid only where the
candidates are exchangeable under the null hypothesis, which rules F10/F11
make true *by construction for this world* and which ADR-026 §3 measured to
three decimal places. That is an assumption about the world, not about the
fab in general, so it ships with a test rather than with a promise:

> On a population of at least 30 fault-free worlds, the realized rate of
> `p_familywise <= alpha` must not exceed `alpha` by more than binomial
> chance, at every declared level (0.01, 0.05, 0.10, 0.20).

A world variant, a recalibrated severity scale or a real fab would each need
that measurement re-run before the null could be trusted. The check is the
diagnosis counterpart of `l7_null_calibration`, and it is a test the
*simulator* can fail.

### 5.2 What the engine estimates, and what it cannot

Four gates conflated these; they are separated here and the separation is
binding.

| term | the object the engine estimates |
|---|---|
| evidence | a *residual* — an observation minus the fab's own reference for it |
| anchor | a shared change point: a nuisance parameter, never a conclusion |
| abstention | one family-wise statement per dataset (§5.1) |
| attribution | a *ranking* over entity hypotheses, with the alternatives it was compared against |
| onset | a separate interval estimate on the attributed candidate's own series |

**"Root cause" in this project means entity attribution, not mechanism
identification.** The mechanism lives only in the hidden plane by
construction: ADR-019 §4 makes `classified_type` a noisy draw over a hidden
origin, and ADR-021 §6 makes a bin a symptom drawn over a hidden cause. There
is no observable channel that identifies which mechanism acted, so an engine
that named one would be matching a catalogue — which §6.4 already forbids.
`fabeval` scores attribution and onset; it does not score mechanism naming,
and no later version may add that without first giving the observable plane a
channel that could carry it.

### 5.3 The anchor is shared, and that is structural

A candidate's evidence is evaluated at change points **it was not consulted
about**. Letting each candidate select its own change point is what the
measurement of ADR-029 §2 rejected: a benign candidate wins a per-candidate
maximization more often than a faulted one does, and the effect worsens as
the horizon grows. Only `fabops.diagnosis.anchors` may choose an anchor, it
is given the whole fab and no candidate identity, and §6.7 pins that
structurally.

*Which* anchor rule is deliberately not frozen (ADR-029 §2), but one thing
about it **is** now fixed and was learned during implementation: the anchor
must be **declared, not discovered**. A change point read out of the fab's own
aggregate is read out of a series the candidates *are* — the entity that moved
the fab created the anchor it is then scored at — and the permutation null,
which starts after the anchor exists, cannot reproduce that selection.
Measured on 200 fault-free worlds, a fab-chosen anchor made the engine fire at
0.200 against a nominal 0.05. The shipped anchor is a declared fraction of the
horizon and `fabops.diagnosis.anchors.select` is handed the horizon and
nothing else.

**One measured warning for whoever implements the scoring, corrected.** The
A9/A6 review gate reported that at `moderate` severity scenario B's planted
chamber reaches 2.65σ on edge-site CD against a "natural-variation floor" of
2.84σ, and concluded that an engine ranking chambers on one statistic "will
score at chance on the null". **The conclusion was wrong and the calibration
gate that followed measured why** (ADR-026): that floor is the worst of seven
chambers across three worlds, and comparing one specified chamber against a
maximum over twenty-one is not a comparison an engine has to lose. Against the
reference an engine would actually use — the null's own *per-chamber*
distribution, over 84 chamber-seeds — the planted chamber sits at:

```
                  edge_cd   edge_defect_share   alarms   yield_split
subtle  (1.61 s)   0.167          0.143          0.059      0.429
moderate(3.22 s)   0.060          0.095          0.018      0.405
obvious (4.00 s)   0.036          0.131          0.023      0.405
```

So a single channel *does* carry signal at `moderate` — p = 0.060 on edge CD,
p = 0.018 on alarms — and the ladder is monotone on both. Three things follow
for the engine, and they are the reason this correction matters more than the
number does:

* **Calibrate against the null's per-chamber distribution, never against its
  worst chamber.** The second is an order statistic that diverges with the
  number of null worlds you build; a threshold read off it is a threshold that
  measures your budget. ADR-027 built `fabeval.reference` for the evaluator's
  side of that. **The engine's side is settled differently and better:
  ADR-029 §3 replaces any external reference with a within-dataset
  family-joint candidate permutation** (§5.1), so the engine calibrates
  against the world it is judging and imports nothing. The engine must not
  import `fabeval` (§6.1); what it inherits is the *principle* — compare like
  with like — not the object.

  **One caution about the numbers in the table above, because they have been
  misread once.** They are the standing of *one specified chamber* on *one
  specified channel*, with truth naming the chamber. An engine faces ~19
  candidates × ~17 channels and must pay for that; the two figures are not
  comparable and the table is not a forecast of engine performance.
* **One channel is not enough even though one channel is not nothing.** At
  p ≈ 0.06 per channel, an engine that ranks seven chambers on edge CD alone
  will name the wrong one often. **Yield in particular is not an attribution
  channel in this scenario** and must not be weighted as though it were: it
  carries no severity information (p ≈ 0.41 at every rung), the planted
  chamber ranks 1st, 1st and 6th of 7 across the demo's three seeds, and on
  twelve fault-free worlds each etch tool is "worst on cohort yield" about a
  third of the time. ADR-028 removed it from A9's gate for exactly that
  reason. It remains a real downstream consequence and a real observable —
  an engine may legitimately *report* it as corroboration — but an engine
  that ranks candidates on it will rank noise.
* **The multi-channel, temporal conclusion stands and is better supported.**
  Rule F11 still puts benign offsets in the subtle band, a benign chamber and
  a subtle fault still differ "only by shape in time", and combining evidence
  across channels and across the window is still the actual problem to solve.
  That is why the benchmark was built first.

## 6. Anti-leakage checks the engine must pass

Static, and enforced the way the existing planes are:

1. **No import** of `fabsim`, `fabeval`, or anything under them.
2. **No path reference** to `truth`, `truth.json`, `scenarios/`, or a dataset
   directory; the engine receives `fab.db` and constructs no sibling paths.
3. **No entity literal.** No tool or chamber name in code — the
   `DEMO_SUSPECT_TOOL` constant is retired by the engine, not inherited.
4. **No mechanism vocabulary.** `chamber_edge_uniformity`, `param_drift`,
   `particle_excursion`, `benign_offset` and the latent names must not appear;
   an engine that knows the mechanism library is matching a catalogue.
5. **Entry-point shape.** `diagnose` takes a database path; no parameter named
   `truth`, `dataset`, `realization`, `scenario` or `expectation`.

Runtime, and the one that actually proves it:

6. **Truth invariance.** Rewrite every hidden record — mechanism names, defect
   origins, die kill causes, the scenario slug — leave `fab.db` byte-identical,
   and the `Investigation` must be **identical**. This is the diagnosis
   counterpart of the emitter's T5. The mirror is required too: a changed
   *observable* value must change the report, or invariance is passing because
   the engine is inert.
7. **The anchor is chosen without a candidate** (ADR-029 §2, contract §5.3).
   `anchors` is handed the whole fab's series and no candidate identity, and
   every candidate is scored at the anchors the module returned. Structural:
   the anchor function's parameter list has nowhere to put a candidate.
8. **Exactly one database is opened, and no sibling path is constructed.**
   `diagnose` receives a path and passes that same path to `sqlite3.connect`;
   nothing in the package calls `Path.parent`, `.parents`, `glob`, `open`, or
   `fabops.config`. This is the runtime form of rule 2 — reaching the hidden
   plane must require deliberate circumvention, not a two-character edit.

## 7. What is deliberately out of scope

No LLM, no RAG, no agent, no graph database, no ontology, no external service
(ADR-006, binding prohibition). No ML before the statistical baseline exists
and is benchmarked (ADR-007). The engine is deterministic: the same database
gives the same report, and a report carries the engine version that produced
it.

## 8. The five decisions, closed

These were recorded rather than guessed when this document was written.
**ADR-029 closes all five**; they are kept with their answers rather than
deleted, so the reasoning stays attached to the outcome.

1. **The score's definition.** *Closed.* Per candidate: the minimum rank-based
   p among a family's channels, Sidak-corrected over the channels that family
   offered, then Fisher across families (§5.1). Families are combined rather
   than channels because the measured cross-family dependence is small (max
   |r| = 0.156 outside metrology↔fdc) while the within-family dependence is
   not. The *statistic* each channel contributes is deliberately **not**
   frozen (ADR-029 §5) — the benchmark that would select it does not yet
   exist.
2. **The abstention threshold.** *Closed.* One family-wise statement per
   dataset, at `alpha`, read against the within-dataset family-joint
   permutation null (§5.1). Neither of ADR-027's two evaluator levels is
   used, because the engine no longer reads an external reference at all.
3. **Onset estimation.** *Closed.* Per candidate, reported as an interval,
   permitted to be null, and permitted to be more than one; estimated
   separately from the evidence statistic rather than as its argmax (§3).
4. **The artifact.** *Closed.* `fabops.investigation/v1`, ADR-008's existing
   name, inheriting that contract's versioning and its FabKG export boundary.
5. **Where the engine lives.** *Closed.* `src/fabops/diagnosis/`.

### 8.1 What remains open, and what it waits on

Three questions are open **by decision**, because the instrument that would
settle them is a benchmark the library cannot yet supply (ADR-029 §5):

* which anchor rule `anchors` should implement;
* which per-candidate statistic each channel should contribute;
* what `alpha` should be for a *fab* rather than for a benchmark.

All three wait on `EXPANSION_ROADMAP` Phase 6's ≥ 10-scenario library with a
declared development/held-out split. Until then the engine ships a declared
default for each, marked as such in code, and `fabeval` reports its numbers as
measurements on a named population rather than as a capability claim.

**The statistic registry, measured.** The default was chosen on **fault-free
calibration alone**, which is the only selection criterion available to an
engine that may not yet claim a benchmark number. On 200 fault-free worlds and
the 25 held-out fault datasets:

| statistic | null p ≤ .01 / .05 / .10 / .20 | mutation | held-out detected / rank-1 | verdict |
|---|---|---|---|---|
| `own_scale_step` *(default)* | .010 / .060 / .110 / .205 | fires | 1/25 / 6/25 | **valid at every level** |
| `standardized_step` | .010 / .075 / .125 / .240 | fires | 7/25 / 5/25 | ~1.5× hot |
| `trend_contrast` | not measured | not measured | not measured | registered, unmeasured |

Measured on 200 fault-free worlds against nominal .01/.05/.10/.20, with the
shipped family combination and the studentized maximum. "Mutation" is a chamber
shifted 30% on three evidence families: a statistic that cannot move on that is
inert whatever its calibration.

`standardized_step` finds more and states a level it does not keep. Its
denominator is pooled across a stratum, which is more precise per candidate
and lets a candidate whose exposure makes its series noisier be systematically
extreme on *every* channel at once — the exact shape convergence is built to
detect. An abstention is a claim, and a mis-calibrated claim is worse than a
weak one, so the calibrated statistic is the default and the powerful one is
kept beside it with its cost on the record.

**And the engine's power is weak: one held-out fault in twenty-five clears the
fab-wide bar, six of twenty-five put the planted entity first.** That is
reported here rather than buried, because the alternative to reporting it is
choosing a statistic that reads better and states a level it does not keep.
