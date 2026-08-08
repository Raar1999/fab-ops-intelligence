# Project Vision — Fab Ops Intelligence

## 1. The question this project answers

> **"What is happening in the fab, why is it happening, what is affected, and what should the engineer do next?"**

Everything in scope serves that sentence: process monitoring, equipment/tool intelligence, yield intelligence, defect intelligence, excursion detection, root-cause investigation, impact analysis, and engineering decision support — on a reproducible synthetic fab whose faults the analysis code is never told.

Explicitly out of scope: knowledge representation, knowledge graphs, literature-derived semantics, LLMs/agents/RAG. Those belong to the separate FabKG project (see `docs/audit/FABOPS_VS_FABKG_BOUNDARY.md`). A knowledge-graph consumer may optionally ingest this project's investigation artifacts; this project stands alone.

## 2. Where we start (honest baseline, from the audit)

Today the repository is a **well-crafted, honest demonstration of one pre-authored yield investigation** on planted data: excellent reproducibility (verified byte-deterministic generator, executed notebook, 26 green tests), a clean SQL-view analytical layer, a genuinely strong defect-spatial capability — and a root cause that is hard-coded into the analysis, charts, dashboard, and tests. The full findings live in `docs/audit/`.

The vision is not to discard this — it is to make the README's existing claim *literally true*: the system, not the author, traces the yield miss to the marginal tool.

## 3. Where we are going (definition of the target)

A **Fab Operations Intelligence platform** with a measurable core loop:

1. **fabsim** generates a fab dataset from a scenario config (fault type, location, onset, severity, seed) that only the generator ever sees — including no-fault and confounded scenarios.
2. **Monitors** (process SPC/drift, equipment states/degradation, normalized yield, defect rates + spatial signatures) watch the data.
3. **Detection** raises first-class excursion objects (what, when, scope).
4. **Diagnosis** enumerates hypotheses across exposure dimensions, collects evidence per family, scores it transparently, and ranks candidates — with "insufficient evidence" as a legitimate outcome.
5. **Impact & actions** quantify loss, rank lot exposure for containment, and template engineering recommendations.
6. **Evaluation** runs the scenario suite and publishes detection rate, attribution precision/recall, false-positive rate, and time-to-detect. **No capability is claimed without its number.**

Success criterion, in one sentence: *on a randomized scenario the platform has never seen, `fabops investigate` names the planted fault (or correctly declines to), shows the evidence that earns the conclusion, and the benchmark table proves how often it gets this right.*

## 4. Identity guardrails

- **Operations, not knowledge.** Relationships here are foreign keys and statistics. No graphs, ontologies, triple stores.
- **Explainable statistics.** Every score decomposes into terms an engineer can recompute. ML only ever enters as a benchmarked comparison against the statistical baseline, never as the default.
- **Right-sized infrastructure.** SQLite + Python + Streamlit, clone-and-run. No services, no cloud, no streaming theater.
- **Synthetic and says so.** Every surface discloses the data's provenance; no fabricated production claims, ever.

## 5. What this demonstrates to semiconductor employers

Mapping actual capabilities (current and target) to the roles this project speaks to:

| Role | Demonstrated today (verified) | Added by the target platform |
|---|---|---|
| **Yield Engineer** | The full excursion arc: symptom → tool commonality → defect-signature confirmation → impact (die-loss counterfactual) → containment ranking; wafer-map literacy | Excursion detection with onset estimation; attribution that controls for confounders; measured attribution accuracy; step/recipe-level loss attribution |
| **Process Engineer / Process Control** | Spec-limit awareness, gate-CD framing, zone-calibrated spatial analysis | SPC/EWMA/CUSUM on parameters with real drift; recipe-change analysis; process-window monitoring; onset-time evidence in diagnosis |
| **Equipment Engineer** | Downtime/PM framing per tool; maintenance-log fluency | Tool-state timelines, utilization/MTBF/MTTR, chamber-grain analysis, between-PM degradation trends, maintenance-effect (before/after) validation |
| **Manufacturing / Ops** | Lot exposure and containment prioritization | Hold recommendations, mix-honest fab KPIs, action templates with post-action validation |
| **Data / Analytics Engineer** | Star-fact + view-layer modeling; deterministic data engineering; tested analytical SQL; thin-client dashboard | Scenario-driven data generation, semantic layer design, evaluation-harness engineering — the rare combination of domain + rigor |
| **Data Scientist / ML Engineer** | Honest leakage analysis (this audit is itself the demonstration) | A benchmark with nulls and confounders — the correct substrate for ever justifying ML |

The differentiator against typical portfolio projects is not breadth — it is **falsifiability**: a diagnostic system whose error rate is measured and published.

## 6. Public positioning (GitHub)

- **Repository name:** `fab-ops-intelligence` (recommended; `fab-ops-analytics` acceptable until the diagnosis engine lands — "intelligence" must be earned by Phase 5/6 of the roadmap).
- **One-liner:** *"Fab operations intelligence on a synthetic semiconductor fab: SPC, equipment health, yield & defect analytics, and a root-cause engine that finds planted faults it was never told about — with a published detection/attribution benchmark."*
- **README structure (target):**
  1. What it does (the core loop, §3) + the benchmark table (generated by `eval/`, never hand-written)
  2. 90-second demo: `pip install -e . && fabops build demo && fabops investigate` with real output
  3. The demo case study (today's ETCH-02 narrative, reframed as *one scenario the engine solves*, wafer maps retained — they are the visual hook)
  4. Architecture diagram (data plane / intelligence plane / evaluation plane)
  5. Synthetic-data statement: what the data is, how faults are planted, why the engine can't cheat (answer-blindness), and **what these results do and do not imply** (they show method validity on simulated faults; they are not field performance)
  6. Limitations (single fab, simplified route, no sensor traces, simulated physics) — kept brutal
  7. Testing & reproducibility (seed determinism, CI, invariant tests)
  8. Roadmap pointer into `docs/`
- **Never claim:** production deployment, real fab data, real-world yield improvement, real benchmark numbers, "AI-powered." The credibility of every real number depends on never faking one.
- **Keep:** the existing README's honesty voice — the audit verified every number it states; that standard is now the project's brand.

## 7. Relationship to the SQL Mastery Handbook

The handbook (separate folder in the bundle) is an educational artifact that teaches SQL against this schema. It stays a separate deliverable — linked from the README at most. The platform repo does not absorb teaching material; the handbook does not constrain the platform's schema evolution (it pins to the frozen demo dataset).
