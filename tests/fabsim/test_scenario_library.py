"""
The Phase 1 scenario library: five configurations, and what each one is for.

`SCENARIO_SPECIFICATION.md` §4 specifies A, B, C, G and I — a null, a spatial
equipment fault, a slow process drift, the same equipment fault under a
routing confound, and a full fault→repair→recovery arc. This module checks
that each one is *realized* as specified and that the five are genuinely
different **as observable datasets**, which is the only comparison a
benchmark will ever get to make.

Two rules shape everything here:

* **Diversity is argued from the observable plane only.** Truth is used to
  confirm that the intended mechanism was realized — that is what truth is
  for — but never to demonstrate that two datasets differ. If the scenarios
  are only distinguishable with the answer key in hand, they are not a
  benchmark, and a test that reached for truth to prove otherwise would be
  hiding exactly that.
* **Nothing is asserted about how *large* an effect is beyond what the
  physics produced.** The assertions are relational — this chamber ranks
  above its peers, this window differs from that one, these two cohorts
  overlap — because a threshold chosen to pass is a threshold that measures
  the author.

The library builds once, at the size its configs declare (84 days, 20 lots,
seed 42), because onset days of 30–40 have no meaning on a shorter horizon.
That costs about ninety seconds and some temporary disk; it is one fixture.
"""
from __future__ import annotations

import json
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from fabsim.emit import build_dataset
from fabsim.scenario import ScenarioConfigError, from_mapping, load_scenario
from fabsim.world import load_world

SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"

#: The approved set (`SCENARIO_SPECIFICATION.md` §4), by file stem. Exactly
#: five: D/E/F/H/J are not in Phase 1 and their absence is asserted.
LIBRARY = ("null_baseline", "chamber_edge_uniformity", "parameter_drift",
           "confounded_chamber_vs_product", "fault_repair_recovery")

#: What each scenario plants, for the truth-side confirmation only. Never
#: used to argue that two observable datasets differ.
PLANTED: dict[str, tuple[str, str] | None] = {
    "null_baseline": None,
    "chamber_edge_uniformity": ("chamber_edge_uniformity", "ETCH-02/B"),
    "parameter_drift": ("param_drift", "ETCH-03/A"),
    "confounded_chamber_vs_product": ("chamber_edge_uniformity", "ETCH-01/A"),
    "fault_repair_recovery": ("particle_excursion", "CVD-01/A"),
}


def configs() -> dict[str, Any]:
    return {name: load_scenario(SCENARIO_ROOT / f"{name}.json")
            for name in LIBRARY}


@pytest.fixture(scope="module")
def library(world, tmp_path_factory):
    """All five, built once at their declared size. The expensive fixture."""
    root = tmp_path_factory.mktemp("library")
    return {name: build_dataset(config, world=world, root=root / name,
                                created_at="2026-01-01T00:00:00+00:00")
            for name, config in configs().items()}


def query(dataset, sql: str, *params) -> list[tuple]:
    connection = sqlite3.connect(str(dataset.db_path))
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def day(world, number: float) -> str:
    """A scenario day as the timestamp the observable plane records."""
    return world.at(int(number * 24 * 60)).isoformat(sep=" ",
                                                     timespec="seconds")


def zscore(scores: dict[str, float], key: str) -> float:
    """How far one chamber stands out from the others on one signal."""
    others = [v for name, v in scores.items() if name != key]
    spread = st.pstdev(others)
    return (scores[key] - st.mean(others)) / spread if spread else 0.0


# ------------------------------------------------------------- the library


def test_the_library_is_exactly_the_five_approved_scenarios():
    """D/E/F/H/J are not Phase 1, and a sixth file would be scope creep."""
    on_disk = sorted(p.stem for p in SCENARIO_ROOT.glob("*.json"))
    assert on_disk == sorted(LIBRARY)


def test_every_scenario_validates_through_the_shipped_contract():
    """No second parser, no bypass: `fabsim.scenario` or nothing."""
    for name, config in configs().items():
        assert config.world == "baseline_fab_v1", name
        assert config.horizon_days == 84 and config.lots == 20, name
        assert config.default_seed == 42, name
        assert json.loads(config.canonical_json)["fabsim"] == "scenario/v1"


