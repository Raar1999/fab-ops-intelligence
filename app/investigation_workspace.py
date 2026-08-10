"""
investigation_workspace.py — the read surface of the engine.

Run with:

    streamlit run app/investigation_workspace.py -- --dataset path/to/fab.db

Six pages, the information architecture `DASHBOARD_AUDIT` §4 recommends: Fab
Today, Process, Equipment, Yield, the Investigation workspace, and the Wafer
explorer. Every number on every one of them is prepared by
`fabops.report.workspace` and drawn by `fabops.report.figures`; this file
computes nothing and asserts nothing.

That is the whole design, and it is the fix for what the audit found. The old
dashboard highlighted a row pink because a module-level constant named the
suspect, its maps tab defaulted to that tool, and its caption told the reader
what to conclude before they had read anything. **There is no suspect constant
in this file, and there cannot be one that means anything:** the schema v2
surfaces are handed a database path and told nothing else, and where a
candidate is highlighted here it is highlighted because `fabops.diagnosis`
ranked it — which on a fault-free dataset means nothing is highlighted at all.

The legacy dashboard (`app/ops_dashboard.py`) is untouched and still narrates
the schema v1 demo, per ADR-010: the two read different fabs, so this is not
its replacement on that surface and does not delete it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import streamlit as st

from fabops.report import figures, workspace

st.set_page_config(page_title="Fab Ops — investigation workspace",
                   layout="wide")


def _arguments() -> tuple[str, str]:
    """The dataset path and the opening page, from the command line or the
    environment.

    There is no default dataset. `fabops.config.DB_PATH` names the *legacy v1*
    database, and a v2 surface that fell back to it would answer questions
    about a different fab without an error anywhere. The page is a convenience
    so that a command or a link can open on the screen it is about.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dataset", default=os.environ.get("FABOPS_DATASET"))
    parser.add_argument("--page", default=os.environ.get("FABOPS_PAGE"))
    known, _rest = parser.parse_known_args(sys.argv[1:])
    return known.dataset or "", known.page or ""


@st.cache_data(show_spinner="running the engine and the monitors…")
def _load(db_path: str, stamp: float):
    """One pass over the dataset. `stamp` is the file's mtime, so a rebuilt
    dataset invalidates the cache — the audited dashboard's `st.cache_data`
    never did, and showed stale numbers until the process was restarted."""
    return workspace.load_workspace(db_path)


def _sidebar() -> tuple[str, str]:
    dataset, opening = _arguments()
    pages = list(workspace.WORKSPACE_PAGES)
    index = pages.index(opening) if opening in pages else 0
    st.sidebar.title("Fab Ops")
    path = st.sidebar.text_input("dataset (path to fab.db)", value=dataset)
    page = st.sidebar.radio("page", pages, index=index)
    st.sidebar.caption(
        "Synthetic data. This surface renders engine output and computes "
        "nothing of its own; where a candidate is highlighted it is because "
        "the engine ranked it.")
    return path, page


def _fab_today(data) -> None:
    today = data["fab_today"]
    summary = data["dataset"]
    columns = st.columns(4)
    columns[0].metric("wafers tested", today["wafers_tested"])
    columns[1].metric("fab attainment (pts)",
                      f"{today['fab_attainment_pts']:+.2f}")
    columns[2].metric("lots", summary["counts"]["lots"])
    columns[3].metric("horizon (days)", summary["horizon_days"])

    st.pyplot(figures.attainment_trend(today["weekly_trend"]))
    st.caption(today["note"])
    st.pyplot(figures.product_attainment(today["products"]))

    left, right = st.columns(2)
    with left:
        st.subheader("Signals raised")
        st.table([{"family": family, "signals": count}
                  for family, count in today["signal_counts"].items()])
        st.caption("A healthy fab raises signals too — the monitors' realized "
                   "rate on fault-free worlds is published in "
                   "`fabops.monitors`. A signal is a prompt, not a claim.")
    with right:
        st.subheader("Least available chambers")
        st.dataframe([{"chamber": row["chamber_label"],
                       "down (h)": round(row["down_min"] / 60.0, 1),
                       "PM (h)": round(row["pm_min"] / 60.0, 1),
                       "utilization": round(row["utilization"], 3)}
                      for row in today["least_available_chambers"]],
                     width="stretch")

    st.subheader("Defect movers")
    if today["defect_movers"]:
        st.dataframe([{"chamber": row["entity"]["id"],
                       "channel": row["channel"], "day": row["day_index"],
                       "rule": row["rule"], "z": row["z"]}
                      for row in today["defect_movers"]],
                     width="stretch")
    else:
        st.info("no defect-rate rule hits on this dataset")


