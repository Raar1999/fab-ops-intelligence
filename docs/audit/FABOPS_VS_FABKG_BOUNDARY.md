# Fab Ops Intelligence vs FabKG — Boundary Definition

**Context:** FabKG is a separate project concerned with knowledge representation, evidence/knowledge graphs, and broader semiconductor intelligence. This repository must not become a second FabKG, and must remain fully useful with FabKG absent. This document fixes the boundary.

---

## 1. The one-line distinction

> **Fab Ops Intelligence computes what is happening in *this* fab from *this* fab's operational data. FabKG represents what is known about semiconductor manufacturing in general.**

Fab Ops answers: *what happened, since when, caused by what, affecting what, and what should the engineer do next* — by statistics over lots, wafers, runs, tools, defects, and maintenance events with timestamps.

FabKG (as understood from its charter) curates and connects *knowledge*: mechanisms, literature, taxonomies, persistent cross-domain relationships — things that stay true across fabs and across months.

## 2. Ownership

### What Fab Ops Intelligence owns
- The operational data model (lots, wafers, runs, tools, chambers, recipes, inspections, defects, yield, maintenance, tool events) and its synthetic generator.
- Statistical monitoring: SPC/control charts, drift and change-point detection, excursion detection.
- Equipment analytics: states, utilization, downtime, degradation, maintenance-effect analysis.
- Yield analytics: target-normalized monitoring, decomposition, loss attribution.
- Defect analytics: rates, Pareto, spatial signatures, wafer-map patterns.
- The diagnostic engine: hypothesis enumeration, evidence collection/correlation, scoring, ranking.
- Impact analysis and containment/exposure ranking; engineering recommendations.
- The investigation record for *this fab's* excursions (as rows/artifacts, not as a graph).
- Its own evaluation harness (scenario benchmarks).

### What FabKG owns
- General semiconductor knowledge: fault mechanisms, defect taxonomies, process-physics relationships, literature-derived facts.
- Knowledge representation itself: ontologies, graph storage, semantic query, cross-domain linking.
- Persistent, fab-independent evidence relationships ("edge-ring signatures are associated with chamber clamp/uniformity faults *in general*").
- Any reasoning that spans domains or sources beyond one fab's operational records.

### The litmus tests
1. **Statistics or semantics?** If the operation is aggregation/estimation/detection over timestamped operational rows → Fab Ops. If it is representing/linking concepts → FabKG.
2. **Would a yield engineer compute it during an excursion?** → Fab Ops. **Would they look it up in a handbook or cite a paper for it?** → FabKG.
3. **Does it expire?** Facts that go stale in hours or days (tool health, active excursions, exposure) → Fab Ops. Facts stable across fabs and years (mechanism knowledge) → FabKG.
4. **Storage smell test:** if the natural home is a SQL table keyed by lot/wafer/tool/time → Fab Ops. If the natural home is a node/edge with provenance → FabKG.

## 3. Concretely, for this repository

- The mapping "EDGE_RING defect fraction ↑ on one chamber ⇒ suspect edge-uniformity/clamp fault" is *domain knowledge*. Fab Ops may ship a small, versioned **local knowledge table** (a plain data file: fault class → expected signatures → recommended checks) sufficient for its recommendations. That table is Fab Ops' own; if FabKG matures, it may *optionally* supply a richer replacement through the interface below. Fab Ops must never grow ontology machinery to manage it.
- Investigation *records* (excursion → evidence → conclusion → outcome) are Fab Ops data. Representing them as a knowledge graph is FabKG's business, fed by export.
- No graph database, no triple store, no ontology library, and no semantic-reasoning layer may be introduced into this repository for any reason. A "relationship" in Fab Ops is a foreign key or a computed statistic.

## 4. Interaction contract (optional, one-way-at-a-time, file-based)

Design goal: either project must build and run with the other entirely absent. Integration is **data exchange through versioned flat files** — no shared code, no shared database, no network calls, no import of one project by the other.

