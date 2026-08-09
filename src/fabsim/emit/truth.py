"""
truth.py — the hidden plane: `fabsim.truth/v1`, the benchmark's answer key.

`GROUND_TRUTH_CONTRACT.md` §3, written from the **realization** rather than
from the configuration. That distinction is the whole reason this artifact
exists: the config states intent ("an edge-uniformity fault on ETCH-02/B from
day 35"), and what the benchmark has to score against is what actually
happened at this seed — which runs really went through that chamber, which
wafers are really in the cohort, when the alarm really fired, what the repair
really recovered. Every field below is read off the realized world; where the
realization holds the answer, the config is not consulted.

    Realization        MechanismRealization, DistractorRealization,
      (hidden)         LatentTrajectory, LatentReset
    FabResponse        alarm_details (condition vs background), repairs
    DefectPopulation   DefectOrigin              ┐  hidden records, all of
    DiePopulation      DieOutcome                ┘  them beside their
                              │                     observable twins
                              ▼
                      truth/truth.json

**This module writes; it never enriches the observable plane.** It imports no
emitter and returns no rows; `fabsim.emit.observable` does not import it, and
neither is reachable from `fabops` (ADR-013). The only join between the two
planes is `dataset_id`, and the only place that join is allowed is `eval/`.

**Two fields of the §3 sketch are deliberately not emitted**, both because
producing them would mean inventing rather than realizing:

* `affected_wafers[].expected_mechanism_share` — a qualitative bucket
  ("high"/"low") that nothing in the realization computes. The measured
  `exposure` beside it carries the same information as a number.
* `lots.priority`'s analogue has no truth-side counterpart; see
  `observable.py` for that one.

Both are recorded in ADR-023 rather than filled with a guess, because a truth
file whose fields are partly measured and partly invented is worse than one
that is smaller and entirely measured.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabsim.defects import DefectPopulation
from fabsim.die import DiePopulation
from fabsim.latent import MechanismRealization, Realization
from fabsim.scenario import ScenarioConfig
from fabsim.world import World

__all__ = [
    "TRUTH_SCHEMA",
    "build_truth",
    "emit_truth",
    "truth_json",
]

#: The contract this artifact speaks, versioned independently of the
#: observable schema (`GROUND_TRUTH_CONTRACT.md` §5): an evaluation-side need
#: must not force an observable-schema bump, or vice versa.
TRUTH_SCHEMA = "fabsim.truth/v1"


def _at(origin: datetime, minutes: int | None) -> str | None:
    if minutes is None:
        return None
    return (origin + timedelta(minutes=int(minutes))).isoformat(
        sep=" ", timespec="seconds")


def _causal_chain(world: World, latent: str) -> list[str]:
    """The planes this latent can reach, derived from the declared world.

    Not a narrative and not a lookup: a channel is listed because the world
    template gives it a nonzero sensitivity to this latent, and a defect
    origin because its intensity does. The die and yield planes are reached by
    every latent that reaches either of the two above, since a die reads the
    measurements and the defects of its own wafer. Derived, so it cannot
    disagree with the physics it describes.
    """
    chain = [f"latent.{latent}"]
    for channel in world.observation.channels:
        if dict(channel.sensitivities).get(latent, 0.0) != 0.0:
            table = ("metrology" if channel.kind == "metrology"
                     else "run_measurements")
            chain.append(f"{table}.{channel.name}")
    for origin in world.defects.origins:
        if dict(origin.sensitivities).get(latent, 0.0) != 0.0:
            chain.append(f"defects.{origin.origin}")
    if len(chain) > 1:
        chain += ["die_bins", "wafer_yield"]
    return chain


def _exposure(timeline: Any, wafer_id: int, chamber_ids: Sequence[int],
              start: int, end: int) -> float:
    """How much of a wafer's processing this activation actually reached.

    The share of the wafer's runs that ran on an affected chamber while the
    activation was live. A wafer that saw the chamber once out of fourteen
    steps is exposed differently from one that saw it twice, and a benchmark
    that scored them alike would be scoring the configuration rather than the
    realization.
    """
    runs = timeline.runs_of_wafer(wafer_id)
    if not runs:
        return 0.0
    hit = sum(1 for run in runs
              if run.chamber_id in chamber_ids
              and run.end_min >= start and run.start_min <= end)
    return hit / len(runs)


def _expected_impact(timeline: Any, die: DiePopulation,
                     affected: Sequence[int]) -> dict[str, Any]:
    """The generator's own accounting of the mediated yield effect.

    Computed from the kill model's output, never injected — and computed
    **within product**, because products differ by up to ten yield points and
    a raw cohort mean would mostly measure which products happened to be in
    the cohort. `None` where the comparison has no support at this seed, which
    is honest: a cohort of three wafers has no cohort deficit worth scoring.
    """
    exposed = set(affected)
    by_product: dict[int, tuple[list[float], list[float]]] = {}
    for summary in die.wafer_yield:
        product_id = timeline.lot(summary.lot_id).product_id
        hit, rest = by_product.setdefault(product_id, ([], []))
        (hit if summary.wafer_id in exposed else rest).append(
            summary.yield_pct)

    deltas: list[tuple[int, float]] = []
    for product_id, (hit, rest) in sorted(by_product.items()):
        if len(hit) < 3 or len(rest) < 3:
            continue
        deltas.append((len(hit),
                       sum(hit) / len(hit) - sum(rest) / len(rest)))
    size = sum(1 for s in die.wafer_yield if s.wafer_id in exposed)
    if not deltas:
        return {"cohort_yield_delta_pts": None, "cohort_size": size}
    weight = sum(n for n, _d in deltas)
    return {
        "cohort_yield_delta_pts": round(
            sum(n * d for n, d in deltas) / weight, 4),
        "cohort_size": size,
    }


def _event(index: int, record: MechanismRealization, *, world: World,
           timeline: Any, response: Any, die: DiePopulation,
           origin: datetime) -> dict[str, Any]:
    """One activation, as intent *and* realization (`GROUND_TRUTH_CONTRACT` §3)."""
    chambers = set(record.chamber_ids)
    start, end = record.active_from_minute, record.active_to_minute

    affected_runs = sorted(
        run.run_id for run in timeline.runs
        if run.chamber_id in chambers
        and run.end_min >= start and run.start_min <= end)
    affected_wafers = sorted(
        {run.wafer_id for run in timeline.runs
         if run.chamber_id in chambers
         and run.end_min >= start and run.start_min <= end})

    # Alarms this activation's chambers raised *because of a condition*, after
    # its onset. `kind` lives in the hidden `alarm_details`; the observable
    # `Alarm` has no field for it, which is why this can only be assembled
    # here.
    alarms = sorted(
        alarm.alarm_id for alarm in response.alarms
        if alarm.chamber_id in chambers and alarm.minute >= record.onset_minute
        and response.detail(alarm.alarm_id).kind == "condition")

    repairs = [r for r in response.repairs
               if r.chamber_id in chambers and r.end_min >= record.onset_minute]
    maintenance_response: dict[str, Any] | None = None
    if repairs:
        first = min(repairs, key=lambda r: r.end_min)
        fractions = [reset.fraction
                     for reset in response.realization.resets
                     if reset.maint_id == first.maint_id
                     and reset.latent == record.latent]
        maintenance_response = {
            "maint_id": first.maint_id,
            "repair_time": _at(origin, first.end_min),
            "recovery_fraction": (round(fractions[0], 6) if fractions
                                  else None),
        }

    return {
        "event_id": f"E{index + 1}",
        "mechanism": record.mechanism,
        "latent": record.latent,
        "target": {"tool": record.tool_name,
                   "chamber": record.chamber_name,
                   "chamber_ids": sorted(chambers)},
        "onset": _at(origin, record.onset_minute),
        "end": _at(origin, end),
        "profile": dict(record.profile),
        "severity": record.severity,
        "severity_realized": {
            "aggregate_shift_sigma": round(record.realized_shift_sigma, 6),
            "realized_magnitude": record.realized_magnitude,
            "nominal_magnitude": record.nominal_magnitude,
        },
        "causal_chain": _causal_chain(world, record.latent),
        "alarms_emitted": alarms,
        "maintenance_response": maintenance_response,
        "affected_runs": affected_runs,
        "affected_wafers": [
            {"wafer_id": wafer_id,
             "exposure": round(_exposure(timeline, wafer_id, sorted(chambers),
                                         start, end), 6)}
            for wafer_id in affected_wafers],
        "expected_impact": _expected_impact(timeline, die, affected_wafers),
    }


def _distractors(world: World, realization: Realization,
                 config: ScenarioConfig) -> list[dict[str, Any]]:
    """Every benign structure a diagnosis engine might wrongly accuse.

    Listing them is what makes false-attribution scoring exact rather than
    judgemental (`GROUND_TRUTH_CONTRACT.md` §3). Three kinds, and the first is
    the one most easily forgotten:

    * the **standing** benign offsets rule F11 puts on every chamber of every
      world, faulted or not — they exist without being declared, they reach
      into the subtle-severity band, and a null dataset has them too;
    * the **declared** distractor mechanisms a scenario asked for, with the
      chambers they actually widened;
    * the scenario's **routing conditions**, which are observable by design
      (ADR-015) and are exactly the confounder scenario G is about.
    """
    entries: list[dict[str, Any]] = [{
        "kind": "benign_offset_baseline",
        "declared": False,
        "note": "every tool and chamber carries a permanent offset on every "
                "latent (rule F11); attribution to any of them is a false "
                "positive",
        "latents": list(world.observation.latents),
        "chamber_count": len(world.chambers),
    }]
    for record in realization.distractors:
        entries.append({
            "kind": record.mechanism,
            "declared": True,
            "magnitude": record.magnitude,
            "target": {"tool": record.tool_name,
                       "chamber": record.chamber_name,
                       "chamber_ids": sorted(record.chamber_ids)},
            "added": [{"chamber_id": chamber_id, "latent": latent,
                       "offset": offset}
                      for chamber_id, latent, offset in record.added],
            "note": "declared benign structure; attribution to this entity is "
                    "a false positive",
        })
    for condition in config.routing_conditions:
        entries.append({
            "kind": "routing_condition",
            "declared": True,
            "note": "observable by design: the routing shift appears in `runs` "
                    "and a diagnosis engine is expected to control for it",
            "condition": dict(condition),
        })
    return entries


def _latent_summaries(realization: Realization,
                      chambers: Sequence[int]) -> dict[str, list[float]]:
    """Weekly-resolution latent trajectories for the affected entities.

    Enough for onset-error scoring without storing the full hourly state
    (`GROUND_TRUTH_CONTRACT.md` §3). Keyed `chamber_id:<id>.<latent>`, values
    rounded — a benchmark scores a week, not a float's last bit.
    """
    summaries: dict[str, list[float]] = {}
    for chamber_id in sorted(set(chambers)):
        for latent in realization.latents:
            trajectory = realization.trajectory(chamber_id, latent)
            summaries[f"chamber_id:{chamber_id}.{latent}"] = [
                round(value, 9) for value in trajectory.weekly_means()]
    return summaries


def build_truth(config: ScenarioConfig, response: Any,
                defects: DefectPopulation, die: DiePopulation, *,
                dataset_id: str, scenario_id: str, seed: int,
                fabsim_version: str, schema_version: str) -> dict[str, Any]:
    """Assemble `fabsim.truth/v1` for one realized dataset. Pure; no I/O."""
    world: World = response.world
    realization: Realization = response.realization
    timeline = response.timeline
    origin = world.time_origin

    events = [
        _event(index, record, world=world, timeline=timeline,
               response=response, die=die, origin=origin)
        for index, record in enumerate(realization.mechanisms)]

    affected_chambers = [chamber_id for record in realization.mechanisms
                         for chamber_id in record.chamber_ids]

    return {
        "schema": TRUTH_SCHEMA,
        "dataset_id": dataset_id,
        "scenario_id": scenario_id,
        "scenario_name": config.name,
        "config_sha256": config.config_sha256,
        "world_sha256": world.world_sha256,
        "seed": seed,
        "fabsim_version": fabsim_version,
        "schema_version": schema_version,
        "events": events,
        "distractors": _distractors(world, realization, config),
        "latent_summaries": _latent_summaries(realization, affected_chambers),
        "hidden_counts": {
            # Sizes of the hidden records, so a truth reader can tell that a
            # plane exists without the plane being copied into the file.
            "defect_origins": len(defects.origins),
            "die_outcomes": len(die.outcomes),
            "latent_resets": len(realization.resets),
            "benign_offsets": len(realization.offsets),
        },
    }


def truth_json(truth: Mapping[str, Any]) -> str:
    """Canonical JSON: sorted keys, fixed separators, ASCII, trailing newline.

    Byte-comparable across runs and hosts, which is what `PHASE_1_ACCEPTANCE`
    A1 §3 asks of this artifact.
    """
    return json.dumps(truth, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False) + "\n"


def emit_truth(config: ScenarioConfig, response: Any,
               defects: DefectPopulation, die: DiePopulation,
               directory: Path, *, dataset_id: str, scenario_id: str,
               seed: int, fabsim_version: str,
               schema_version: str) -> dict[str, Any]:
    """Write `truth/truth.json` under `directory`, and return what was written.

    The subdirectory is the boundary made visible: a dataset can be handed to
    an analytical consumer by giving it `fab.db`, and the hidden plane is one
    directory listing away from being audited (`GROUND_TRUTH_CONTRACT.md` §2).
    """
    truth = build_truth(config, response, defects, die, dataset_id=dataset_id,
                        scenario_id=scenario_id, seed=seed,
                        fabsim_version=fabsim_version,
                        schema_version=schema_version)
    target = directory / "truth"
    target.mkdir(parents=True, exist_ok=True)
    (target / "truth.json").write_bytes(truth_json(truth).encode("utf-8"))
    return truth
