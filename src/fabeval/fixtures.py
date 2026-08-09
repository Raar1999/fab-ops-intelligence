"""
fixtures.py — the per-scenario expectation table L11 scores against.

`ANTI_LEAKAGE_DESIGN.md` L11 says the expectations live "in `eval/fixtures/`",
and this is that table. It exists as a separate module from the checks for one
reason: a check that decided for itself what "recoverable" means could always
be satisfied, so what each scenario is *expected* to show is declared once,
in one place, where changing it is visible in a diff.

**These are relational, not golden.** Each expectation says which chamber
should rank where on which observable channel, and how strongly — an ordering
and a floor, never a value to match. Two reasons: a golden number turns every
legitimate physics change into a test failure that teaches nothing, and a
floor chosen after seeing the result is a floor that measures the author. The
floors here are stated in leave-one-out σ across chambers, the same currency
L7 uses for the null, so "above the floor" and "below it" are one measurement.

Where a scenario is *specified* to be near the floor — C is `subtle`, and
`CAUSAL_MECHANISM_MODEL.md` §8 defines subtle as "near the detection floor;
no alarms; benchmark headroom" — the expectation says so, and a result that
was *too* strong would fail it. An evaluation that only ever checks "is the
signal big enough" cannot notice a simulator that made the hard case easy.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from fabeval.queries import (
    alarm_counts,
    chamber_edge_cd_deviation,
    chamber_edge_defect_share,
    chamber_yield_split,
    maintenance_contrast,
    metric_trend,
    product_tool_exposure,
    rank,
    zscore,
)

__all__ = ["EXPECTATIONS", "evaluate_expectation", "expectation_for"]

#: Column order of `dataset_meta`, mirrored so this module needs no import
#: from the emitter to read one observable row.
_META_COLUMNS = ("schema_version", "fabsim_version", "dataset_id",
                 "time_origin", "horizon_days")

#: What each library scenario is expected to show, on the observable plane.
#:
#: `channels` — (query, the chamber that should lead, the leave-one-out σ
#: floor, the worst acceptable rank, whether the channel is **required**). A
#: required channel must meet both bounds; a corroborating one is measured and
#: reported but cannot fail the scenario alone.
#:
#: That distinction is not a convenience. ADR-018 records that
#: `edge_uniformity` is **signed**, so whether a fault's edge push reinforces
#: or opposes a chamber's own benign radial offset is a per-seed coin flip —
#: and the edge-*defect* channel reads `|edge_uniformity|`, so it can
#: legitimately go either way. Measured across scenario B's three A2 seeds:
#: metrology ranks the planted chamber 1st every time (z = +2.65 / +1.46 /
#: +2.15) while defect share ranks it 1st, 3rd and 6th (z = +2.34 / +0.08 /
#: −1.54). Requiring the defect channel to lead would be requiring a
#: guarantee ADR-018 says the physics does not give, and the first thing to
#: break would be a seed rather than a bug.
#:
#: `min_leading` is how many declared channels the planted chamber must rank
#: first on, so "the evidence is recoverable" is a claim about the scenario
#: rather than about one query. `max_sigma` caps a channel the scenario is
#: specified to stay near the floor on. `structure` names non-channel
#: properties.
EXPECTATIONS: dict[str, dict[str, Any]] = {
    "null_baseline": {
        "events": 0,
        # A is scored by L7 rather than by a channel expectation: the whole
        # claim is that *no* chamber leads by more than natural variation.
        "channels": (),
        "structure": ("no_events", "quiet"),
    },
    "chamber_edge_uniformity": {
        "events": 1,
        "channels": (
            # B's three declared channels (`SCENARIO_SPECIFICATION.md` §4 B).
            # Metrology is required: it reads the *signed* departure through
            # the radial model and holds across seeds. The other two
            # corroborate, for the reason stated above.
            ("edge_cd", "ETCH-02/B", 1.0, 1, True),
            ("edge_defect_share", "ETCH-02/B", 0.0, 7, False),
            ("alarms", "ETCH-02/B", 1.0, 2, True),
        ),
        "min_leading": 1,
        "structure": ("repair_in_window",),
    },
    "parameter_drift": {
        "events": 1,
        "channels": (
            # C is `subtle`: a trend that is present and near the floor. The
            # cap is the point — a subtle scenario that led by 3σ would mean
            # the difficulty axis had collapsed.
            ("cd_trend", "ETCH-03/A", 0.5, 3, True),
        ),
        "max_sigma": {"cd_trend": 3.0, "alarms": 2.0},
        "structure": ("trend_positive",),
    },
    "confounded_chamber_vs_product": {
        "events": 1,
        "channels": (
            ("alarms", "ETCH-01/A", 1.5, 1, True),
        ),
        "min_leading": 1,
        "structure": ("confound_present", "confound_imperfect"),
    },
    "fault_repair_recovery": {
        "events": 1,
        "channels": (
            ("alarms", "CVD-01/A", 1.5, 1, True),
        ),
        "min_leading": 1,
        "structure": ("arc_ordered", "maintenance_contrast"),
    },
}


def expectation_for(scenario_name: str) -> dict[str, Any] | None:
    return EXPECTATIONS.get(scenario_name)


def _channel(dataset: Any, name: str) -> dict[str, float]:
    """One reference query's per-chamber scores, by channel name."""
    if name == "edge_cd":
        scores = chamber_edge_cd_deviation(dataset.db_path)
    elif name == "edge_defect_share":
        scores = chamber_edge_defect_share(dataset.db_path)
    elif name == "alarms":
        scores = alarm_counts(dataset.db_path)
    elif name == "yield_split":
        # Lower is worse for a yield deficit, so it is negated to keep every
        # channel "higher means more suspicious" and the σ floors comparable.
        return {label: -score.value
                for label, score in chamber_yield_split(dataset.db_path).items()}
    elif name == "cd_trend":
        onset = dataset.truth["events"][0]["onset"] if dataset.truth["events"] \
            else "0000"
        scores = metric_trend(dataset.db_path, onset)
    else:
        raise KeyError(f"unknown reference channel {name!r}")
    return {label: score.value for label, score in scores.items()}


