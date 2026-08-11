"""
The explainer restates the artifact and never adds to it.

Every tick and cross on the Investigation screen comes from here, so the thing
worth testing is not that the checks are *nice* but that each one moves when
the field it claims to read moves, and only then. A checklist that stayed green
while the artifact changed underneath it would be the audited defect returning
in a more persuasive form — prose that tells the reader what to conclude.

The tests are therefore mostly mutations: take a real artifact, change one
field, and require exactly the corresponding check to flip.
"""
from __future__ import annotations

import copy

import pytest

from fabapp import explain
from fabapp.service import open_dataset


@pytest.fixture(scope="module")
def artifact(faulted):
    return open_dataset(faulted.record)["investigation"]["investigation"]


@pytest.fixture(scope="module")
def candidate(artifact):
    assessed = [c for c in artifact["candidates"] if c["status"] == "assessed"]
    assert assessed, "no candidate was scored; the mutations have no subject"
    return assessed[0]


def check_named(checks, claim):
    return next(check for check in checks if check.claim == claim)


# ------------------------------------------------------------ the outcome


def test_the_outcome_is_the_engines_own_verdict(artifact):
    outcome = explain.explain_investigation(artifact)
    assert outcome.insufficient_evidence == artifact["insufficient_evidence"]
    assert outcome.p_familywise == artifact["abstention"]["p_familywise"]
    assert outcome.alpha == artifact["abstention"]["alpha"]
    assert outcome.reason == artifact["abstention"]["reason"]
    assert outcome.permutations == artifact["abstention"]["permutations"]
    assert outcome.assessed + outcome.not_assessable == len(
        artifact["candidates"])


def test_the_headline_says_insufficient_evidence_when_the_engine_abstains(
        artifact):
    """The abstention has to survive the trip to a screen intact.

    At its declared level this engine abstains on every dataset the project can
    build, so this is the case the product spends most of its life in, and the
    one where an interface is most tempted to show the top of the ranking as
    though it were an answer.
    """
    outcome = explain.explain_investigation(artifact)
    if artifact["insufficient_evidence"]:
        assert "Insufficient evidence" in outcome.headline
        assert "no candidate is offered" in outcome.headline.lower()
    else:
        assert "leading candidate" in outcome.headline.lower()


def test_a_flipped_abstention_flips_the_headline(artifact):
    mutated = copy.deepcopy(artifact)
    mutated["insufficient_evidence"] = not artifact["insufficient_evidence"]
    before = explain.explain_investigation(artifact).headline
    after = explain.explain_investigation(mutated).headline
    assert before != after


def test_the_reasons_a_candidate_could_not_be_scored_are_grouped(artifact):
    outcome = explain.explain_investigation(artifact)
    refused = [c for c in artifact["candidates"]
               if c["status"] == "not_assessable"]
    assert outcome.not_assessable == len(refused)
    assert sum(count for _reason, count
               in outcome.not_assessable_reasons) == len(refused)
    if refused:
        assert all(reason for reason, _count
                   in outcome.not_assessable_reasons)


# ---------------------------------------------------------- the checklists


def test_every_declared_criterion_is_produced(artifact, candidate):
    explanation = explain.explain_candidate(artifact, candidate)
    assert tuple(c.claim for c in explanation.considered) == \
        explain.CONSIDERATION_CRITERIA
    assert tuple(c.claim for c in explanation.attribution) == \
        explain.ATTRIBUTION_CRITERIA


def test_every_check_cites_the_field_it_read(artifact, candidate):
    explanation = explain.explain_candidate(artifact, candidate)
    for check in (*explanation.considered, *explanation.attribution):
        assert check.source, check.claim
        assert check.evidence, check.claim


def test_family_wise_significance_reads_the_same_flag_the_cli_prints(
        artifact, candidate):
    """The screen and `fabops-diagnose` must not be able to disagree."""
    explanation = explain.explain_candidate(artifact, candidate)
    check = check_named(explanation.attribution, "family-wise significance")
    assert check.met is not artifact["insufficient_evidence"]

    mutated = copy.deepcopy(artifact)
    mutated["insufficient_evidence"] = not artifact["insufficient_evidence"]
    flipped = check_named(
        explain.explain_candidate(mutated, candidate).attribution,
        "family-wise significance")
    assert flipped.met is not check.met


def test_removing_the_onsets_removes_the_temporal_claim(artifact, candidate):
    mutated = copy.deepcopy(candidate)
    mutated["onsets"] = []
    check = check_named(
        explain.explain_candidate(artifact, mutated).considered,
        "temporal departure located")
    assert not check.met
    assert "no anchor" in check.evidence


def test_removing_the_evidence_removes_the_family_claim(artifact, candidate):
    mutated = copy.deepcopy(candidate)
    mutated["evidence"] = []
    considered = explain.explain_candidate(artifact, mutated).considered
    assert not check_named(considered, "relevant evidence family").met
    assert not check_named(considered, "sufficient peer structure").met


def test_a_not_assessable_candidate_carries_the_engines_own_reason(artifact):
    refused = [c for c in artifact["candidates"]
               if c["status"] == "not_assessable"]
    if not refused:
        pytest.skip("every candidate was assessable on this dataset")
    explanation = explain.explain_candidate(artifact, refused[0])
    check = check_named(explanation.considered, "sufficient peer structure")
    assert not check.met
    assert check.evidence == refused[0]["reason"]


def test_cross_family_agreement_needs_two_families(artifact, candidate):
    mutated = copy.deepcopy(candidate)
    for entry in mutated["evidence"]:
        entry["rank"] = 1
    families = {entry["family"] for entry in mutated["evidence"]}
    check = check_named(
        explain.explain_candidate(artifact, mutated).attribution,
        "cross-family agreement")
    assert check.met is (len(families) >= 2)

    for entry in mutated["evidence"]:
        entry["rank"] = 4
    lowered = check_named(
        explain.explain_candidate(artifact, mutated).attribution,
        "cross-family agreement")
    assert not lowered.met


def test_candidate_significance_is_read_against_the_declared_level(
        artifact, candidate):
    alpha = artifact["abstention"]["alpha"]
    mutated = copy.deepcopy(candidate)

    mutated["p_value"] = alpha / 2.0
    assert check_named(
        explain.explain_candidate(artifact, mutated).attribution,
        "candidate-level significance").met

    mutated["p_value"] = min(alpha * 2.0, 1.0)
    assert not check_named(
        explain.explain_candidate(artifact, mutated).attribution,
        "candidate-level significance").met


def test_an_attributable_candidate_needs_every_attribution_check(artifact,
                                                                 candidate):
    """`attributable` is an AND over the four, so a screen cannot report a
    conclusion the engine did not reach."""
    explanation = explain.explain_candidate(artifact, candidate)
    assert explanation.attributable == all(
        check.met for check in explanation.attribution)
    if artifact["insufficient_evidence"]:
        assert not explanation.attributable


def test_the_explainer_needs_nothing_but_the_artifact(artifact, candidate):
    """No database, no scenario, no path — the explainer is a pure function of
    `fabops.investigation/v1`, which is what makes it testable by mutation and
    unable to consult anything the engine did not produce."""
    import inspect as inspection

    for function in (explain.explain_investigation, explain.explain_candidate):
        parameters = list(inspection.signature(function).parameters)
        assert parameters[0] == "artifact"
        assert not {"db_path", "record", "scenario", "dataset", "seed"} & set(
            parameters)

    payload = explain.explain_candidate(artifact, candidate).to_dict()
    assert "truth" not in str(payload).lower()
