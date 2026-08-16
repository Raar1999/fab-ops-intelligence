# Architecture — the system as built

This is a map of the **implemented** system: the four packages, what each one is
allowed to see, and how the boundaries between them are enforced.

It is deliberately short, and it is not the design record. The reasoning behind
each decision is in [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md);
the binding contracts are in [`design/`](design/); the forensic audit of the v1
system that started the project is in [`audit/`](audit/), including
[`audit/TARGET_ARCHITECTURE.md`](audit/TARGET_ARCHITECTURE.md), which is the
*pre-implementation* target and is kept as a historical record — where it and
this document differ, this one describes what shipped.

---

## 1. The pipeline

```
FabSim
  ↓  generates a synthetic fab: world, timeline, latent state, response,
  ↓  observation, defects, die and yield
Observable SQL database  (SQLite, schema v2)        truth.json  (hidden)
  ↓                                                      │
FabOps semantic layer — facts and views, read-only       │
  ↓                                                      │
FabOps monitors — process · equipment · yield · defect   │
  ↓                                                      │
FabOps diagnosis — answer-blind, ranked candidates       │
  ↓  or `insufficient_evidence`                          │
FabOps report — impact, containment, recommended checks  │
  ↓                                                      ▼
FabApp — the product workspace                     FabEval — the benchmark
  ↓                                                  reads BOTH planes,
User: explore · investigate · export                 writes nothing
```

Every arrow above is a one-way dependency. Nothing below the database reads the
scenario that produced it.

## 2. The four packages and their privileges

| Package | Role | May read | May write |
|---|---|---|---|
| `fabsim` | generates the fab | the scenario config — **the only code that ever sees it** | both planes: the observable dataset and `truth/` |
| `fabops` | analyses the fab | the observable database only, by path | reports and artifacts; never the dataset |
| `fabeval` | measures the analysis | **both** planes — the answer and the report at once | nothing (ADR-024, an absolute lexical rule) |
| `fabapp` | presents the product | the observable plane, through `fabops` | datasets it generates, on the user's instruction |

Those four rows are the architecture. Everything else is detail.

`fabapp` is the interesting case: it *generates* datasets and then *analyses*
them, which is what a product does, so it is the one component that spans the
build and the read. It may reach neither the hidden plane nor the evaluator —
generation returns a handle with no field an answer could arrive through, and
the analysis path takes a database path and nothing else.

## 3. The two planes

A dataset is two files that live side by side:

- the **observable plane** — a SQLite database, schema v2, the operational data a
  real fab would have: lots, wafers, runs, chambers, tool events, inspections,
  defects, metrology, yield, maintenance;
- the **hidden plane** — `truth.json`, which records what was actually planted:
  the fault, its entity, its onset, its mechanism, its severity.

The engine is handed a **database path, not a directory**, because a directory
has `truth/` as a sibling. That is a signature, not a convention, and a test
fails if the signature widens. The full treatment is in
[`design/ANTI_LEAKAGE_DESIGN.md`](design/ANTI_LEAKAGE_DESIGN.md) and
[`design/GROUND_TRUTH_CONTRACT.md`](design/GROUND_TRUTH_CONTRACT.md).

## 4. The public surfaces

These are the entry points the rest of the system and the tests are written
against. They are stable; the internals behind them are not a contract.

```python
fabsim.emit.build_dataset(config, seed, world=..., root=...)  -> Dataset
fabops.semantic.open_layer(db_path)      -> sqlite3.Connection   # TEMP views, read-only
fabops.monitors.monitor(db_path, ...)                            # four families
fabops.diagnosis.diagnose(db_path, ...)                          # the answer-blind engine
fabops.report.build_report(db_path, subject=None)                # the decision artifact
fabapp.service.workflow_check(dataset)                           # the product chain, headless
fabeval.matrix.evaluate(built, sweep)                            # scores A1-A11, reads both planes
```

and the console scripts that wrap them:

```
fabops-app          the product (fabapp.cli)
fabops-diagnose     the engine alone, as JSON
fabops-monitor      the four monitor families
fabops-report       the full decision artifact
fabops-benchmark    build a population and score the engine
fabops-build        the LEGACY v1 database
fabops-investigate  the LEGACY v1 narrated demo
```

`fabops-investigate` and `fabops-diagnose` are separately named on purpose: the
first narrates a conclusion that is a documented constant, the second is the
engine. Typing the obvious command must not hand somebody the planted story
while they believe they ran the analysis (ADR-003, ADR-010).

`FabOps.bat` at the repository root is the only launcher, and it is not a
seventh surface: it is a batch file that runs `python -m pip install -e ".[app]"`
and then `fabops-app`, in that order, and starts nothing if the install fails.
It holds no logic of its own, so the architecture above is unchanged by whether
a user double-clicks it or types the two commands.

## 5. How the boundaries are enforced

They are not enforced by convention or by review. Three mechanisms, all in
`tests/`:

1. **Import scans.** No module under `fabops/` may import `fabsim`'s scenario or
   truth surfaces; no module under `fabapp/` may import `fabeval`.
2. **Signature scans.** The functions on the analysis path are checked for a
   parameter an answer could arrive through. A widened signature fails the scan
   even if nothing yet passes an answer to it.
3. **Runtime invariance.** One dataset's investigation is rendered twice — once
   with the scenario resolvable and once with it not — and the two must be
   byte-identical. This is the check that catches leakage the first two miss,
   because it tests behaviour rather than shape.

Determinism supports all three: same inputs, same content hash; every
multi-row query states a total order; and shuffling the rows of any query
without an `ORDER BY` must leave every report byte-identical.

## 6. Where FabKG is

**Nowhere in this repository.** FabKG is a separate project. The boundary — one
optional, file-based, versioned exchange in each direction, and no shared code —
is fixed in [`audit/FABOPS_VS_FABKG_BOUNDARY.md`](audit/FABOPS_VS_FABKG_BOUNDARY.md).

No knowledge graph, ontology, LLM, agent, RAG or literature-retrieval component
exists here, and ADR-006 is a binding prohibition on adding one. Domain
knowledge enters this system in exactly one place and one form: the recommended
checks in `fabops/actions/`, which is a versioned data file rather than code, so
that a FabKG-supplied replacement would be a file swap and not a release.

## 7. What the architecture deliberately does not have

No service layer, message broker, external database, container runtime, ML model
or scheduler. The substrate is SQLite, SQL views, pandas, matplotlib, Streamlit
and pytest, and the scale ceiling — one fab, one route, roughly 500 wafers per
dataset — is declared rather than apologised for (ADR-012).

Every detector is a formula an engineer can recompute by hand: SPC rules,
EWMA/CUSUM, permutation tests, effect sizes, additive evidence scores. That is a
requirement, not a stage — an explainable statistic can be argued with, and the
project's claim is about method rather than performance.