def test_a_malformed_scenario_would_be_rejected(world):
    """The library passing says something only if failure is possible.

    Validation is in two places on purpose and both are exercised.
    `fabsim.scenario` checks *structure* and deliberately does not open a
    registry — a config may name a world or a mechanism that does not exist
    yet, and the loader is not the thing that knows. The registry itself
    rejects the name at resolve time.
    """
    from fabsim.mechanisms import resolve_events

    base = json.loads(configs()["chamber_edge_uniformity"].canonical_json)

    structural = json.loads(json.dumps(base))
    structural["events"][0]["severity"] = "catastrophic"
    with pytest.raises(ScenarioConfigError):
        from_mapping(structural)

    unknown = json.loads(json.dumps(base))
    unknown["events"][0]["mechanism"] = "chamber_is_bad"
    accepted = from_mapping(unknown)          # structurally fine…
    with pytest.raises(ScenarioConfigError, match="unknown mechanism"):
        resolve_events(world, accepted.events)   # …and refused here


def test_the_scenarios_have_distinct_identities():
    identities = {name: c.scenario_id for name, c in configs().items()}
    assert len(set(identities.values())) == len(LIBRARY)
    for scenario_id in identities.values():
        assert scenario_id.startswith("scn-") and len(scenario_id) == 16


def test_renaming_a_scenario_does_not_change_its_identity():
    """§5: prose is documentation. Renaming must not claim a new dataset."""
    for name, config in configs().items():
        raw = json.loads(config.canonical_json)
        raw["name"] = "something-else-entirely"
        raw["description"] = "and a different description"
        assert from_mapping(raw).config_sha256 == config.config_sha256, name


def test_no_scenario_identity_leaks_its_subject():
    """Rule D5: an id may not disclose what the dataset contains."""
    for name, config in configs().items():
        haystack = f"{config.scenario_id} {config.dataset_identity(42, world_sha256='0'*64).dataset_id}"
        for token in ("edge", "drift", "confound", "repair", "null",
                      "etch", "cvd", "uniform", "particle", "product"):
            assert token not in haystack.lower(), (name, token)


def test_the_maintainers_index_lists_every_scenario():
    """§5: the slug ↔ id mapping lives in truth and in this index, and
    nowhere a diagnostic consumer can reach."""
    index = (SCENARIO_ROOT / "README.md").read_text(encoding="utf-8")
    for name, config in configs().items():
        assert name in index, name
        assert config.scenario_id in index, name


# ------------------------------------------------- realized as specified


def test_each_scenario_realizes_the_mechanism_it_declares(library):
    """Truth's job: confirm the intended physics actually happened."""
    for name, dataset in library.items():
        planted = PLANTED[name]
        events = dataset.truth["events"]
        if planted is None:
            assert events == [], name
            continue
        (event,) = events
        mechanism, target = planted
        assert event["mechanism"] == mechanism, name
        assert f"{event['target']['tool']}/{event['target']['chamber']}" == target
        assert event["severity_realized"]["aggregate_shift_sigma"] > 0.0, name
        assert event["affected_runs"] and event["affected_wafers"], name
        assert event["causal_chain"][0].startswith("latent."), name
        assert "wafer_yield" in event["causal_chain"], name


def test_the_truth_of_each_scenario_carries_the_contract_fields(library):
    """§13, field by field, against `GROUND_TRUTH_CONTRACT.md` §3."""
    for name, dataset in library.items():
        truth = dataset.truth
        assert set(truth) >= {"schema", "dataset_id", "scenario_id",
                              "scenario_name", "config_sha256", "seed",
                              "fabsim_version", "schema_version", "events",
                              "distractors", "latent_summaries"}
        assert truth["scenario_name"] == name
        assert truth["distractors"], name
        for event in truth["events"]:
            assert set(event) >= {
                "event_id", "mechanism", "target", "onset", "end", "profile",
                "severity", "severity_realized", "causal_chain",
                "alarms_emitted", "maintenance_response", "affected_runs",
                "affected_wafers", "expected_impact"}


# ---------------------------------------------------------------- A: null


