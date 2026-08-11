"""
datasets.py — the page a user lands on: create one, or open one.

Three panels, in the order somebody actually needs them:

    Create      pick a scenario and a seed, press the button, wait ~20 s
    Open        a table of what exists, with what it is and whether it works
    Details     the provenance of whichever one is open

**What the scenario picker shows, and the two things it will not.** A scenario
offers its slug, its world, its horizon, its lot count and its default seed —
the build inputs, which are what decide how long the wait is and what the
dataset's identity will be. It does not offer the configuration's prose, and it
does not offer a count of the events in it. Both are answer-key material: every
configuration in this library describes in words which chamber it faults, and
an event count of zero is the complete answer to the fault-free member.
`fabapp.scenarios` is where that projection happens, and this page can only
render what it is given.

**What the browser shows about a dataset that was never written down.** The
scenario column is recovered by digesting the local scenario library and
matching against the manifest's identity, so it is right for a dataset built
here, right for one built by the command line, and honestly blank for one built
from a configuration this installation does not have.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from fabapp.config import locations
from fabapp.generate import GenerationError, create, would_produce
from fabapp.registry import READY, discover, resolve
from fabapp.scenarios import available

DATASET_STATE = "dataset_path"

#: The widest seed the generator accepts is bounded by `fabsim.rng`; the input
#: is bounded here only so the widget cannot offer a negative one. A rejected
#: seed comes back as the generator's own message rather than this page's.
_SEED_MIN, _SEED_MAX = 0, 999


def _open(path: Path | str) -> None:
    st.session_state[DATASET_STATE] = str(path)


def _create_panel() -> None:
    st.subheader("Create a dataset")
    options = available()
    if not options:
        st.error(f"No scenario configurations in {locations().scenarios}.")
        return

    slugs = [option.slug for option in options]
    left, middle, right = st.columns([3, 1, 1])
    slug = left.selectbox("Scenario", slugs,
                          help="What each scenario contains is deliberately "
                               "not shown: it is the answer the engine is "
                               "supposed to earn.")
    option = options[slugs.index(slug)]
    seed = middle.number_input("Seed", min_value=_SEED_MIN, max_value=_SEED_MAX,
                               value=int(option.default_seed), step=1,
                               help="Same scenario, same seed, same dataset — "
                                    "byte for byte.")
    right.metric("horizon", f"{option.horizon_days} d")

    st.caption(
        f"world `{option.world}` · {option.lots} lots · "
        f"{option.horizon_days}-day horizon · scenario id `{option.scenario_id}` "
        f"· generation takes about 20 seconds and writes ~120 MB")

    existing = would_produce(slug, int(seed))
    if existing.status == READY:
        st.info(f"This combination already exists as **{existing.dataset_id}** "
                f"— it is fully determined by the scenario, the world, the "
                f"seed and the versions, so rebuilding it produces the same "
                f"bytes.")
        columns = st.columns(2)
        if columns[0].button("Open it", type="primary", width="stretch"):
            _open(existing.db_path)
            st.rerun()
        rebuild = columns[1].button("Rebuild anyway", width="stretch")
        if not rebuild:
            return
    elif not st.button("Generate dataset", type="primary"):
        return

    _generate(slug, int(seed))


def _generate(slug: str, seed: int) -> None:
    stages = {
        "scenario selection": "Loading the scenario configuration and its world",
        "generation": "Running the simulator and writing the dataset",
        "registration": "Registering the dataset",
    }
    with st.status(f"Creating **{slug}** at seed {seed}…",
                   expanded=True) as status:
        def announce(stage: str) -> None:
            st.write(stages.get(stage, stage))

        try:
            created = create(slug, seed, rebuild=True, on_stage=announce)
        except GenerationError as exc:
            status.update(label=f"Generation failed at {exc.stage}",
                          state="error")
            st.error(str(exc))
            return
        st.write("The generator's self-test passed"
                 if created.validated else "Reusing the existing build")
        status.update(label=f"Created {created.record.dataset_id}",
                      state="complete")

    _open(created.record.db_path)
    st.success(f"**{created.record.dataset_id}** is ready. "
               f"Open **Fab Today** to explore it, or **Investigation** to "
               f"run the diagnosis.")
    st.rerun()


def _open_panel(current) -> None:
    st.subheader("Open a dataset")
    records = discover()
    where = locations()
    if not records:
        st.info(f"No datasets yet in `{where.datasets}`. Create one above.")
    else:
        st.dataframe(
            [{"dataset": record.dataset_id or record.db_path.parent.name,
              "scenario": record.scenario or "—",
              "seed": record.seed,
              "horizon (d)": record.horizon_days,
              "schema": record.schema_version,
              "generator": record.fabsim_version or "—",
              "status": record.status,
              "size (MB)": round(record.size_bytes / 1e6, 1),
              "built": record.created_at or "—"}
             for record in records],
            width="stretch", hide_index=True)

        usable = [record for record in records if record.usable]
        if usable:
            labels = [record.label for record in usable]
            index = 0
            if current is not None:
                for position, record in enumerate(usable):
                    if str(record.db_path) == str(current.db_path):
                        index = position
                        break
            chosen = st.selectbox("Dataset", labels, index=index)
            if st.button("Open dataset", type="primary"):
                _open(usable[labels.index(chosen)].db_path)
                st.rerun()
        else:
            st.warning("None of the datasets found can be opened. The status "
                       "column says why for each one.")

    with st.expander("Open a dataset from somewhere else"):
        st.caption(
            "A path to a `fab.db`, the directory holding one, or a dataset id. "
            "The legacy schema v1 demo database is refused here: it holds a "
            "different fab, and the legacy dashboard is how to read it "
            "(`fabops-app --legacy`).")
        typed = st.text_input("Path or dataset id", key="dataset_reference")
        if st.button("Open") and typed.strip():
            record = resolve(typed)
            if record.usable:
                _open(record.db_path)
                st.rerun()
            else:
                st.error(f"{record.status}: {record.detail}")


def _details_panel(current) -> None:
    if current is None or not current.usable:
        return
    st.subheader("Dataset details")
    st.caption(
        "Provenance, as the observable plane records it. A dataset is fully "
        "determined by its configuration, its world, its seed and the two "
        "version numbers; the fingerprint is those five in one value and the "
        "content digest is what the rows themselves hash to.")
    left, right = st.columns(2)
    with left:
        st.markdown(
            f"""