def _structure(dataset: Any, name: str) -> tuple[bool, str]:
    """Non-channel properties a scenario must exhibit."""
    truth = dataset.truth
    if name == "no_events":
        return not truth["events"], "events: 0"
    if name == "quiet":
        counts = dataset.observable.row_counts()
        ok = counts["alarms"] > 20 and counts["defects"] > 1000
        return ok, (f"a null fab still runs: {counts['alarms']} alarms, "
                    f"{counts['defects']} defects")
    if name == "repair_in_window":
        event = truth["events"][0]
        response = event["maintenance_response"]
        return response is not None, (
            f"maintenance response: {response['maint_id'] if response else None}")
    if name == "trend_positive":
        scores = _channel(dataset, "cd_trend")
        label = _target_label(truth)
        value = scores.get(label)
        return (value is not None and value > 0.0,
                f"{label} trend {value:+.5f}" if value is not None
                else f"{label} absent")
    if name == "confound_present":
        condition = _routing_condition(dataset)
        if condition is None:
            return False, "no routing condition declared"
        shares = product_tool_exposure(
            dataset.db_path, condition["start"], condition["end"])
        product, tool = condition["product"], condition["tool"]
        inside = shares["inside"].get(product, {}).get(tool, 0.0)
        outside = shares["outside"].get(product, {}).get(tool, 0.0)
        return inside - outside > 0.3, (
            f"{product} on {tool}: {outside:.3f} outside -> {inside:.3f} inside")
    if name == "confound_imperfect":
        condition = _routing_condition(dataset)
        if condition is None:
            return False, "no routing condition declared"
        shares = product_tool_exposure(
            dataset.db_path, condition["start"], condition["end"])
        product, tool = condition["product"], condition["tool"]
        inside = shares["inside"]
        escaped = (1.0 - inside.get(product, {}).get(tool, 0.0)) \
            * inside.get(product, {}).get("_runs", 0.0)
        visitors = sum(entry.get(tool, 0.0) * entry.get("_runs", 0.0)
                       for name_, entry in inside.items() if name_ != product)
        ok = escaped >= 5 and visitors >= 20
        return ok, (f"{escaped:.0f} dedicated-product runs went elsewhere, "
                    f"{visitors:.0f} other-product runs came here")
    if name == "arc_ordered":
        from fabeval.acceptance import arc_ordering

        ordered, detail = arc_ordering(dataset)
        return ordered, detail
    if name == "maintenance_contrast":
        label = _target_label(truth)
        contrast = maintenance_contrast(dataset.db_path, label)
        if not contrast.get("repairs"):
            return False, "no unscheduled maintenance on the chamber"
        before, after = contrast["exposed_before"], contrast["exposed_after"]
        control_before = contrast["control_before"]
        control_after = contrast["control_after"]
        if None in (before, after, control_before, control_after):
            return False, "too few inspections either side of the repair"
        # Difference in differences: the chamber's own change across its
        # repair, net of whatever the fab did over the same stretch. What is
        # required is that maintenance *coincided with a measurable change on
        # this chamber specifically* - the direction is not asserted, because
        # a partial recovery on a load that keeps accumulating can legitimately
        # go either way and pinning a sign would be asserting an outcome the
        # physics does not guarantee.
        moved = (after - before) - (control_after - control_before)
        return abs(moved) >= 1.0, (
            f"defects/inspection {before:.1f} -> {after:.1f} "
            f"(control {control_before:.1f} -> {control_after:.1f}); "
            f"difference in differences {moved:+.2f}")
    raise KeyError(f"unknown structural expectation {name!r}")