def test_the_null_is_not_an_artificially_clean_world(library):
    """§12: A is the false-positive control, so it must contain everything a
    faulted world contains except the fault.

    A null that alarmed less, broke down less or lost no yield would make any
    of those an answer on its own, and the control would be measuring the
    wrong thing.
    """
    null = library["null_baseline"]
    counts = null.observable.row_counts()
    assert null.truth["events"] == []

    assert counts["alarms"] > 20
    unscheduled = query(null, "SELECT COUNT(*) FROM maintenance "
                              "WHERE maint_type = 'UNSCHEDULED'")[0][0]
    assert unscheduled > 10
    assert counts["defects"] > 5000

    yields = [row[0] for row in query(null, "SELECT yield_pct FROM wafer_yield")]
    assert len(yields) > 100
    assert st.pstdev(yields) > 1.0            # not a constant
    assert max(yields) < 100.0                # no perfect wafer
    assert min(yields) > 40.0                 # and no catastrophe either
    bins = {row[0] for row in query(null, "SELECT DISTINCT bin_code FROM die_bins")}
    assert bins == {"PASS", "OPEN_SHORT", "PARAM", "LEAK", "OTHER"}


def test_the_null_is_not_quieter_than_the_faulted_scenarios(library):
    """The control must not be separable from the cases by volume alone."""
    null = library["null_baseline"].observable.row_counts()
    for name, dataset in library.items():
        if name == "null_baseline":
            continue
        counts = dataset.observable.row_counts()
        for table in ("alarms", "maintenance", "defects", "wafer_yield"):
            ratio = counts[table] / max(1, null[table])
            assert 0.7 < ratio < 1.5, (name, table, ratio)


# ------------------------------------------------------ B: the edge fault


def _chamber_signals(dataset, world) -> dict[str, dict[str, float]]:
    """Per-etch-chamber observable signals, from SQL over the dataset alone.

    Three independent channels a real analyst would reach for: how far the
    edge-site CD sits from its own recipe target, what share of the gate-layer
    defects land in the outer fifth of the wafer, and how often the chamber
    complained. No truth, no latent, no origin — everything here is a column
    an emitted dataset has.
    """
    rows = query(dataset, """
        SELECT t.tool_name || '/' || c.chamber_name, m.value, rc.metric_target
        FROM metrology m
        JOIN flow_steps f ON f.flow_step_id = m.flow_step_id
        JOIN runs r ON r.wafer_id = m.wafer_id
                   AND r.flow_step_id = m.flow_step_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        JOIN wafers w ON w.wafer_id = m.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN recipes rc ON rc.step_id = f.step_id
                       AND rc.product_id = l.product_id
        WHERE m.param_name = 'cd_nm_edge'""")
    cd: dict[str, list[float]] = defaultdict(list)
    for name, value, target in rows:
        cd[name].append(abs(value - target) / target)

    rows = query(dataset, """
        SELECT t.tool_name || '/' || c.chamber_name, d.x_mm, d.y_mm
        FROM defects d
        JOIN runs r ON r.wafer_id = d.wafer_id
        JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
        JOIN process_steps s ON s.step_id = f.step_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        WHERE s.operation_type = 'ETCH' AND d.layer = 'GATE'""")
    edge: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for name, x_mm, y_mm in rows:
        bucket = edge[name]
        bucket[1] += 1
        if (x_mm ** 2 + y_mm ** 2) ** 0.5 >= 0.80 * 150.0:
            bucket[0] += 1

    rows = query(dataset, """
        SELECT t.tool_name || '/' || c.chamber_name, COUNT(*)
        FROM alarms a
        JOIN chambers c ON c.chamber_id = a.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        WHERE t.tool_name LIKE 'ETCH-%' GROUP BY 1""")
    alarms = {name: float(count) for name, count in rows}

    etch = {name for name in cd if len(cd[name]) > 20}
    return {
        "cd_edge": {n: st.mean(cd[n]) for n in etch},
        "edge_share": {n: edge[n][0] / edge[n][1] for n in etch
                       if edge[n][1] > 200},
        "alarms": {n: alarms.get(n, 0.0) for n in etch},
    }


