# Diagnosis Contract — what the RCA engine may see, and what it must produce

**Status:** design gate. **No diagnosis engine is implemented.** This document
exists because the benchmark gate found that the contract was not defined
anywhere: ADR-003 states the *rule* (answer-blind analytics), ADR-005 states
that evaluation gates the claim, ADR-007 states that statistics come before
ML, and ADR-008 names an output artifact (`fabops.investigation/v1`) — but
nothing said what the engine is handed, what it returns, or how a conclusion
is scored. Implementing against that gap would have meant inventing
architecture, which the gate forbids.

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
  dataset_id            copied from dataset_meta; the only id it needs
  generated_by          engine name + version
  window                the period examined
  candidates[]          ranked, best first
  considered[]          hypotheses examined and rejected, with why
  insufficient_evidence bool - a first-class answer, not a failure
```

Each candidate:

```
Candidate
  entity        {kind: tool|chamber|product|recipe|operator|step, id, name}
  score         a number the engine defines and the report explains
  onset         estimated, with an interval; null if not estimable
  evidence[]    {channel, statistic, value, comparison, support}
  confounders[] competing explanations considered and how they were controlled
  narrative     generated from the evidence, never free-form prose
```

Three requirements on the output, each answering a specific audited failure:

* **Every candidate must be falsifiable from the dataset.** An `evidence`
  entry names a channel, a statistic and a comparison an analyst can re-run.
* **`considered[]` is mandatory and must be non-empty** when a candidate is
  offered. The audited v1 narrated one conclusion and checked nothing else;
  scenario G exists precisely to punish an engine that ranks the first
  correlation it finds.
* **`insufficient_evidence` is a correct answer.** On scenario A it is the
  *only* correct answer, and an engine that always names someone scores worse
  than one that abstains.

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
  measures your budget. This is §8.2's open decision, and ADR-027 has now
  built the instrument it needs: `fabeval.reference` derives the distribution
  of the per-chamber statistic under exchangeability, exposes a critical value
  at a declared level and an exceedance probability for any standing, and
  depends on no dataset — so an abstention threshold read off it is calibrated
  against the null world without being fitted to it. The engine must not
  import it (§6.1 forbids importing `fabeval`); what it may inherit is the
  *method*, and the numbers above are what that method reports.
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
   counterpart of the emitter's T5, and it is the mutation test the next gate
   must ship. The mirror is required too: a changed *observable* value must
   change the report, or invariance is passing because the engine is inert.

## 7. What is deliberately out of scope

No LLM, no RAG, no agent, no graph database, no ontology, no external service
(ADR-006, binding prohibition). No ML before the statistical baseline exists
and is benchmarked (ADR-007). The engine is deterministic: the same database
gives the same report, and a report carries the engine version that produced
it.

## 8. Open decisions the next gate must make

Recorded rather than guessed, because inventing them here would be the
architecture-invention Part 11 forbids:

1. **The score's definition.** Whether a candidate's score is an evidence
   count, a combined effect size, a likelihood ratio against the null-world
   floor, or a rank-only ordering. §5's measurement argues against any
   single-channel score.
2. **The abstention threshold.** What evidence level makes
   `insufficient_evidence` the answer — and it must be calibrated against the
   null worlds, never against the faulted ones. *Partly advanced by ADR-027*:
   the calibration object now exists and has a declared level (0.05 for
   screening, the fab's own 3-sigma convention for an action limit). What is
   still open is which of the two an abstention threshold should use, and how
   evidence combines across channels before it is compared to either.
3. **Onset estimation.** Which change-point statistic, and whether onset is
   reported per candidate or per dataset.
4. **Whether `Investigation` is `fabops.investigation/v1`** (ADR-008's
   existing name, whose consumer is the FabKG export boundary) or a new
   schema. If it reuses the name it inherits that contract's versioning.
5. **Where the engine lives.** `src/fabops/diagnosis/` keeps it inside the
   analytical plane the lint already guards, which is the cheaper and more
   defensible option; a separate top-level package would need its own lint.
