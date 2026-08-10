"""
Impact, containment, recommendations, and the artifact that carries them.

The failure this stage is prone to is not a crash — it is a confident number.
A die-loss figure printed without its standard error, or without the spread the
fab's own healthy chambers show, is the audited v1's mistake wearing decimals.
So the tests here are mostly about what the artifact *refuses* to say:

* it names no subject when the evidence names none;
* it records whether a subject was concluded or supplied;
* it reports a deficit against benign variation, not against zero;
* and its recommendation table is data, loadable and replaceable, with an
  invalid replacement reported rather than swallowed.
"""
from __future__ import annotations

import json
import statistics as st

import pytest

from fabops.actions import (DEFAULT_KNOWLEDGE_PATH, KNOWLEDGE_SCHEMA,
                            load_knowledge, recommend)
from fabops.impact import (MIN_EXPOSED_WAFERS, STANDING_LIMIT, estimate_loss,
                           exposed_wafers, lot_exposure)
from fabops.report import REPORT, REPORT_SCHEMA, build_report
from fabops.semantic import open_layer


@pytest.fixture(scope="module")
def layer(demo_dataset):
    connection = open_layer(demo_dataset["db_path"])
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def busiest(layer):
    """The chamber with the most exposure at the busiest etch step."""
    step, chamber = layer.execute(
        "SELECT step_name, chamber_label FROM v_chamber_exposure "
        "WHERE operation_type = 'ETCH' "
        "ORDER BY wafers DESC, chamber_label LIMIT 1").fetchone()
    return str(step), str(chamber)


# ------------------------------------------------------------------ exposure


def test_lot_exposure_is_ranked_and_bounded(layer, busiest):
    step, chamber = busiest
    rows = lot_exposure(layer, "chamber", chamber, step)
    assert rows, "the busiest chamber at the busiest step exposed no lots"
    shares = [row.share for row in rows]
    assert shares == sorted(shares, reverse=True)
    for row in rows:
        assert 0.0 < row.share <= 1.0
        assert row.exposed_wafers <= row.lot_wafers


def test_exposure_reconciles_with_the_wafer_list(layer, busiest):
    step, chamber = busiest
    wafers = exposed_wafers(layer, "chamber", chamber, step)
    rows = lot_exposure(layer, "chamber", chamber, step)
    assert len(wafers) == sum(row.exposed_wafers for row in rows)
    assert len(set(wafers)) == len(wafers)


def test_an_unknown_subject_kind_is_refused(layer):
    with pytest.raises(ValueError, match="unknown subject kind"):
        lot_exposure(layer, "constellation", "X")


# -------------------------------------------------------------------- loss


def test_the_deficit_matches_the_semantic_layers_own_view(layer, busiest):
    """Two definitions of one quantity is the defect this asserts away. The
    view and the estimator are separate code and must agree to the last bits a
    different summation order can move."""
    step, chamber = busiest
    estimate = estimate_loss(layer, "chamber", chamber, step)
    row = layer.execute(
        "SELECT deficit_pts, wafers FROM v_chamber_yield_deficit "
        "WHERE step_name = ? AND chamber_label = ?", (step, chamber)
    ).fetchone()
    assert row is not None
    assert estimate.deficit_pts == pytest.approx(float(row[0]), rel=1e-9)
    assert estimate.exposed_wafers == int(row[1])


def test_the_estimate_carries_its_uncertainty_and_its_reference(layer,
                                                                busiest):
    step, chamber = busiest
    estimate = estimate_loss(layer, "chamber", chamber, step)
    payload = estimate.to_dict()
    assert payload["standard_error_pts"] > 0
    assert payload["peers_compared"] >= 2
    assert "distinguishable_from_benign_variation" in payload
    assert payload["distinguishable_from_benign_variation"] is (
        abs(estimate.standing_z) >= STANDING_LIMIT)
    assert payload["per_product"], "no product survived the support floors"
    for entry in payload["per_product"]:
        assert entry["exposed_wafers"] >= MIN_EXPOSED_WAFERS


def test_the_die_delta_follows_the_sign_of_the_deficit(layer, busiest):
    step, chamber = busiest
    estimate = estimate_loss(layer, "chamber", chamber, step)
    assert (estimate.die_delta >= 0) == (estimate.deficit_pts >= 0)
    assert abs(estimate.die_delta) <= estimate.exposed_die
    assert estimate.optimistic_recoverable_die >= 0