def test_the_edge_fault_stands_out_on_the_channels_it_should(library, world):
    """B's expected evidence (§4 B), measured on the observable plane.

    The planted chamber must rank first on the edge-sensitive channels — CD at
    the edge sites, the edge-zone defect share, and the alarm count — and the
    same chamber must *not* rank first in the null, or the ranking would be a
    property of that chamber rather than of the fault.
    """
    target = "ETCH-02/B"
    faulted = _chamber_signals(library["chamber_edge_uniformity"], world)
    null = _chamber_signals(library["null_baseline"], world)

    for channel in ("cd_edge", "edge_share", "alarms"):
        scores = faulted[channel]
        assert max(scores, key=scores.get) == target, (channel, scores)
        assert zscore(scores, target) > 1.5, (channel, zscore(scores, target))
        # …and it moved: the same chamber's standing on this channel is
        # higher than it was with no fault in the world.
        assert scores[target] > null[channel][target], channel


def test_the_edge_fault_is_mediated_and_not_written_in(library, world):
    """The subtraction, at library scale.

    Rebuild the same scenario with the mechanism removed and nothing else
    changed, and measure how much of the chamber's standing goes with it.

    The comparison is a *difference*, not a bar, and it has to be: at this
    seed ETCH-02/B already carries a large benign edge offset — it stands at
    z≈+1.4 on this channel with no fault in the world at all, which is rule
    F11's standing structure doing exactly what it exists to do. A test that
    demanded the chamber look ordinary without the mechanism would be
    asserting that benign offsets are small, which is the opposite of the
    design. What must be true is that removing the mechanism removes the
    mechanism's share.
    """
    config = configs()["chamber_edge_uniformity"]
    raw = json.loads(config.canonical_json)
    raw["events"] = []
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        without = build_dataset(from_mapping(raw), world=world,
                                root=Path(directory), created_at="pinned")
        benign = _chamber_signals(without, world)
    faulted = _chamber_signals(library["chamber_edge_uniformity"], world)

    with_fault = zscore(faulted["cd_edge"], "ETCH-02/B")
    without_fault = zscore(benign["cd_edge"], "ETCH-02/B")
    assert with_fault - without_fault > 0.8, (with_fault, without_fault)
    assert faulted["cd_edge"]["ETCH-02/B"] > benign["cd_edge"]["ETCH-02/B"]
    # The alarm channel is the fault's alone: the benign offset is absorbed
    # into the chamber's own control limits (ADR-017 §3), so it cannot alarm.
    assert (zscore(faulted["alarms"], "ETCH-02/B")
            - zscore(benign["alarms"], "ETCH-02/B")) > 1.0


# ------------------------------------------------------------- C: the drift


def test_the_drift_is_a_trend_on_one_chamber(library, world):
    """C's expected evidence (§4 C): a monotone CD walk, visible in metrology
    before yield says anything.

    Measured as a difference in differences — each chamber's late-window mean
    minus its early-window mean — so the fab-wide wander every chamber shares
    cancels and what is left is the one that moved on its own.
    """
    dataset = library["parameter_drift"]
    rows = query(dataset, """
        SELECT t.tool_name || '/' || c.chamber_name, m.meas_time, m.value,
               rc.metric_target
        FROM metrology m
        JOIN flow_steps f ON f.flow_step_id = m.flow_step_id
        JOIN runs r ON r.wafer_id = m.wafer_id
                   AND r.flow_step_id = m.flow_step_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        JOIN wafers w ON w.wafer_id = m.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN recipes rc ON rc.step_id = f.step_id
                       AND rc.product_id = l.product_id
        WHERE m.param_name = 'cd_nm_center'""")

    # Split at the onset — the physically natural before/after, and chosen
    # for that reason rather than for the ranking it produces. (It matters:
    # the planted chamber ranks 1st at a cut of day 44 and 3rd at day 51, and
    # picking the flattering one would be measuring the author.)
    cut = day(world, 30)
    early: dict[str, list[float]] = defaultdict(list)
    late: dict[str, list[float]] = defaultdict(list)
    for name, when, value, target in rows:
        (late if when >= cut else early)[name].append((value - target) / target)
    shift = {name: st.mean(late[name]) - st.mean(early[name])
             for name in early
             if len(early[name]) > 10 and len(late.get(name, ())) > 10}

    assert len(shift) >= 5
    # A trend exists on the planted chamber, and it is *evidence* rather than
    # a verdict. C is `subtle`, and §8 defines subtle as "near the detection
    # floor — benchmark headroom": a subtle scenario whose chamber topped a
    # single GROUP BY would not be subtle. Measured here: it ranks second of
    # seven at z≈+1.0. Demanding rank 1 would either be tuning the scenario
    # or pinning a lucky window.
    assert shift["ETCH-03/A"] > 0.0
    assert zscore(shift, "ETCH-03/A") > 0.5
    ranked = sorted(shift, key=shift.get, reverse=True)
    assert ranked.index("ETCH-03/A") < 3, ranked