def _process(data) -> None:
    page = data["process"]
    st.subheader("Process control charts")
    if not page["chambers"]:
        st.info("no chamber has enough same-role peers to be charted on this "
                "world; see the structural boundary in DIAGNOSIS_CONTRACT §8.2")
        return
    left, right = st.columns(2)
    chamber = left.selectbox("chamber", page["chambers"])
    channel = right.selectbox("channel", page["channels"])

    connection = workspace.open_layer(st.session_state["dataset_path"])
    try:
        chart = workspace.control_chart(
            connection, channel, chamber, data["dataset"]["horizon_days"],
            page["signals"])
    finally:
        connection.close()

    if chart is None:
        st.warning(f"{chamber} has no chartable series on {channel} — either "
                   "too few baseline days or too few same-role peers")
        return
    st.pyplot(figures.control_chart_figure(chart))
    st.caption(chart["note"])
    st.write(f"baseline: {chart['baseline_points']} days ending day "
             f"{chart['baseline_last_day']}; limits widened ×"
             f"{chart['inflation']} for the uncertainty of that estimate")
    if chart["signals"]:
        st.dataframe([{"day": s["day_index"], "rule": s["rule"],
                       "z": s["z"], "value": s["value"]}
                      for s in chart["signals"]], width="stretch")

    st.subheader("Signals per chamber, with their denominators")
    st.dataframe([{"chamber": name, **record}
                  for name, record in sorted(page["per_chamber"].items())],
                 width="stretch")
    st.caption(page["per_chamber_note"])


def _equipment(data) -> None:
    page = data["equipment"]
    st.subheader("Equipment health")
    st.dataframe([{"chamber": name,
                   "utilization": record.get("utilization"),
                   "availability": record.get("availability"),
                   "MTBF (h)": record.get("mtbf_hours"),
                   "MTTR (h)": record.get("mttr_hours"),
                   "down events": record.get("down_events"),
                   "defects/wafer": round(
                       page["defects_per_wafer"].get(name, 0.0), 2)}
                  for name, record in sorted(page["health"].items())],
                 width="stretch")
    st.caption(page["note"])

    chambers = sorted(page["health"])
    if chambers:
        chamber = st.selectbox("state timeline", chambers)
        connection = workspace.open_layer(st.session_state["dataset_path"])
        try:
            intervals = workspace.state_timeline(connection, chamber)
        finally:
            connection.close()
        st.pyplot(figures.state_timeline_figure(
            intervals, data["dataset"]["horizon_days"]))

    if page["signals"]:
        st.subheader("Equipment signals")
        st.dataframe([{"chamber": s["entity"]["id"], "channel": s["channel"],
                       "rule": s["rule"], "z": s["z"], "value": s["value"]}
                      for s in page["signals"]], width="stretch")


def _yield(data) -> None:
    page = data["yield"]
    st.subheader("Attainment by product")
    st.dataframe(page["products"], width="stretch")
    st.subheader("Lots, worst attainment first")
    st.dataframe(page["lots"], width="stretch")
    st.subheader("Chamber standing on cohort yield")
    st.dataframe(page["chamber_standing"], width="stretch")
    st.caption(page["chamber_note"])


