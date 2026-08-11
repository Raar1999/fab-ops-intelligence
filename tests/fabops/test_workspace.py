"""
The workspace data layer: the numbers behind every page, and the figures.

`DASHBOARD_AUDIT` §4 asks for a surface that *renders engine output and never
computes or asserts a conclusion itself*. That splits into two testable halves
and this module owns the first:

* every page's payload is produced without Streamlit, so it is reachable from a
  test that needs no browser, and the figures draw the same numbers the
  decision was made from — here;
* no screen contains an entity literal, a query or an import of the simulator —
  `tests/fabapp/test_ui_guards.py`, which applies those scans to *every* module
  of the product's interface rather than to one file.

The second half used to live here and pointed at `app/investigation_workspace.py`.
That file was a duplicate v2 entry point once the product absorbed its six
pages, so it is gone and its guards moved with the pages (ADR-037). The legacy
schema v1 dashboard is untouched and is still checked, in the same place.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fabops.report import figures, workspace
from fabops.semantic import open_layer

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def layer(demo_dataset):
    connection = open_layer(demo_dataset["db_path"])
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def loaded(demo_dataset):
    return workspace.load_workspace(demo_dataset["db_path"])


# ---------------------------------------------------------------- the data


def test_every_page_has_a_payload(loaded):
    assert set(workspace.WORKSPACE_PAGES) == {
        "Fab Today", "Process", "Equipment", "Yield", "Defect",
        "Investigation", "Wafer explorer"}
    for key in ("dataset", "fab_today", "process", "equipment", "yield",
                "defect", "investigation", "wafers", "monitor"):
        assert loaded[key], key
    assert loaded["dataset"]["schema_version"] == "2.0"
    assert "fabsim_version" not in loaded["dataset"], (
        "the analysis plane read the generator's version; the code-plane lint "
        "forbids naming it and the manifest already carries it")
    assert loaded["generated_by"]["engine"].startswith("fabops.diagnosis/")


def test_fab_today_reports_attainment_beside_the_raw_mean(loaded):
    today = loaded["fab_today"]
    assert today["weekly_trend"]
    for row in today["weekly_trend"]:
        assert {"week_index", "mean_yield_pct", "mean_attainment_pts"} <= set(
            row)
    raw = [row["mean_yield_pct"] for row in today["weekly_trend"]]
    normalized = [row["mean_attainment_pts"] for row in today["weekly_trend"]]
    assert max(raw) - min(raw) > max(normalized) - min(normalized), (
        "the raw weekly series is not more variable than the normalized one; "
        "either the mix artifact is gone from this dataset or the "
        "normalization is not doing anything")
    assert today["products"]
    assert today["wafers_tested"] > 0


def test_the_control_chart_is_the_one_the_rules_were_evaluated_on(layer,
                                                                  loaded):
    """A picture that disagrees with the decision it illustrates is worse than
    no picture. Every rule hit must land on a point the chart actually plots,
    and outside its own baseline window."""
    page = loaded["process"]
    horizon = loaded["dataset"]["horizon_days"]
    checked = 0
    for signal in page["signals"][:25]:
        chart = workspace.control_chart(layer, signal["channel"],
                                        signal["entity"]["id"], horizon,
                                        page["signals"])
        assert chart is not None, signal
        assert signal["day_index"] in chart["days"]
        assert signal["day_index"] >= chart["baseline_last_day"]
        assert chart["inflation"] >= 1.0
        assert len(chart["upper"]) == len(chart["days"]) == len(chart["lower"])
        checked += 1
    assert checked >= 5, "too few signals to have checked anything"


def test_a_charted_point_outside_the_limits_is_a_reported_signal(layer,
                                                                 loaded):
    """The mirror of the test above: the drawn limits must be the limits the
    single-point rule used, so every point outside them is flagged."""
    page = loaded["process"]
    horizon = loaded["dataset"]["horizon_days"]
    signal = page["signals"][0]
    chart = workspace.control_chart(layer, signal["channel"],
                                    signal["entity"]["id"], horizon,
                                    page["signals"])
    flagged = {s["day_index"] for s in chart["signals"]
               if s["rule"] == "we1_beyond_3_sigma"}
    outside = {day for day, value, high, low
               in zip(chart["days"], chart["values"], chart["upper"],
                      chart["lower"])
               if day >= chart["baseline_last_day"]
               and (value > high or value < low)}
    assert outside == flagged, (outside ^ flagged)


def test_the_wafer_detail_reconciles_with_its_own_yield_row(layer, loaded):
    wafer = loaded["wafers"][0]["wafer_id"]
    detail = workspace.wafer_detail(layer, wafer)
    assert detail["runs"], "a tested wafer with no route"
    assert len(detail["die"]) == detail["total_die"]
    assert sum(1 for d in detail["die"] if d["bin_code"] == "PASS") == \
        detail["good_die"]
    assert len(detail["defects"]) == next(
        row["defects"] for row in loaded["wafers"]
        if row["wafer_id"] == wafer)
    steps = [row["step_sequence"] for row in detail["runs"]]
    assert steps == sorted(steps)


def test_the_equipment_page_carries_health_and_its_caveat(loaded):
    page = loaded["equipment"]
    assert page["health"]
    for record in page["health"].values():
        assert 0.0 <= record["utilization"] <= 1.0
        assert record["mttr_hours"] >= 0.0
    assert "repair is not evidence of a fault" in page["note"]


def test_the_yield_page_ranks_lots_worst_first_and_keeps_the_caveat(loaded):
    page = loaded["yield"]
    attainment = [row["mean_attainment_pts"] for row in page["lots"]]
    assert attainment == sorted(attainment)
    assert "ADR-028" in page["chamber_note"]


def test_the_investigation_page_is_the_decision_artifact(loaded,
                                                         demo_dataset):
    from fabops.report import build_report

    assert loaded["investigation"] == build_report(
        demo_dataset["db_path"]).to_dict()


def test_the_defect_page_assembles_measurements_that_already_existed(
        loaded, layer):
    """The page the product added, and the rule it was added under: every
    number on it is one the defect monitor already computed and the workspace
    was throwing away. If this page ever computes something of its own, this
    assertion is what has to be edited to allow it."""
    page = loaded["defect"]
    measurements = loaded["monitor"]["measurements"]["defect"]

    assert page["class_pareto"] == measurements["class_pareto"]
    assert page["signature_leaders"] == measurements["signature_leaders"]
    assert page["wafers_scored"] == measurements["wafers_scored"]
    assert page["signals"] == [s for s in loaded["monitor"]["signals"]
                               if s["family"] == "defect"]

    # …and the one query it shares with the equipment page is literally the
    # same query, so the two pages cannot start disagreeing about a rate.
    shared = workspace.defects_per_wafer(layer)
    assert loaded["equipment"]["defects_per_wafer"] == shared
    assert {row["chamber_label"]: row["defects_per_wafer"]
            for row in page["per_chamber"]} == {
        name: round(rate, 3) for name, rate in shared.items()}

    rates = [row["defects_per_wafer"] for row in page["per_chamber"]]
    assert rates == sorted(rates, reverse=True)
    assert "exposure and not" in page["per_chamber_note"]


# ------------------------------------------------------------- the figures


def test_every_figure_renders(layer, loaded, tmp_path):
    today = loaded["fab_today"]
    made = [figures.attainment_trend(today["weekly_trend"]),
            figures.product_attainment(today["products"])]

    signal = loaded["process"]["signals"][0]
    chart = workspace.control_chart(layer, signal["channel"],
                                    signal["entity"]["id"],
                                    loaded["dataset"]["horizon_days"],
                                    loaded["process"]["signals"])
    made.append(figures.control_chart_figure(chart))

    detail = workspace.wafer_detail(layer, loaded["wafers"][0]["wafer_id"])
    made.append(figures.wafer_map(detail))
    made.append(figures.die_map(detail))

    chamber = sorted(loaded["equipment"]["health"])[0]
    made.append(figures.state_timeline_figure(
        workspace.state_timeline(layer, chamber),
        loaded["dataset"]["horizon_days"]))

    for index, figure in enumerate(made):
        target = tmp_path / f"figure_{index}.png"
        figure.savefig(target, dpi=70)
        assert target.stat().st_size > 1000
        figure.clf()


# ------------------------------------- the legacy dashboard, still separate


def test_the_legacy_dashboard_is_untouched_and_separate():
    """ADR-010: the demo is deleted from no surface until its replacement is
    strictly better *on that surface*. The product reads schema v2 datasets and
    this reads the schema v1 database, so they answer about different fabs and
    the product is not its replacement — it is reachable from the product
    (`fabops-app --legacy`) and is not merged into it."""
    legacy = REPO / "app" / "ops_dashboard.py"
    assert legacy.exists()
    assert "DEMO_SUSPECT_TOOL" in legacy.read_text(encoding="utf-8")
    assert not (REPO / "app" / "investigation_workspace.py").exists(), (
        "the superseded v2 workspace is back; its pages live in fabapp.ui "
        "and two v2 entry points is the duplication ADR-037 removed")