def test_the_drift_stays_below_the_alarm_channel(library, world):
    """§4 C: "no alarm until late — drift is below alarm thresholds by
    design". The drift must not be findable the easy way."""
    dataset = library["parameter_drift"]
    signals = _chamber_signals(dataset, world)
    assert zscore(signals["alarms"], "ETCH-03/A") < 2.0


# ---------------------------------------------------- G: the confounded one


def test_the_routing_condition_creates_a_real_exposure_imbalance(library,
                                                                 world):
    """§9, measured from `runs` alone — the confound is observable data.

    Inside the declared window the dedicated product's traffic concentrates on
    the dedicated tool; outside it, it does not. The routing shift is honest
    data a diagnosis engine is expected to see and control for (ADR-015), not
    hidden state.
    """
    dataset = library["confounded_chamber_vs_product"]
    rows = query(dataset, """
        SELECT p.product_name, t.tool_name, r.start_time
        FROM runs r
        JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
        JOIN process_steps s ON s.step_id = f.step_id
        JOIN tools t ON t.tool_id = r.tool_id
        JOIN wafers w ON w.wafer_id = r.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN products p ON p.product_id = l.product_id
        WHERE s.operation_type = 'ETCH'""")
    start, end = day(world, 28), day(world, 62)
    inside: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    outside: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for product, tool, when in rows:
        bucket = inside if start <= when < end else outside
        bucket[product][tool] += 1

    def share(bucket, product):
        total = sum(bucket[product].values())
        return bucket[product].get("ETCH-01", 0) / total if total else 0.0

    dedicated = share(inside, "Mobile-28")
    assert dedicated > 0.7, dedicated
    assert dedicated > share(outside, "Mobile-28") + 0.3
    for product in inside:
        if product != "Mobile-28":
            assert share(inside, product) < dedicated - 0.3, product


def test_the_confound_leaves_both_control_comparisons_data(library, world):
    """§9: a preference, bounded away from certainty.

    A hard filter would make product and chamber exposure the same variable
    and neither control comparison would have anything to run on (ADR-015 §2).
    Three overlaps are required and all three are checked: the dedicated
    product still reaches other tools, other products still reach the
    dedicated tool, and inside the tool the chamber is still chosen by
    availability rather than by the dedication.
    """
    dataset = library["confounded_chamber_vs_product"]
    start, end = day(world, 28), day(world, 62)
    rows = query(dataset, """
        SELECT p.product_name, t.tool_name, c.chamber_name
        FROM runs r
        JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
        JOIN process_steps s ON s.step_id = f.step_id
        JOIN tools t ON t.tool_id = r.tool_id
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN wafers w ON w.wafer_id = r.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN products p ON p.product_id = l.product_id
        WHERE s.operation_type = 'ETCH'
          AND r.start_time >= ? AND r.start_time < ?""", start, end)

    escaped = sum(1 for product, tool, _c in rows
                  if product == "Mobile-28" and tool != "ETCH-01")
    visitors = sum(1 for product, tool, _c in rows
                   if product != "Mobile-28" and tool == "ETCH-01")
    assert escaped >= 5, escaped
    assert visitors >= 20, visitors

    chambers: dict[str, int] = defaultdict(int)
    for _p, tool, chamber in rows:
        if tool == "ETCH-01":
            chambers[chamber] += 1
    assert len(chambers) == 2
    smaller, larger = sorted(chambers.values())
    assert smaller / larger > 0.5, chambers


