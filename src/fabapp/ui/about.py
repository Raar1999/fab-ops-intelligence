"""
about.py — what this application is, where it keeps things, and what it is not.

Three jobs. It tells a first-time reader what they are looking at and what the
measured capability actually is; it prints the paths this installation resolved,
so "where did my dataset go?" is answerable without reading source; and it is
the signpost to the legacy schema v1 demo, which is a different fab and is
reachable but deliberately not merged in.
"""
from __future__ import annotations

import streamlit as st

from fabapp import APP
from fabapp.config import locations
from fabapp.service import WORKSPACE_PAGES


def render(current) -> None:
    st.title("FabOps — About")
    st.markdown(
        """
**FabOps is a simulated semiconductor fab, the answer-blind analysis stack that
reads it, and the benchmark that says how often that stack is right.**

The simulator writes two planes: an observable SQLite dataset, and a hidden
answer key that only the evaluator may read. Everything in this application
reads the observable one. The diagnosis engine is handed a database path and
nothing else — not the scenario, not the seed, not the fault — which is why
the ranked candidates on the Investigation page are earned rather than
narrated.

All data is synthetic. Nothing here is a real fab, a real benchmark, or a
deployed system.
""")

    st.subheader("What the engine can and cannot do")
    st.markdown(
        """
At its declared level the engine **abstains on every dataset this project can
build**: its false-alarm rate is zero and so is its detection rate. It ranks a
planted entity first on about one development dataset in ten. Those numbers are
measured by `fabops-benchmark`, published in the README, and regenerated rather
than typed.

It can only name entities in two of eight equipment families — every statistic
is a standing among same-role peers and needs at least two of them — and "root
cause" here means *entity attribution*, never mechanism identification. The
Investigation page says which of those boundaries it hit, per candidate.
""")

    st.subheader("Where this installation keeps things")
    where = locations().to_dict()
    st.dataframe(
        [{"what": key, "where": value} for key, value in where.items()
         if key != "overrides"], width="stretch", hide_index=True)
    overrides = where["overrides"]
    if overrides:
        st.caption(f"environment overrides in effect: {overrides}")
    else:
        st.caption(
            "No environment overrides are set. `FABOPS_HOME`, "
            "`FABOPS_SCENARIO_ROOT` and `FABOPS_DATASET_ROOT` move these.")

    st.subheader("The legacy schema v1 demo")
    st.markdown(
        """
This repository still contains its own predecessor: a schema **v1** database
and a narrated investigation whose conclusion is a documented constant. It is
kept deliberately — it is the project's regression anchor, and it reads a
*different fab* from everything in this application.

It is not merged into these pages, and that is the point: one application with
one navigation bar answering about two different fabs is exactly the confusion
this architecture spends its guards avoiding. Start it separately:

```
fabops-app --legacy
```

It is a **demonstration**, not a discovery engine — it narrates and verifies a
planted answer.
""")

    st.subheader("For developers")
    st.markdown(
        f"""
This application is `{APP}`. It orchestrates and presents; it computes nothing.

| command | what it does |
|---|---|
| `fabops-app` | this application |
| `fabops-app --check` | run the whole workflow headlessly and print the result |
| `fabops-app --list` | list the datasets that exist |
| `fabops-app --where` | print the paths above |
| `fabops-diagnose <db>` | the engine alone, as JSON |
| `fabops-monitor <db>` | the four monitor families |
| `fabops-report <db>` | the full decision artifact |
| `fabops-benchmark` | build a population and score the engine |

Pages carrying prepared data: {', '.join(WORKSPACE_PAGES)}.
""")