### Export: Fab Ops → FabKG (the useful direction)
Fab Ops may emit an **investigation artifact** per completed excursion — a schema-versioned JSON document:

```
{ "schema": "fabops.investigation/v1",
  "excursion":   { detected_at, signal, scope: {lots, wafers, window} },
  "hypotheses":  [ { candidate, type: tool|chamber|recipe|…, score,
                     evidence: [ { family, effect_size, support, direction } ] } ],
  "conclusion":  { candidate | "insufficient_evidence", confidence },
  "impact":      { est_die_loss, affected_lots },
  "actions":     [ … ],
  "provenance":  { dataset_seed, scenario_id, code_version } }
```

FabKG may ingest these as evidence instances. Fab Ops writes the file and is done — it has no knowledge of, or dependency on, any consumer.

> **As implemented (2026-08-10, ADR-034). The sketch above is left as written; two of its names moved.** `fabops.investigation/v1` was claimed by `DIAGNOSIS_CONTRACT.md` §3 for exactly what `diagnose(db_path)` returns — ranked candidates, their evidence, the rivals considered, onsets and the abstention — and ADR-029 §7 closed that. The document with `impact`, `actions` and `provenance` in it is therefore **`fabops.report/v1`**, written by `fabops-report`, and it embeds the investigation verbatim rather than restating it.
>
> The other name change is `excursion`: it is not a field, because it is not an input. Its three parts are each already present and better placed — the window on the investigation, the onsets per candidate (plural, and permitted to be absent), and the scope as the ranked candidate list with its rejected rivals beside it (ADR-029 §8).
>
> Everything the anti-coupling rules require is unchanged and is checked: no FabKG dependency in `pyproject.toml`, no import in either direction, the schemas versioned here, and the import path exercised with a locally committed fixture file rather than any FabKG asset.

### Import: FabKG → Fab Ops (strictly optional)
FabKG may supply **knowledge priors** as a plain data file matching Fab Ops' local knowledge-table schema (fault class → signatures → checks → typical mechanisms). Fab Ops treats it exactly like its built-in table: loaded if present and schema-valid, ignored otherwise. No FabKG runtime, format, or library leaks in.

> **As implemented (2026-08-10, ADR-034).** The table is `src/fabops/actions/knowledge.json`, schema `fabops.knowledge/v1`, keyed by **evidence signature** rather than by fault class — because no observable channel in schema v2 identifies which mechanism acted, so a table keyed by mechanism would be a catalogue this project's data cannot index (`DIAGNOSIS_CONTRACT.md` §5.2). It maps *which families of evidence led* onto *what to go and look at*, and a test scans it for mechanism vocabulary and fails on any.
>
> One deliberate deviation from "ignored otherwise": a replacement that is present and invalid falls back to the built-in table **and records why** in the report's `provenance.knowledge.rejected_override`. Ignoring it silently would let a fab run for months on a table that never loaded. Nothing else is stricter, and nothing is looser.

### Anti-coupling rules (enforceable in review)
1. No dependency in `requirements`/`pyproject` originating from FabKG.
2. No import of FabKG code; no FabKG import of this repo's internals (artifacts only).
3. Schemas versioned in *this* repo; changes are backward-compatible or version-bumped.
4. CI never requires FabKG assets; integration paths are tested with locally committed fixture files.
5. The README describes FabKG integration in one short optional section, or not at all.

## 5. Why this boundary is the clean one

The two projects fail in different ways and are validated in different ways: Fab Ops is falsifiable by scenario benchmarks (did it find the planted fault? did it stay quiet on the null scenario?); FabKG is judged by representation quality and coverage. Merging them couples a measurable statistics engine to an open-ended representation effort, and the audit shows this repository's entire value lies on the measurable side. The exchange-file contract lets FabKG benefit from Fab Ops' outputs without either project inheriting the other's failure modes.