def test_the_confounded_fault_is_still_physically_realized(library):
    """The correlation is a distractor; the chamber fault is the cause."""
    truth = library["confounded_chamber_vs_product"].truth
    (event,) = truth["events"]
    assert event["target"]["tool"] == "ETCH-01"
    assert event["severity_realized"]["aggregate_shift_sigma"] > 1.0
    kinds = [d["kind"] for d in truth["distractors"]]
    assert "routing_condition" in kinds
    assert "benign_offset_baseline" in kinds


def test_the_dedicated_product_is_not_the_only_exposed_one(library, world):
    """No perfect separation: if only one product ever saw the chamber, the
    within-product control would be undefined and G would be unscoreable."""
    dataset = library["confounded_chamber_vs_product"]
    rows = query(dataset, """
        SELECT DISTINCT p.product_name
        FROM runs r
        JOIN chambers c ON c.chamber_id = r.chamber_id
        JOIN tools t ON t.tool_id = c.tool_id
        JOIN wafers w ON w.wafer_id = r.wafer_id
        JOIN lots l ON l.lot_id = w.lot_id
        JOIN products p ON p.product_id = l.product_id
        WHERE t.tool_name = 'ETCH-01' AND c.chamber_name = 'A'""")
    assert len({row[0] for row in rows}) >= 3


# ------------------------------------------------------- I: the whole arc


def test_the_fault_repair_arc_happens_in_causal_order(library, world):
    """§10: the ordering must come out of the simulator, not the config.

    Read entirely from observable timestamps: the configured onset, then the
    chamber's first alarm, then the repair window that the escalation earned,
    then the window's end. Each strictly after the last.
    """
    dataset = library["fault_repair_recovery"]
    chamber = world.chamber_by_name("CVD-01", "A").chamber_id
    onset = day(world, 40)

    alarms = query(dataset, "SELECT alarm_time, alarm_code FROM alarms "
                            "WHERE chamber_id = ? AND alarm_time >= ? "
                            "ORDER BY alarm_time", chamber, onset)
    assert len(alarms) >= 3, "escalation needs three complaints in a window"
    assert any(code == "PARTICLE_HI" for _t, code in alarms)
    first_alarm = alarms[0][0]

    repairs = query(dataset, "SELECT start_time, end_time FROM maintenance "
                             "WHERE chamber_id = ? AND maint_type = "
                             "'UNSCHEDULED' AND start_time >= ? "
                             "ORDER BY start_time", chamber, first_alarm)
    assert repairs, "the escalation produced no repair"
    repair_start, repair_end = repairs[0]

    assert onset < first_alarm < repair_start < repair_end
    # …and the repair is the fab's own loop, not an instant reflex.
    assert first_alarm < repair_start


def test_the_arc_moves_the_defect_rate_and_the_repair_is_imperfect(library,
                                                                   world):
    """§4 I: a before/after-maintenance contrast on one chamber, with a
    residual — the recovery quality is drawn, so "did it work?" is a real
    question with a nontrivial answer (`CAUSAL_MECHANISM_MODEL.md` §6).
    """
    dataset = library["fault_repair_recovery"]
    chamber = world.chamber_by_name("CVD-01", "A").chamber_id
    rows = query(dataset, """
        SELECT i.inspection_time, i.total_defect_count,
               EXISTS(SELECT 1 FROM runs r WHERE r.wafer_id = i.wafer_id
                      AND r.chamber_id = ?) AS exposed
        FROM inspections i
        WHERE EXISTS(SELECT 1 FROM defects d
                     WHERE d.inspection_id = i.inspection_id
                       AND d.layer = 'METAL')""", chamber)

    onset = day(world, 40)
    before = [n for when, n, exposed in rows if exposed and when < onset]
    during = [n for when, n, exposed in rows if exposed and when >= onset]
    control_before = [n for when, n, exposed in rows
                      if not exposed and when < onset]
    control_during = [n for when, n, exposed in rows
                      if not exposed and when >= onset]
    assert len(before) > 10 and len(during) > 10

    # Difference in differences: the exposed chamber's defect rate rose
    # relative to the fab's over the same stretch.
    moved = ((st.mean(during) - st.mean(before))
             - (st.mean(control_during) - st.mean(control_before)))
    assert moved > 0.5, moved

    # Recovery is realized rather than configured, and imperfect either way.
    (event,) = dataset.truth["events"]
    response = event["maintenance_response"]
    assert response is not None
    assert 0.0 <= response["recovery_fraction"] < 1.0