def _target_label(truth: Mapping[str, Any]) -> str:
    event = truth["events"][0]
    return f"{event['target']['tool']}/{event['target']['chamber']}"


def _routing_condition(dataset: Any) -> dict[str, Any] | None:
    """The declared routing window, with its bounds as wall-clock timestamps.

    Truth records the condition in scenario *days* — it is declared
    configuration — while the run log records timestamps. The conversion needs
    the world clock, and that is an **observable** value: `dataset_meta`
    carries `time_origin` precisely so a consumer can turn one into the other
    without a second source. Reading it from there rather than from the world
    object keeps this arithmetic on the same footing as the query it feeds.
    """
    from datetime import datetime, timedelta

    for distractor in dataset.truth["distractors"]:
        if distractor["kind"] != "routing_condition":
            continue
        condition = distractor["condition"]
        (meta,) = dataset.observable.rows("dataset_meta")
        columns = _META_COLUMNS
        origin = datetime.fromisoformat(meta[columns.index("time_origin")])
        return {
            "product": condition["product"],
            "tool": condition["tool"],
            "start": (origin + timedelta(days=condition["start_day"])
                      ).isoformat(sep=" ", timespec="seconds"),
            "end": (origin + timedelta(days=condition["end_day"])
                    ).isoformat(sep=" ", timespec="seconds"),
        }
    return None


def evaluate_expectation(dataset: Any,
                         expectation: Mapping[str, Any]) -> tuple[bool, str]:
    """Score one dataset against its declared expectation."""
    notes: list[str] = []
    ok = True

    if len(dataset.truth["events"]) != expectation["events"]:
        return False, (f"expected {expectation['events']} event(s), got "
                       f"{len(dataset.truth['events'])}")

    leading = 0
    for channel, label, floor, worst_rank, required in expectation["channels"]:
        scores = _channel(dataset, channel)
        if label not in scores:
            ok = ok and not required
            notes.append(f"{channel}: {label} absent")
            continue
        sigma = zscore(scores, label)
        position = rank(scores, label)
        if position == 1:
            leading += 1
        good = sigma >= floor and position <= worst_rank
        if required and not good:
            ok = False
        role = "required" if required else "corroborating"
        bounds = f", need >={floor} and <={worst_rank}" if required else ""
        notes.append(
            f"{channel}: {label} z={sigma:+.2f} rank {position}/{len(scores)} "
            f"({role}{bounds}) "
            f"{'ok' if good else ('MISS' if required else 'quiet')}")

    minimum = expectation.get("min_leading", 0)
    if expectation["channels"] and leading < minimum:
        ok = False
        notes.append(f"the planted chamber leads {leading} channel(s); this "
                     f"scenario must lead at least {minimum}")

    for channel, cap in (expectation.get("max_sigma") or {}).items():
        scores = _channel(dataset, channel)
        label = _target_label(dataset.truth)
        if label not in scores:
            continue
        sigma = zscore(scores, label)
        if sigma > cap:
            ok = False
            notes.append(f"{channel}: {label} z={sigma:+.2f} exceeds the "
                         f"{cap} cap this scenario is specified to stay under")

    for name in expectation.get("structure", ()):
        good, detail = _structure(dataset, name)
        ok = ok and good
        notes.append(f"{name}: {detail} {'ok' if good else 'MISS'}")

    return ok, "; ".join(notes)