| | |
|---|---|
| dataset id | `{current.dataset_id}` |
| scenario | {current.scenario or "not in the local scenario library"} |
| scenario id | `{current.scenario_id or "—"}` |
| seed | {current.seed if current.seed is not None else "—"} |
| horizon | {current.horizon_days} days |
| built | {current.created_at or "—"} |
""")
    with right:
        st.markdown(
            f"""
| | |
|---|---|
| schema version | `{current.schema_version}` |
| generator version | `{current.fabsim_version or "—"}` |
| build fingerprint | `{(current.build_fingerprint or "—")[:24]}…` |
| content digest | `{(current.content_sha256 or "—")[:24]}…` |
| configuration digest | `{(current.config_sha256 or "—")[:24]}…` |
| world digest | `{(current.world_sha256 or "—")[:24]}…` |
""")
    if current.row_counts:
        with st.expander("Row counts"):
            st.dataframe([{"table": table, "rows": count}
                          for table, count in sorted(current.row_counts.items())],
                         width="stretch", hide_index=True)
    st.caption(f"`{current.db_path}`")


def render(current) -> None:
    st.title("FabOps — Datasets")
    st.caption(
        "A dataset is one run of the simulated fab. Create one from a "
        "scenario, or open one that exists; everything else in the "
        "application reads whichever one is open.")
    _create_panel()
    st.divider()
    _open_panel(current)
    st.divider()
    _details_panel(current)