# ------------------------------------------------------ cross-scenario


def test_the_five_datasets_are_observably_different(library):
    """§16: diversity argued from the observable plane, never from truth.

    If two library members produced the same observable dataset, one of them
    would be scoring nothing.
    """
    digests = {name: dataset.observable.content_sha256()
               for name, dataset in library.items()}
    assert len(set(digests.values())) == len(LIBRARY)

    fingerprints = {name: dataset.identity.build_fingerprint
                    for name, dataset in library.items()}
    assert len(set(fingerprints.values())) == len(LIBRARY)


def test_the_scenarios_differ_in_where_and_when_they_move(library, world):
    """…and differ in *structure*, not merely in their bytes.

    Two datasets could hash differently and still be the same scenario with
    the noise reshuffled. What is checked here is that the library covers
    distinct observable shapes: the two etch faults implicate different
    chambers, the confounded one has a routing shift the others do not, and
    the particle scenario moves a different operation type entirely.
    """
    edge_leader = {}
    for name in ("chamber_edge_uniformity", "confounded_chamber_vs_product",
                 "null_baseline"):
        signals = _chamber_signals(library[name], world)
        edge_leader[name] = max(signals["cd_edge"], key=signals["cd_edge"].get)
    assert edge_leader["chamber_edge_uniformity"] == "ETCH-02/B"
    assert edge_leader["confounded_chamber_vs_product"] != \
        edge_leader["chamber_edge_uniformity"]

    # Only G shifts routing.
    def dedication_gap(dataset):
        rows = query(dataset, """
            SELECT t.tool_name, r.start_time FROM runs r
            JOIN flow_steps f ON f.flow_step_id = r.flow_step_id
            JOIN process_steps s ON s.step_id = f.step_id
            JOIN tools t ON t.tool_id = r.tool_id
            JOIN wafers w ON w.wafer_id = r.wafer_id
            JOIN lots l ON l.lot_id = w.lot_id
            JOIN products p ON p.product_id = l.product_id
            WHERE s.operation_type = 'ETCH' AND p.product_name = 'Mobile-28'""")
        start, end = day(world, 28), day(world, 62)
        inside = [t for t, when in rows if start <= when < end]
        outside = [t for t, when in rows if not (start <= when < end)]
        if not inside or not outside:
            return 0.0
        return (sum(1 for t in inside if t == "ETCH-01") / len(inside)
                - sum(1 for t in outside if t == "ETCH-01") / len(outside))

    gaps = {name: dedication_gap(dataset) for name, dataset in library.items()}
    assert gaps["confounded_chamber_vs_product"] > 0.3
    for name, gap in gaps.items():
        if name != "confounded_chamber_vs_product":
            assert abs(gap) < 0.25, (name, gap)

    # Only I concentrates alarms on a CVD chamber.
    def cvd_alarm_share(dataset):
        rows = query(dataset, """
            SELECT t.tool_name, COUNT(*) FROM alarms a
            JOIN tools t ON t.tool_id = a.tool_id GROUP BY 1""")
        total = sum(n for _t, n in rows)
        return sum(n for t, n in rows if t.startswith("CVD")) / max(1, total)

    shares = {name: cvd_alarm_share(d) for name, d in library.items()}
    assert shares["fault_repair_recovery"] > max(
        v for k, v in shares.items() if k != "fault_repair_recovery")


# ---------------------------------------------------------- anti-leakage


FORBIDDEN_TEXT = ("mechanism", "fault", "suspect", "ground_truth", "truth",
                  "scenario", "counterfactual", "origin", "injected")


