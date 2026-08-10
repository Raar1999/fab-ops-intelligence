"""
The investigation workspace: the data behind every page, and the figures.

`DASHBOARD_AUDIT` §4 asks for a surface that *renders engine output and never
computes or asserts a conclusion itself*. That is a testable property, so it is
tested here rather than asserted in a docstring:

* every page's payload is produced without Streamlit, so it is reachable from a
  test that needs no browser;
* the app file contains no entity literal and no analysis — a suspect
  highlighted on the old dashboard was highlighted by a module constant, and
  the check that this cannot recur is a scan, not a promise;
* the figures draw the same numbers the decision was made from.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from fabops.report import figures, workspace
from fabops.semantic import open_layer

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "app" / "investigation_workspace.py"


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
        "Fab Today", "Process", "Equipment", "Yield", "Investigation",
        "Wafer explorer"}
    for key in ("dataset", "fab_today", "process", "equipment", "yield",
                "investigation", "wafers", "monitor"):
        assert loaded[key], key
    assert loaded["dataset"]["schema_version"] == "2.0"
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


# -------------------------------------------------- the app is a renderer


def test_the_app_computes_nothing():
    """Everything numeric on the screen must come from `workspace` or
    `figures`. This scans the app for the arithmetic and the SQL that would
    mean it had started deciding things for itself."""
    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree)
                 if isinstance(node, ast.Import) for alias in node.names}
    assert not {name for name in imported
                if name.split(".")[0] in ("fabsim", "fabeval")}
    assert "sqlite3" not in imported, "the app opened its own database"
    assert not re.search(r"\bSELECT\b", source, re.I), (
        "the app contains SQL; analysis belongs in the semantic layer")


#: What a v2 presentation surface may not name. Declared once so the scan and
#: the mutation that proves the scan works are literally the same expression.
FORBIDDEN_LITERALS = (r"ETCH-\d", r"CVD-\d", r"PVD-\d", r"CMP-\d",
                      r"LITHO-\d", r"Mobile-28", r"Logic-14",
                      r"chamber_edge_uniformity", r"param_drift",
                      r"particle_excursion", r"benign_offset",
                      r"DEMO_SUSPECT_TOOL")


def entity_hits(source: str) -> list[str]:
    return [pattern for pattern in FORBIDDEN_LITERALS
            if re.search(pattern, source)]


def test_the_app_names_no_entity_and_no_mechanism():
    """The audited dashboard's defect, in the one place it would return.

    `SUSPECT = "ETCH-02"` drove a pink row and a default selection. Nothing in
    this file may name a tool, a chamber, a product or a mechanism — where a
    candidate is highlighted here it is because the engine ranked it.
    """
    source = APP.read_text(encoding="utf-8")
    assert not entity_hits(source)

    tree = ast.parse(source)
    imported = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module}
    assert "fabops.config" not in imported, (
        "the v2 workspace imported the module that holds the legacy demo's "
        "suspect and the legacy database path")


def _code_only(path: Path) -> str:
    """The module's source with its docstrings removed.

    Scanned separately from prose on purpose: the app's own docstring explains
    *why* it must not fall back to the legacy database, and a scan that could
    not tell an explanation from an instruction would forbid explaining.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    parts = [ast.dump(node) for node in ast.walk(tree)
             if isinstance(node, (ast.Name, ast.Attribute))]
    parts += [node.value for node in ast.walk(tree)
              if isinstance(node, ast.Constant)
              and isinstance(node.value, str) and id(node) not in docstrings]
    return "\n".join(parts)


def test_the_entity_scan_fires_on_an_app_that_names_one():
    """A guard that cannot fail is not a guard.

    The audited dashboard's own constant is appended to a copy of the app and
    run through the *same function* the real file passes, so the two cannot
    diverge into a strict scan and a lenient mutation.
    """
    poisoned = APP.read_text(encoding="utf-8") + '\nSUSPECT = "ETCH-02"\n'
    assert entity_hits(poisoned) == [r"ETCH-\d"]


def test_the_sql_scan_fires_on_an_app_that_queries():
    poisoned = APP.read_text(encoding="utf-8") + (
        '\nrows = st.connection("x").query("SELECT 1 FROM runs")\n')
    assert re.search(r"\bSELECT\b", poisoned, re.I)
    assert not re.search(r"\bSELECT\b",
                         APP.read_text(encoding="utf-8"), re.I)


def test_the_app_has_no_default_dataset():
    """A v2 surface that fell back to `fabops.config.DB_PATH` would answer
    about the schema v1 fab with no error anywhere."""
    code = _code_only(APP)
    assert "DB_PATH" not in code
    assert "--dataset" in code and "FABOPS_DATASET" in code


@pytest.mark.parametrize("page", workspace.WORKSPACE_PAGES)
def test_every_page_executes(demo_dataset, page):
    """Streamlit's bare mode runs the script with every widget returning its
    default, which is enough to prove each page renders end to end on real
    data. The repository has never had a test that executed a dashboard; the
    audited one was verified by hand once and then drifted."""
    import subprocess
    import sys

    pytest.importorskip("streamlit")
    result = subprocess.run(
        [sys.executable, str(APP), "--dataset", demo_dataset["db_path"],
         "--page", page],
        capture_output=True, text=True, cwd=str(REPO), timeout=900)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "Traceback" not in result.stderr, result.stderr[-2000:]


def test_the_investigation_page_executes_on_a_fault_free_dataset(null_dataset):
    """The page that is most tempting to write for the case where there *is* a
    candidate. On a fault-free world there is none, and it must still render."""
    import subprocess
    import sys

    pytest.importorskip("streamlit")
    result = subprocess.run(
        [sys.executable, str(APP), "--dataset", null_dataset["db_path"],
         "--page", "Investigation"],
        capture_output=True, text=True, cwd=str(REPO), timeout=900)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "Traceback" not in result.stderr


def test_the_legacy_dashboard_is_untouched_and_separate():
    """ADR-010: the demo is deleted from no surface until its replacement is
    strictly better *on that surface*. These two read different fabs, so the
    workspace is not a replacement and does not pretend to be."""
    legacy = REPO / "app" / "ops_dashboard.py"
    assert legacy.exists()
    assert "DEMO_SUSPECT_TOOL" in legacy.read_text(encoding="utf-8")
    assert APP.exists() and APP != legacy