def _investigation(data) -> None:
    decision = data["investigation"]
    investigation = decision["investigation"]
    abstention = investigation["abstention"]

    if investigation["insufficient_evidence"]:
        st.warning(
            f"**Insufficient evidence.** No candidate is more extreme than "
            f"relabelling the candidates produces "
            f"(family-wise p = {abstention['p_familywise']} against α = "
            f"{abstention['alpha']}). On a fault-free dataset this is the only "
            f"correct answer, and it is a first-class one.")
    else:
        st.success(f"Leading candidate offered at family-wise p = "
                   f"{abstention['p_familywise']}")

    st.caption(f"engine {investigation['generated_by']} · window "
               f"{investigation['window']['start_day']}–"
               f"{investigation['window']['end_day']} d · "
               f"{abstention['permutations']} permutations")

    st.subheader("Ranked candidates")
    assessed = [c for c in investigation["candidates"]
                if c["status"] == "assessed"]
    st.dataframe([{"kind": c["entity"]["kind"], "entity": c["entity"]["id"],
                   "p": c["p_value"], "score": round(c["score"], 3),
                   "onsets": ", ".join(str(o["day"]) for o in c["onsets"])}
                  for c in assessed[:15]], width="stretch")

    if assessed:
        chosen = st.selectbox("evidence for", [c["entity"]["id"]
                                               for c in assessed[:15]])
        candidate = next(c for c in assessed if c["entity"]["id"] == chosen)
        st.write(candidate["narrative"])
        st.dataframe(candidate["evidence"], width="stretch")
        if candidate["confounders"]:
            st.subheader("Confounders controlled")
            st.dataframe(candidate["confounders"], width="stretch")

    st.subheader("Considered and rejected")
    st.dataframe(investigation["considered"], width="stretch")

    st.subheader("Impact")
    if decision["impact"] is None:
        st.info("No subject, so no impact estimate. Name a subject with "
                "`fabops-report --subject` to ask what acting on it would "
                "cost; the artifact will record that you supplied it.")
    else:
        impact = decision["impact"]
        columns = st.columns(3)
        columns[0].metric("exposed wafers", impact["exposed_wafers"])
        columns[1].metric("within-product deficit (pts)",
                          f"{impact['within_product_deficit_pts']:+.3f}",
                          delta=f"± {impact['standard_error_pts']:.3f}")
        columns[2].metric("standing vs peers (z)",
                          f"{impact['standing_z_among_peers']:+.2f}")
        st.caption(impact["note"])
        if not impact["distinguishable_from_benign_variation"]:
            st.warning("This deficit is inside the spread this fab's healthy "
                       "chambers show. Treat the die figure as an upper bound "
                       "on what containment could recover, not as a loss.")
        st.dataframe(decision["containment"]["lots_ranked_by_exposure"],
                     width="stretch")

    st.subheader("Recommended")
    st.dataframe(decision["actions"], width="stretch")
    st.caption(f"knowledge table: {decision['provenance']['knowledge']}")


def _wafer_explorer(data) -> None:
    wafers = data["wafers"]
    if not wafers:
        st.info("no tested wafers in this dataset")
        return
    index = {row["wafer_id"]: row for row in wafers}
    ordered = sorted(wafers, key=lambda row: (row["attainment_pts"],
                                              row["wafer_id"]))
    labels = [f"{row['wafer_id']} — {row['product_name']} "
              f"({row['yield_pct']:.1f}%)" for row in ordered]
    choice = st.selectbox("wafer (worst attainment first)", labels)
    wafer_id = ordered[labels.index(choice)]["wafer_id"]

    connection = workspace.open_layer(st.session_state["dataset_path"])
    try:
        detail = workspace.wafer_detail(connection, wafer_id)
    finally:
        connection.close()

    columns = st.columns(4)
    columns[0].metric("yield", f"{detail['yield_pct']:.1f}%")
    columns[1].metric("target", f"{detail['target_yield_pct']:.1f}%")
    columns[2].metric("good die",
                      f"{detail['good_die']}/{detail['total_die']}")
    columns[3].metric("defects", len(detail["defects"]))

    left, right = st.columns(2)
    layers = sorted({d["layer"] for d in detail["defects"]}) or [None]
    with left:
        layer = st.selectbox("defect layer", layers)
        st.pyplot(figures.wafer_map(detail, layer))
    with right:
        st.pyplot(figures.die_map(detail))

    st.subheader("Route")
    st.dataframe(detail["runs"], width="stretch")
    st.caption(f"lot {index[wafer_id]['lot_id']} · "
               f"{index[wafer_id]['product_name']}")


_PAGES = {
    "Fab Today": _fab_today,
    "Process": _process,
    "Equipment": _equipment,
    "Yield": _yield,
    "Investigation": _investigation,
    "Wafer explorer": _wafer_explorer,
}


def main() -> None:
    path, page = _sidebar()
    if not path:
        st.title("Fab Ops — investigation workspace")
        st.info(
            "Point this at a schema v2 dataset: pass `--dataset "
            "path/to/fab.db` after `--`, set `FABOPS_DATASET`, or type the "
            "path in the sidebar. Datasets are built by the simulator and are "
            "not committed; the legacy schema v1 demo has its own dashboard "
            "at `app/ops_dashboard.py`.")
        return

    target = Path(path)
    if not target.exists():
        st.error(f"{target} does not exist.")
        return

    st.session_state["dataset_path"] = str(target)
    data = _load(str(target), target.stat().st_mtime)

    st.title(f"Fab Ops — {page}")
    st.caption(f"dataset {data['dataset']['dataset_id']} · schema "
               f"{data['dataset']['schema_version']} · rendered from "
               f"{data['generated_by']['engine']}, "
               f"{data['generated_by']['monitors']}")
    _PAGES[page](data)


main()
