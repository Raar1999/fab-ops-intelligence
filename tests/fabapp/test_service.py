"""
The product chain end to end, and the one invariance that makes it blind.

`workflow_check` is the whole user journey with the browser taken out:
discover, open, seven pages of data, the engine's verdict, the export. The
end-to-end test is that command, because a check somebody can type is worth
more than an assertion only the suite can make.

The invariance is the important one. The product knows which scenario a user
picked — it has to, it built the dataset — and the engine must not. That is
easy to promise and easy to break, because the two live in one process here for
the first time. So it is measured: the rendered investigation is compared
between a run where the scenario resolves and a run where it cannot, and the
two must be identical to the byte.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabapp import registry, service

REPO = Path(__file__).resolve().parents[2]


def test_opening_a_dataset_produces_every_pages_payload(faulted):
    payload = service.open_dataset(faulted.record)
    for page, key in service.PAGE_PAYLOADS.items():
        assert payload.get(key), page
    assert set(service.PAGE_PAYLOADS) == set(service.WORKSPACE_PAGES)
    assert payload["product"].startswith("fabapp/")


def test_opening_accepts_a_path_and_checks_it_first(faulted):
    assert service.open_dataset(str(faulted.record.db_path))["dataset"]


def test_an_unusable_dataset_is_refused_with_the_registrys_reason(tmp_path):
    with pytest.raises(service.DatasetNotUsable) as raised:
        service.open_dataset(tmp_path / "nothing" / "fab.db")
    assert "missing" in str(raised.value)
    assert raised.value.record.status == registry.MISSING


def test_the_legacy_database_cannot_be_opened_by_the_product():
    legacy = REPO / "data" / "fab.db"
    if not legacy.is_file():
        pytest.skip("the legacy v1 database has not been built here")
    with pytest.raises(service.DatasetNotUsable) as raised:
        service.open_dataset(legacy)
    assert "not a schema v2 dataset" in str(raised.value)


def test_the_workflow_check_runs_the_whole_chain(populated_root):
    result = service.workflow_check(root=populated_root)
    assert result.datasets_found == result.datasets_ready == 2
    assert result.dataset_id.startswith("scn-")
    assert result.pages == service.WORKSPACE_PAGES
    assert result.artifact_bytes > 1000
    assert result.outcome.headline
    described = result.describe()
    assert "diagnosis" in described and result.dataset_id in described
    assert json.loads(json.dumps(result.to_dict()))


def test_the_workflow_check_refuses_an_empty_root(tmp_path):
    with pytest.raises(service.DatasetNotUsable) as raised:
        service.workflow_check(root=tmp_path)
    assert "create one first" in str(raised.value)


def test_the_export_is_the_same_document_the_command_line_writes(faulted):
    from fabops.report import build_report

    name, text = service.investigation_artifact(faulted.record.db_path)
    assert name.endswith(".fabops-report.json")
    assert faulted.record.dataset_id in name
    payload = json.loads(text)
    assert payload["schema"] == "fabops.report/v1"
    assert payload == build_report(faulted.record.db_path).to_dict()
    assert payload["investigation"]["schema"] == "fabops.investigation/v1"


def test_a_supplied_subject_is_recorded_as_supplied(faulted):
    """The distinction `fabops.report` draws and the product must not blur: a
    subject a human named is a different claim from one the engine concluded."""
    artifact = service.open_dataset(
        faulted.record)["investigation"]["investigation"]
    chosen = service.subject_candidates(artifact)[0]

    _name, text = service.investigation_artifact(
        faulted.record.db_path, subject=chosen)
    subject = json.loads(text)["subject"]
    assert subject["id"] == chosen
    assert subject["source"] == "operator"
    assert "does not claim the evidence names it" in subject["note"]


def test_supplying_a_subject_fills_the_panels_the_abstention_leaves_empty(
        faulted):
    """The product gap this closes.

    The engine abstains on every dataset this project can build, so it names no
    subject, so impact, containment and the recommended checks are `null` on
    every screen. An engineer asking "assume it is this one — what would acting
    on it cost?" is the supported way to fill them, and it must not disturb the
    engine's own verdict.
    """
    without = service.decision_for(faulted.record.db_path)
    artifact = without["investigation"]
    assert without["subject"] is None
    assert without["impact"] is None and without["containment"] is None

    chosen = service.subject_candidates(artifact)[0]
    with_subject = service.decision_for(faulted.record.db_path, subject=chosen)

    assert with_subject["impact"] is not None
    assert with_subject["containment"]["lots_ranked_by_exposure"]
    assert with_subject["actions"]
    assert with_subject["impact"]["exposed_wafers"] > 0

    # …and the investigation underneath is untouched: a supplied subject asks a
    # cost question, it does not re-run or move the engine's verdict.
    assert with_subject["investigation"] == artifact


def test_only_candidates_impact_can_be_computed_for_are_offered(faulted):
    """A product or an operator can be a candidate and cannot be a containment
    subject. Offering one would produce a panel that came back empty."""
    artifact = service.open_dataset(
        faulted.record)["investigation"]["investigation"]
    offered = set(service.subject_candidates(artifact))
    assert offered

    kinds = {c["entity"]["kind"] for c in artifact["candidates"]
             if c["entity"]["id"] in offered}
    assert kinds <= set(service.IMPACT_KINDS)

    scored = {c["entity"]["kind"] for c in artifact["candidates"]
              if c["status"] == "assessed"}
    assert scored - set(service.IMPACT_KINDS), (
        "every assessed candidate is impact-eligible on this dataset, so the "
        "filter has not been exercised")


def test_the_export_is_what_the_screen_is_showing(faulted):
    """`artifact_text` renders an already-built decision rather than building a
    second one, so a download cannot differ from the page above it."""
    decision = service.decision_for(faulted.record.db_path)
    name, text = service.artifact_text(decision)
    assert name.startswith(faulted.record.dataset_id)
    assert json.loads(text) == decision


# ------------------------------------------------------- the invariance


def test_the_investigation_is_identical_whether_or_not_the_scenario_resolves(
        faulted, monkeypatch, tmp_path):
    """The product knows the scenario; the engine must not.

    The mechanism the product uses to display a scenario is a lookup against
    the local library, so pointing that library somewhere empty removes the
    product's knowledge of what it built while changing nothing about the
    dataset. Every byte of the rendered investigation must survive that — if
    any of it moved, something on the analysis path had reached the scenario.
    """
    known = service.open_dataset(faulted.record)
    assert registry.inspect(faulted.record.db_path).scenario == \
        "chamber_edge_uniformity"

    empty = tmp_path / "no-library"
    empty.mkdir()
    monkeypatch.setenv("FABOPS_SCENARIO_ROOT", str(empty))
    blind_record = registry.inspect(faulted.record.db_path)
    assert blind_record.scenario is None, (
        "the scenario still resolved; the invariance has not been exercised")

    blind = service.open_dataset(blind_record)
    assert json.dumps(blind["investigation"], sort_keys=True) == \
        json.dumps(known["investigation"], sort_keys=True)
    assert service.outcome_of(blind).to_dict() == \
        service.outcome_of(known).to_dict()


def test_the_verdict_does_not_depend_on_the_scenario_that_produced_it(
        faulted, null):
    """Both members reach the same declared level by the same route.

    Not an assertion that the two verdicts are equal — they are computed from
    different fabs — but that neither carries anything scenario-shaped: the
    engine, the level and the permutation count are the dataset-independent
    parts of the method, and they must match.
    """
    verdicts = {}
    for creation in (faulted, null):
        payload = service.open_dataset(creation.record)
        artifact = payload["investigation"]["investigation"]
        verdicts[creation.scenario] = (
            artifact["generated_by"], artifact["abstention"]["alpha"],
            artifact["abstention"]["permutations"],
            artifact["settings"]["statistic"])
    assert len(set(verdicts.values())) == 1, verdicts


def test_the_analysis_path_takes_a_database_path_and_nothing_else():
    """Structural. `open_dataset` may accept a record for its *status*, and
    what it passes onward is one path — so the scenario cannot ride along."""
    import inspect as inspection

    source = inspection.getsource(service.open_dataset)
    assert "load_workspace(record.db_path)" in source
    assert "scenario" not in source
