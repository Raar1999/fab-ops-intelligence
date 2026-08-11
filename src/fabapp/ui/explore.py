"""
explore.py — the six screens that describe the fab.

Fab Today, Process, Equipment, Yield, Defect and the Wafer explorer: the
information architecture `DASHBOARD_AUDIT` §4 recommends, in its order, with
the defect domain given its own page now that a product has room for one.

Every number here is prepared by `fabops.report.workspace` and drawn by
`fabops.report.figures`. This module contains no arithmetic beyond turning
minutes into hours for a column heading, and no query at all: where a page
needs a live connection — a control chart, a state ribbon, one wafer's map —
it opens the semantic layer and hands it straight back to the function that
owns the computation.

These pages moved here from `app/investigation_workspace.py` when the product
absorbed it, and they moved unchanged. What they render was already the right
thing; what was wrong was that reaching them required knowing a filename.
"""
from __future__ import annotations

import streamlit as st

from fabops.report import figures, workspace

PAGES = ("Fab Today", "Process", "Equipment", "Yield", "Defect",
         "Wafer explorer")


def _layer(record):
    return workspace.open_layer(record.db_path)


# ------------------------------------------------------------- Fab Today


def _fab_today(data, record) -> None:
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
                   "`fabops.monitors`. A signal is a prompt to look, not a "
                   "claim.")
    with right:
        st.subheader("Least available chambers")
        st.dataframe([{"chamber": row["chamber_label"],
                       "down (h)": round(row["down_min"] / 60.0, 1),
                       "PM (h)": round(row["pm_min"] / 60.0, 1),
                       "utilization": round(row["utilization"], 3)}
                      for row in today["least_available_chambers"]],
                     width="stretch", hide_index=True)

    st.subheader("Defect movers")
    if today["defect_movers"]:
        st.dataframe([{"chamber": row["entity"]["id"],
                       "channel": row["channel"], "day": row["day_index"],
                       "rule": row["rule"], "z": row["z"]}
                      for row in today["defect_movers"]],
                     width="stretch", hide_index=True)
    else:
        st.info("no defect-rate rule hits on this dataset")


# --------------------------------------------------------------- Process


def _process(data, record) -> None:
    page = data["process"]
    st.subheader("Process control charts")
    if not page["chambers"]:
        st.info("no chamber has enough same-role peers to be charted on this "
                "world; see the structural boundary in DIAGNOSIS_CONTRACT §8.2")
        return
    left, right = st.columns(2)
    chamber = left.selectbox("chamber", page["chambers"])
    channel = right.selectbox("channel", page["channels"])

    connection = _layer(record)
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
                      for s in chart["signals"]], width="stretch",
                     hide_index=True)

    st.subheader("Signals per chamber, with their denominators")
    st.dataframe([{"chamber": name, **row}
                  for name, row in sorted(page["per_chamber"].items())],
                 width="stretch", hide_index=True)
    st.caption(page["per_chamber_note"])


# ------------------------------------------------------------- Equipment


def _equipment(data, record) -> None:
    page = data["equipment"]
    st.subheader("Equipment health")
    st.dataframe([{"chamber": name,
                   "utilization": row.get("utilization"),
                   "availability": row.get("availability"),
                   "MTBF (h)": row.get("mtbf_hours"),
                   "MTTR (h)": row.get("mttr_hours"),
                   "down events": row.get("down_events"),
                   "defects/wafer": round(
                       page["defects_per_wafer"].get(name, 0.0), 2)}
                  for name, row in sorted(page["health"].items())],
                 width="stretch", hide_index=True)
    st.caption(page["note"])

    chambers = sorted(page["health"])
    if chambers:
        chamber = st.selectbox("state timeline", chambers)
        connection = _layer(record)
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
                      for s in page["signals"]], width="stretch",
                     hide_index=True)


# ----------------------------------------------------------------- Yield


def _yield(data, record) -> None:
    page = data["yield"]
    st.subheader("Attainment by product")
    st.dataframe(page["products"], width="stretch", hide_index=True)
    st.subheader("Lots, worst attainment first")
    st.dataframe(page["lots"], width="stretch", hide_index=True)
    st.subheader("Chamber standing on cohort yield")
    st.dataframe(page["chamber_standing"], width="stretch", hide_index=True)
    st.caption(page["chamber_note"])


# ---------------------------------------------------------------- Defect


def _defect(data, record) -> None:
    page = data["defect"]
    columns = st.columns(3)
    columns[0].metric("wafers with a scored signature", page["wafers_scored"])
    columns[1].metric("defect classes",
                      len(page["class_pareto"]))
    columns[2].metric("defect-rate signals", len(page["signals"]))

    left, right = st.columns([2, 3])
    with left:
        st.subheader("Class Pareto")
        st.dataframe(page["class_pareto"], width="stretch", hide_index=True)
        st.caption(
            "The classified type is a noisy draw over a hidden origin — the "
            "world declares a confusion matrix and the classifier obeys it — "
            "so this is a Pareto of what inspection *called* things, and no "
            "spatial claim rests on it.")
    with right:
        st.subheader("Defects per wafer, by chamber")
        st.dataframe(page["per_chamber"], width="stretch", hide_index=True)
        st.caption(page["per_chamber_note"])

    st.subheader("Spatial signatures")
    st.caption(page["signature_note"])
    leaders = page["signature_leaders"]
    names = [name for name in ("edge_share", "center_share", "clustering",
                               "linearity") if leaders.get(name)]
    if not names:
        st.info("no wafer carries enough defects to be scored on this dataset")
    else:
        for name, tab in zip(names, st.tabs(names)):
            with tab:
                st.dataframe(leaders[name], width="stretch", hide_index=True)

    if page["signals"]:
        st.subheader("Defect-rate rule hits")
        st.dataframe([{"chamber": s["entity"]["id"], "channel": s["channel"],
                       "day": s["day_index"], "rule": s["rule"], "z": s["z"]}
                      for s in page["signals"]], width="stretch",
                     hide_index=True)


# -------------------------------------------------------- Wafer explorer


def _wafer_explorer(data, record) -> None:
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

    connection = _layer(record)
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
    st.dataframe(detail["runs"], width="stretch", hide_index=True)
    st.caption(f"lot {index[wafer_id]['lot_id']} · "
               f"{index[wafer_id]['product_name']}")


_RENDERERS = {
    "Fab Today": _fab_today,
    "Process": _process,
    "Equipment": _equipment,
    "Yield": _yield,
    "Defect": _defect,
    "Wafer explorer": _wafer_explorer,
}


def render(page: str, data, record) -> None:
    _RENDERERS[page](data, record)