def test_a_planted_shortfall_is_recovered_by_the_estimator(layer, busiest,
                                                           demo_dataset,
                                                           tmp_path):
    """The mirror: the estimator must move when the fab moves.

    Ten points are removed from every wafer the subject processed, in a copy of
    the database. The deficit must come back close to ten points and the
    standing must clear the limit — otherwise the zero-ish numbers this
    estimator reports on the real library would be zeros it cannot leave.
    """
    import shutil
    import sqlite3

    step, chamber = busiest
    victim = tmp_path / "planted.db"
    shutil.copy(demo_dataset["db_path"], victim)

    wafers = exposed_wafers(layer, "chamber", chamber, step)
    connection = sqlite3.connect(str(victim))
    try:
        connection.executemany(
            "UPDATE wafer_yield SET yield_pct = yield_pct - 10.0 "
            "WHERE wafer_id = ?", [(wafer,) for wafer in wafers])
        connection.commit()
    finally:
        connection.close()

    hurt = open_layer(victim)
    try:
        estimate = estimate_loss(hurt, "chamber", chamber, step)
    finally:
        hurt.close()

    assert estimate.deficit_pts < -8.0, estimate.deficit_pts
    assert estimate.die_delta < 0
    assert estimate.distinguishable, (
        "a ten-point planted shortfall did not clear the benign-variation "
        "limit; the standing reference is not doing its job")


# --------------------------------------------------------------- knowledge


def test_the_built_in_table_is_valid_and_covers_every_family():
    from fabops.diagnosis.channels import FAMILIES

    table = load_knowledge()
    assert set(table.families) == set(FAMILIES), (
        "the recommendation table and the engine disagree about which evidence "
        "families exist")
    assert set(table.per_family) == set(FAMILIES)
    assert table.always and table.insufficient_evidence
    payload = json.loads(DEFAULT_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    assert payload["schema"] == KNOWLEDGE_SCHEMA


def test_an_absent_override_is_the_ordinary_case(tmp_path):
    """`FABOPS_VS_FABKG_BOUNDARY.md` §4: this repository must build and run
    with the other project entirely absent."""
    table = load_knowledge(tmp_path / "nothing-here.json")
    assert table.source == "built-in"
    assert not table.rejected


def test_an_invalid_override_falls_back_and_says_so(tmp_path):
    """Present-and-invalid is not fatal, per the contract — but it is not
    silent either, or a fab could run for months on a table that never loaded."""
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"schema": "somebody.else/v9"}),
                      encoding="utf-8")
    table = load_knowledge(broken)
    assert table.source == "built-in"
    assert "broken.json" in table.rejected
    assert "somebody.else/v9" in table.rejected


