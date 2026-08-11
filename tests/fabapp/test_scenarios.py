"""
The scenario picker publishes build inputs and refuses answer material.

This is the product's most dangerous screen, and it is dangerous in a way that
is easy to miss: the scenario configurations are *right there*, they parse into
a rich object, and rendering more of that object makes a nicer picker. Every
field beyond the build inputs is a piece of the answer key — the prose says
which chamber is faulted, and an event count of zero is the complete answer to
the fault-free member.

So the projection is tested from both ends: that the six published fields are
what a screen gets, and that the words which would give the game away are
absent from what it gets even though they are present in the file it came from.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabapp import scenarios

REPO = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPO / "scenarios"


@pytest.fixture(scope="module")
def options():
    return scenarios.available(SCENARIO_ROOT)


def test_the_library_is_offered(options):
    slugs = [option.slug for option in options]
    assert len(slugs) >= 10, slugs
    assert slugs == sorted(slugs)
    assert len(set(slugs)) == len(slugs)
    on_disk = {path.stem for path in SCENARIO_ROOT.glob("*.json")}
    assert set(slugs) == on_disk


def test_an_option_carries_the_build_inputs_and_nothing_else(options):
    """The published surface, asserted as an exact set.

    An exact equality rather than a subset check: a field that arrives on this
    object arrives on every screen that renders it, so growing the list has to
    be a deliberate edit in a diff somebody reads.
    """
    for option in options:
        assert set(option.to_dict()) == set(scenarios.PUBLISHED_FIELDS)
        assert set(vars(option)) == set(scenarios.PUBLISHED_FIELDS), (
            "the option carries state beyond what it publishes")
        assert option.horizon_days > 0 and option.lots > 0
        assert option.scenario_id.startswith("scn-")


def test_the_picker_publishes_no_field_that_would_answer_the_question(options):
    """The fields that are *in* the configuration and must not come out.

    Every one of these is present in the file the option was built from, which
    is what makes this a real check rather than a restatement of the dataclass.
    """
    forbidden = ("description", "events", "distractors", "routing_conditions",
                 "mechanism", "target", "severity", "onset_day", "profile")
    for option in options:
        published = json.dumps(option.to_dict()).lower()
        for field in forbidden:
            assert field not in published, (option.slug, field)


def test_the_configurations_really_do_contain_what_the_picker_withholds():
    """The other end of the same check.

    If the configurations stopped naming their targets, the test above would
    keep passing while protecting nothing. This asserts the hazard is real: the
    prose names a chamber, and the event list names a mechanism.
    """
    named_targets = 0
    for path in sorted(SCENARIO_ROOT.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "description" in raw, path.name
        for event in raw.get("events", ()):
            assert "mechanism" in event and "target" in event
            named_targets += 1
    assert named_targets >= 10, (
        "no scenario names a target; the picker's projection would be "
        "protecting nothing")


def test_an_event_count_is_not_derivable_from_what_the_picker_offers(options):
    """The subtler leak, and the reason a count is withheld rather than shown.

    `null_baseline` and a faulted scenario must be indistinguishable from the
    published fields alone. They share a world, a horizon, a lot count and a
    default seed, so what separates them on this screen is only the slug the
    user themselves chose.
    """
    published = {option.slug: dict(option.to_dict()) for option in options}
    for record in published.values():
        record.pop("slug")
        record.pop("scenario_id")
    null = published["null_baseline"]
    faulted = [name for name, record in published.items()
               if name != "null_baseline" and record == null]
    assert faulted, (
        "the fault-free scenario is distinguishable from every faulted one by "
        "its build inputs alone")


def test_the_slug_of_a_dataset_is_recovered_from_its_identity(options):
    for option in options:
        assert scenarios.slug_for_scenario_id(
            option.scenario_id, SCENARIO_ROOT) == option.slug


def test_an_unknown_identity_resolves_to_nothing_rather_than_a_guess():
    assert scenarios.slug_for_scenario_id("scn-000000000000",
                                          SCENARIO_ROOT) is None


def test_a_library_that_is_not_there_is_empty_rather_than_an_error(tmp_path):
    assert scenarios.available(tmp_path / "nope") == ()


def test_an_unparseable_configuration_does_not_take_the_library_down(tmp_path):
    """One malformed file is an editing mistake, not a broken installation."""
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "fine.json").write_text(
        (SCENARIO_ROOT / "null_baseline.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    slugs = [option.slug for option in scenarios.available(tmp_path)]
    assert slugs == ["fine"]


def test_the_generator_still_gets_the_whole_configuration():
    """The projection is for screens, not for the simulator: `config_for`
    returns the real thing, because that is what gets built."""
    config = scenarios.config_for("chamber_edge_uniformity", SCENARIO_ROOT)
    assert config.events, "the generator was handed a scenario with no events"
    assert config.description


def test_asking_for_a_scenario_that_does_not_exist_lists_the_ones_that_do():
    with pytest.raises(FileNotFoundError, match="null_baseline"):
        scenarios.config_for("no_such_scenario", SCENARIO_ROOT)