def test_no_scenario_writes_its_subject_into_the_observable_plane(library):
    """§14 test 3, over every text value of every row of all five datasets."""
    from fabsim.emit.observable import SCHEMA_TABLES

    subjects = {"chamber_edge_uniformity", "param_drift",
                "particle_excursion", "benign_offset", "null_baseline",
                "parameter_drift", "confounded_chamber_vs_product",
                "fault_repair_recovery", "product_dedication"}
    for name, dataset in library.items():
        for table in SCHEMA_TABLES:
            for row in dataset.observable.rows(table):
                for value in row:
                    if not isinstance(value, str):
                        continue
                    lowered = value.lower()
                    for subject in subjects:
                        assert subject not in lowered, (name, table, value)
                    for token in FORBIDDEN_TEXT:
                        if token == "origin" and value.startswith("2026"):
                            continue
                        assert token not in lowered, (name, table, value)


def test_no_dataset_path_names_its_scenario(library):
    """§14 test 4 / ADR-013: directories are opaque ids."""
    for name, dataset in library.items():
        parts = " ".join(dataset.directory.parts[-1:]).lower()
        for token in ("edge", "drift", "confound", "repair", "null",
                      "uniform", "particle"):
            assert token not in parts, (name, token)
        assert dataset.directory.name == dataset.dataset_id


def test_the_manifest_of_every_scenario_names_no_subject(library):
    for name, dataset in library.items():
        rest = {k: v for k, v in dataset.manifest.items() if k != "row_counts"}
        text = json.dumps(rest).lower()
        for token in ("edge", "drift", "confound", "repair", "null",
                      "mechanism", "etch", "cvd", "mobile"):
            assert token not in text, (name, token)


def test_the_truth_of_a_scenario_is_reachable_only_beside_it(library):
    """§14 test 5: the hidden plane stays in its own directory, and nothing
    in the observable one points at it."""
    for name, dataset in library.items():
        assert dataset.truth_path.parent.name == "truth"
        assert dataset.truth_path.parent.parent == dataset.directory
        blob = dataset.db_path.read_bytes()
        assert b"truth.json" not in blob, name
        assert name.encode() not in blob, name


# -------------------------------------------------------- reproducibility


@pytest.mark.parametrize("name", LIBRARY)
def test_every_scenario_rebuilds_identically(world, tmp_path, name):
    """§15, per scenario: same five inputs, same dataset.

    Built at a reduced horizon so the parametrization stays affordable; what
    is under test is the determinism of the pipeline, which does not depend
    on how long the horizon is.
    """
    # The horizon stays at 84 days — onsets of 30 to 40 have no meaning on a
    # shorter one, and the contract rightly refuses an event that never
    # starts. Lots are what get reduced, which thins the wafer population
    # without touching the temporal structure under test.
    raw = json.loads(configs()[name].canonical_json)
    raw["lots"] = 2
    config = from_mapping(raw)

    first = build_dataset(config, world=world, root=tmp_path / "a",
                          created_at="pinned")
    second = build_dataset(config, world=world, root=tmp_path / "b",
                           created_at="pinned")
    assert first.observable.content_sha256() == \
        second.observable.content_sha256()
    assert first.truth_path.read_bytes() == second.truth_path.read_bytes()
    assert (first.directory / "fab_database.sql").read_bytes() == \
        (second.directory / "fab_database.sql").read_bytes()
    assert first.manifest == second.manifest


@pytest.mark.parametrize("name", LIBRARY)
def test_a_different_seed_gives_a_different_realization(world, tmp_path, name):
    """A2's precondition: the library is not seed-degenerate."""
    raw = json.loads(configs()[name].canonical_json)
    raw["lots"] = 2
    config = from_mapping(raw)

    first = build_dataset(config, 42, world=world, root=tmp_path / "a",
                          created_at="pinned")
    second = build_dataset(config, 101, world=world, root=tmp_path / "b",
                           created_at="pinned")
    assert first.observable.content_sha256() != \
        second.observable.content_sha256()
    assert first.identity.dataset_id != second.identity.dataset_id
    # …but the scenario is the same scenario.
    assert first.identity.scenario_id == second.identity.scenario_id
    assert first.truth["scenario_name"] == second.truth["scenario_name"]