def test_a_valid_override_replaces_the_built_in_table(tmp_path):
    payload = json.loads(DEFAULT_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    payload["source"] = "an-external-supplier"
    payload["version"] = "9.9.9"
    override = tmp_path / "supplied.json"
    override.write_text(json.dumps(payload), encoding="utf-8")
    table = load_knowledge(override)
    assert table.source == "an-external-supplier"
    assert table.version == "9.9.9"
    assert not table.rejected


def test_an_override_naming_an_unknown_family_is_rejected(tmp_path):
    payload = json.loads(DEFAULT_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    payload["per_family"]["telepathy"] = {"checks": ["ask the tool nicely"]}
    override = tmp_path / "extra.json"
    override.write_text(json.dumps(payload), encoding="utf-8")
    table = load_knowledge(override)
    assert "telepathy" in table.rejected


#: The mechanism and latent vocabulary. Declared once so the scan and the
#: mutation that proves it works are the same expression.
MECHANISM_WORDS = ("chamber_edge_uniformity", "param_drift",
                   "particle_excursion", "benign_offset", "edge_uniformity",
                   "particle_load", "param_bias")


def mechanism_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [word for word in MECHANISM_WORDS if word in lowered]


def test_no_recommendation_names_a_mechanism():
    """`DIAGNOSIS_CONTRACT.md` §5.2: no observable channel identifies which
    mechanism acted, so nothing downstream may claim one. The table maps
    evidence onto *checks*, which is what the boundary document permits."""
    assert not mechanism_hits(
        DEFAULT_KNOWLEDGE_PATH.read_text(encoding="utf-8"))


def test_the_mechanism_scan_fires_on_a_table_that_names_one(tmp_path):
    """The mirror of the test above. A knowledge table that named the
    mechanism library would be matching a catalogue, and the scan that forbids
    it has to be able to catch one."""
    payload = json.loads(DEFAULT_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    payload["per_family"]["metrology"]["checks"].append(
        "Suspect a chamber_edge_uniformity fault and confirm it.")
    poisoned = tmp_path / "poisoned.json"
    poisoned.write_text(json.dumps(payload), encoding="utf-8")

    hits = mechanism_hits(poisoned.read_text(encoding="utf-8"))
    assert "chamber_edge_uniformity" in hits, "the mutation did not take"

    # It is schema-valid, and that is the point: the loader accepts it without
    # complaint, so the *scan* is the only thing standing between this
    # repository and a mechanism catalogue.
    accepted = load_knowledge(poisoned)
    assert not accepted.rejected
    assert accepted.source != "built-in" or accepted.version


def test_an_empty_signature_recommends_no_subject_specific_action():
    actions = recommend((), subject=None)
    assert actions
    assert {action.kind for action in actions} == {"context"}
    assert any("insufficient" in action.text.lower()
               or "no containment action" in action.text.lower()
               for action in actions)


def test_a_signature_produces_checks_and_containment():
    actions = recommend(("metrology", "fdc"), subject="TOOL-01/A")
    kinds = {action.kind for action in actions}
    assert {"check", "containment", "context"} <= kinds
    assert all("TOOL-01/A" in action.because or "every investigation"
               in action.because for action in actions)
    texts = [action.text for action in actions]
    assert len(texts) == len(set(texts)), "the same action was listed twice"


def test_an_indistinguishable_impact_adds_its_caution():
    with_caution = recommend(("yield",), subject="X", distinguishable=False)
    without = recommend(("yield",), subject="X", distinguishable=True)
    assert len(with_caution) > len(without)
    assert any("upper bound" in action.text for action in with_caution)


# ------------------------------------------------------------------ report


def test_the_report_abstains_with_no_subject_when_the_engine_does(demo_dataset):
    """The property the whole project exists to protect, at the last stage
    where it would be easiest to lose: a document that feels incomplete
    without a name must not supply one."""
    report = build_report(demo_dataset["db_path"])
    payload = report.to_dict()
    assert payload["schema"] == REPORT_SCHEMA
    assert payload["generated_by"] == REPORT
    if payload["investigation"]["insufficient_evidence"]:
        assert payload["subject"] is None
        assert payload["impact"] is None
        assert payload["containment"] is None
        assert payload["actions"]
        assert {a["kind"] for a in payload["actions"]} == {"context"}
    else:                                                # pragma: no cover
        assert payload["subject"]["source"] == "engine"


def test_a_supplied_subject_is_recorded_as_supplied(demo_dataset, busiest):
    _step, chamber = busiest
    report = build_report(demo_dataset["db_path"], subject=chamber)
    payload = report.to_dict()
    assert payload["subject"]["id"] == chamber
    assert payload["subject"]["source"] == "operator"
    assert "does not claim the evidence names it" in payload["subject"]["note"]
    assert payload["impact"] is not None
    assert payload["containment"]["lots_ranked_by_exposure"]


def test_the_report_round_trips_through_json(demo_dataset, busiest):
    _step, chamber = busiest
    report = build_report(demo_dataset["db_path"], subject=chamber)
    assert json.loads(report.to_json()) == report.to_dict()


def test_the_embedded_investigation_is_the_engines_own_artifact(demo_dataset):
    """The wrapper must not edit what it wraps: other systems version against
    `fabops.investigation/v1` (ADR-008), and a report that adjusted it would
    make one schema name mean two things."""
    from fabops.diagnosis import INVESTIGATION_SCHEMA, diagnose

    report = build_report(demo_dataset["db_path"])
    assert report.investigation == diagnose(demo_dataset["db_path"]).to_dict()
    assert report.investigation["schema"] == INVESTIGATION_SCHEMA


def test_the_provenance_names_every_component_that_produced_a_number(
        demo_dataset, busiest):
    _step, chamber = busiest
    provenance = build_report(demo_dataset["db_path"],
                              subject=chamber).provenance
    assert provenance["engine"].startswith("fabops.diagnosis/")
    assert provenance["semantic_layer"].startswith("fabops.semantic/")
    assert provenance["impact"].startswith("fabops.impact/")
    assert provenance["knowledge"]["schema"] == KNOWLEDGE_SCHEMA


def test_the_step_is_chosen_by_exposure_and_not_by_outcome(demo_dataset,
                                                           layer, busiest):
    """Choosing the step where the deficit came out worst would be a
    per-subject maximization — the selection ADR-029 §2 measured and rejected
    for the engine's anchors."""
    _step, chamber = busiest
    report = build_report(demo_dataset["db_path"], subject=chamber)
    chosen = report.subject["step_name"]
    exposure = {step: wafers for step, wafers in layer.execute(
        "SELECT step_name, wafers FROM v_chamber_exposure "
        "WHERE chamber_label = ? ORDER BY step_name", (chamber,))}
    assert chosen == max(sorted(exposure), key=lambda s: exposure[s])
