"""
truthschema.py — the `fabsim.truth/v1` validator.

`PHASE_1_ACCEPTANCE.md` A10 requires that truth files are *valid against*
their schema, and until now nothing checked. This is that check, and it lives
here rather than in `fabsim` on purpose: a generator that validates its own
output against its own idea of the contract is marking its own homework. The
validator reads `GROUND_TRUTH_CONTRACT.md` §3 as the authority and knows
nothing about how truth was produced.

Two rules shape it:

* **It never repairs.** A missing field, a wrong type, a dangling reference
  and an out-of-range exposure are all rejections. Silently defaulting one
  would let a truth file that cannot be scored look scoreable.
* **A rejection names the field.** `events[0].affected_wafers[3].exposure`
  rather than "invalid truth" — a validator that only says *that* something
  is wrong makes the next hour someone else's problem.

What it deliberately does **not** do: invent fields. Two of the §3 sketch's
entries are not emitted and their absence is *accepted*, because ADR-023 §5
recorded why — `affected_wafers[].expected_mechanism_share` is a qualitative
bucket nothing realizes, and the measured `exposure` beside it carries the
same information. Requiring it here would be requiring the emitter to guess.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "TRUTH_SCHEMA",
    "TruthValidationError",
    "validate_truth",
    "validate_truth_file",
]

#: The contract this validator implements.
TRUTH_SCHEMA = "fabsim.truth/v1"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_DATASET_ID = re.compile(r"scn-[0-9a-f]{12}-s\d+")
_SCENARIO_ID = re.compile(r"scn-[0-9a-f]{12}")
_EVENT_ID = re.compile(r"E\d+")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
#: `chamber_id:<id>.<latent>` — the key shape `latent_summaries` uses.
_SUMMARY_KEY = re.compile(r"chamber_id:\d+\.[a-z][a-z0-9_]*")

_TOP_LEVEL = ("schema", "dataset_id", "scenario_id", "scenario_name",
              "config_sha256", "world_sha256", "seed", "fabsim_version",
              "schema_version", "events", "distractors", "latent_summaries",
              "hidden_counts")

_EVENT_FIELDS = ("event_id", "mechanism", "latent", "target", "onset", "end",
                 "profile", "severity", "severity_realized", "causal_chain",
                 "alarms_emitted", "maintenance_response", "affected_runs",
                 "affected_wafers", "expected_impact")

_HIDDEN_COUNTS = ("defect_origins", "die_outcomes", "latent_resets",
                  "benign_offsets")

#: Closed vocabularies the truth plane may use. Mirrored from the world and
#: scenario contracts rather than imported, so a change to either has to be
#: made here too and cannot slip past the validator unnoticed — the same
#: reason `fabsim.world` mirrors the mechanism names.
_SEVERITIES = ("subtle", "moderate", "obvious")
_PROFILE_TYPES = ("step", "ramp", "intermittent")
_MECHANISMS = ("benign_offset", "chamber_edge_uniformity", "param_drift",
               "particle_excursion")
_LATENTS = ("edge_uniformity", "param_bias", "particle_load")


class TruthValidationError(ValueError):
    """A truth artifact was rejected. `path` says where.

    Carries the offending field path so a failure is actionable, in the same
    shape `ScenarioConfigError` and `WorldTemplateError` already use — three
    contracts, one rejection idiom.
    """

    def __init__(self, message: str, path: str = "") -> None:
        self.path = path
        self.reason = message
        super().__init__(f"{path}: {message}" if path else message)


# ------------------------------------------------------------------ helpers


def _at(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else str(key)


def _require(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if not isinstance(obj, Mapping) or key not in obj:
        raise TruthValidationError(f"missing required field {key!r}",
                                   _at(path, key))
    return obj[key]


def _reject_unknown(obj: Mapping[str, Any], allowed: Sequence[str],
                    path: str) -> None:
    unknown = sorted(k for k in obj if k not in allowed)
    if unknown:
        raise TruthValidationError(
            "unknown field(s) " + ", ".join(repr(k) for k in unknown)
            + "; allowed: " + ", ".join(sorted(allowed)), path)


def _text(value: Any, path: str, pattern: "re.Pattern[str] | None" = None,
          *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TruthValidationError(
            f"expected a string, got {type(value).__name__}", path)
    if not value and not allow_empty:
        raise TruthValidationError("must not be empty", path)
    if pattern is not None and not pattern.fullmatch(value):
        raise TruthValidationError(
            f"{value!r} does not match {pattern.pattern!r}", path)
    return value


def _choice(value: Any, path: str, choices: Sequence[str]) -> str:
    text = _text(value, path)
    if text not in choices:
        raise TruthValidationError(
            f"{text!r} is not one of: " + ", ".join(sorted(choices)), path)
    return text


def _integer(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TruthValidationError(
            f"expected an integer, got {type(value).__name__}", path)
    if minimum is not None and value < minimum:
        raise TruthValidationError(f"must be >= {minimum}, got {value}", path)
    return value


def _number(value: Any, path: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TruthValidationError(
            f"expected a number, got {type(value).__name__}", path)
    number = float(value)
    if minimum is not None and number < minimum:
        raise TruthValidationError(f"must be >= {minimum}, got {number}", path)
    if maximum is not None and number > maximum:
        raise TruthValidationError(f"must be <= {maximum}, got {number}", path)
    return number


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TruthValidationError(
            f"expected an object, got {type(value).__name__}", path)
    return value


def _array(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise TruthValidationError(
            f"expected an array, got {type(value).__name__}", path)
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise TruthValidationError(
            f"expected a boolean, got {type(value).__name__}", path)
    return value


# ------------------------------------------------------------------- events


def _validate_target(raw: Any, path: str) -> None:
    target = _mapping(raw, path)
    _reject_unknown(target, ("tool", "chamber", "chamber_ids"), path)
    _text(_require(target, "tool", path), _at(path, "tool"))
    chamber = target.get("chamber")
    if chamber is not None:
        _text(chamber, _at(path, "chamber"))
    ids = _array(_require(target, "chamber_ids", path),
                 _at(path, "chamber_ids"))
    if not ids:
        raise TruthValidationError(
            "an activation that reached no chamber is not an activation",
            _at(path, "chamber_ids"))
    for index, value in enumerate(ids):
        _integer(value, _at(_at(path, "chamber_ids"), index), minimum=1)
    if sorted(ids) != list(ids):
        raise TruthValidationError("must be sorted", _at(path, "chamber_ids"))


def _validate_profile(raw: Any, path: str) -> None:
    profile = _mapping(raw, path)
    kind = _choice(_require(profile, "type", path), _at(path, "type"),
                   _PROFILE_TYPES)
    allowed = ("type", "ramp_days") if kind == "ramp" else ("type",)
    _reject_unknown(profile, allowed, path)
    if kind == "ramp":
        _number(_require(profile, "ramp_days", path), _at(path, "ramp_days"),
                minimum=0.0)


def _validate_severity_realized(raw: Any, path: str) -> None:
    realized = _mapping(raw, path)
    _reject_unknown(realized, ("aggregate_shift_sigma", "realized_magnitude",
                               "nominal_magnitude"), path)
    # Realized, not configured: this is the number the latent plane produced,
    # and a benchmark that scored against the ladder instead would be scoring
    # the configuration (`GROUND_TRUTH_CONTRACT.md` §1).
    _number(_require(realized, "aggregate_shift_sigma", path),
            _at(path, "aggregate_shift_sigma"))
    _number(_require(realized, "realized_magnitude", path),
            _at(path, "realized_magnitude"))
    _number(_require(realized, "nominal_magnitude", path),
            _at(path, "nominal_magnitude"))


def _validate_causal_chain(raw: Any, path: str, latent: str) -> None:
    chain = _array(raw, path)
    if not chain:
        raise TruthValidationError(
            "an activation that reaches nothing has no chain", path)
    head = _text(chain[0], _at(path, 0))
    if head != f"latent.{latent}":
        raise TruthValidationError(
            f"a chain must begin at the latent it drives; expected "
            f"'latent.{latent}', got {head!r}", _at(path, 0))
    for index, link in enumerate(chain):
        text = _text(link, _at(path, index))
        if "." in text:
            plane, _, name = text.partition(".")
            _text(plane, _at(path, index), _IDENTIFIER)
            _text(name, _at(path, index))
        else:
            _text(text, _at(path, index), _IDENTIFIER)


def _validate_maintenance_response(raw: Any, path: str) -> None:
    if raw is None:
        # Honest: not every activation earns a repair inside the horizon.
        return
    response = _mapping(raw, path)
    _reject_unknown(response, ("maint_id", "repair_time",
                               "recovery_fraction"), path)
    _integer(_require(response, "maint_id", path), _at(path, "maint_id"),
             minimum=1)
    _text(_require(response, "repair_time", path), _at(path, "repair_time"),
          _TIMESTAMP)
    fraction = response.get("recovery_fraction")
    if fraction is not None:
        # A no-fix draw is 0.0 and is a legitimate outcome
        # (`CAUSAL_MECHANISM_MODEL.md` §6); a perfect one is not.
        _number(fraction, _at(path, "recovery_fraction"),
                minimum=0.0, maximum=1.0)


def _validate_affected_wafers(raw: Any, path: str) -> list[int]:
    wafers = _array(raw, path)
    seen: list[int] = []
    for index, entry in enumerate(wafers):
        where = _at(path, index)
        item = _mapping(entry, where)
        _reject_unknown(item, ("wafer_id", "exposure"), where)
        wafer_id = _integer(_require(item, "wafer_id", where),
                            _at(where, "wafer_id"), minimum=1)
        # Exposure is a *share* of the wafer's runs, so it is bounded, and it
        # is strictly positive: a wafer with no exposure is not affected.
        exposure = _number(_require(item, "exposure", where),
                           _at(where, "exposure"), minimum=0.0, maximum=1.0)
        if exposure <= 0.0:
            raise TruthValidationError(
                "an affected wafer with zero exposure was never exposed",
                _at(where, "exposure"))
        seen.append(wafer_id)
    if sorted(seen) != seen:
        raise TruthValidationError("must be sorted by wafer_id", path)
    if len(set(seen)) != len(seen):
        raise TruthValidationError("duplicate wafer_id", path)
    return seen


def _validate_expected_impact(raw: Any, path: str, cohort: int) -> None:
    impact = _mapping(raw, path)
    _reject_unknown(impact, ("cohort_yield_delta_pts", "cohort_size"), path)
    delta = impact.get("cohort_yield_delta_pts")
    if delta is not None:
        # `None` where the within-product comparison had no support; a number
        # otherwise. Either is honest, a missing key is not.
        _number(delta, _at(path, "cohort_yield_delta_pts"))
    if "cohort_yield_delta_pts" not in impact:
        raise TruthValidationError(
            "missing required field 'cohort_yield_delta_pts'",
            _at(path, "cohort_yield_delta_pts"))
    size = _integer(_require(impact, "cohort_size", path),
                    _at(path, "cohort_size"), minimum=0)
    if size > cohort:
        raise TruthValidationError(
            f"cohort_size {size} exceeds the {cohort} affected wafers",
            _at(path, "cohort_size"))


def _validate_event(raw: Any, path: str, index: int) -> None:
    event = _mapping(raw, path)
    _reject_unknown(event, _EVENT_FIELDS, path)
    for field in _EVENT_FIELDS:
        _require(event, field, path)

    _text(event["event_id"], _at(path, "event_id"), _EVENT_ID)
    if event["event_id"] != f"E{index + 1}":
        raise TruthValidationError(
            f"event ids are positional; expected 'E{index + 1}'",
            _at(path, "event_id"))
    _choice(event["mechanism"], _at(path, "mechanism"), _MECHANISMS)
    latent = _choice(event["latent"], _at(path, "latent"), _LATENTS)
    _validate_target(event["target"], _at(path, "target"))

    onset = _text(event["onset"], _at(path, "onset"), _TIMESTAMP)
    end = _text(event["end"], _at(path, "end"), _TIMESTAMP)
    if end < onset:
        raise TruthValidationError(
            f"end {end} precedes onset {onset}", _at(path, "end"))

    _validate_profile(event["profile"], _at(path, "profile"))
    _choice(event["severity"], _at(path, "severity"), _SEVERITIES)
    _validate_severity_realized(event["severity_realized"],
                                _at(path, "severity_realized"))
    _validate_causal_chain(event["causal_chain"], _at(path, "causal_chain"),
                           latent)

    alarms = _array(event["alarms_emitted"], _at(path, "alarms_emitted"))
    for position, alarm_id in enumerate(alarms):
        _integer(alarm_id, _at(_at(path, "alarms_emitted"), position),
                 minimum=1)
    if sorted(alarms) != list(alarms):
        raise TruthValidationError("must be sorted",
                                   _at(path, "alarms_emitted"))

    _validate_maintenance_response(event["maintenance_response"],
                                   _at(path, "maintenance_response"))

    runs = _array(event["affected_runs"], _at(path, "affected_runs"))
    for position, run_id in enumerate(runs):
        _integer(run_id, _at(_at(path, "affected_runs"), position), minimum=1)
    if sorted(runs) != list(runs):
        raise TruthValidationError("must be sorted",
                                   _at(path, "affected_runs"))

    wafers = _validate_affected_wafers(event["affected_wafers"],
                                       _at(path, "affected_wafers"))
    if runs and not wafers:
        raise TruthValidationError(
            "runs happened but no wafer is recorded as affected",
            _at(path, "affected_wafers"))
    _validate_expected_impact(event["expected_impact"],
                              _at(path, "expected_impact"), len(wafers))


# -------------------------------------------------------------- distractors


def _validate_distractor(raw: Any, path: str) -> str:
    entry = _mapping(raw, path)
    kind = _text(_require(entry, "kind", path), _at(path, "kind"),
                 _IDENTIFIER)
    _boolean(_require(entry, "declared", path), _at(path, "declared"))
    if "note" in entry:
        _text(entry["note"], _at(path, "note"))
    if kind == "benign_offset_baseline":
        _reject_unknown(entry, ("kind", "declared", "note", "latents",
                                "chamber_count"), path)
        latents = _array(_require(entry, "latents", path),
                         _at(path, "latents"))
        for index, name in enumerate(latents):
            _choice(name, _at(_at(path, "latents"), index), _LATENTS)
        _integer(_require(entry, "chamber_count", path),
                 _at(path, "chamber_count"), minimum=1)
    elif kind == "routing_condition":
        _reject_unknown(entry, ("kind", "declared", "note", "condition"),
                        path)
        _mapping(_require(entry, "condition", path), _at(path, "condition"))
    else:
        _choice(kind, _at(path, "kind"), _MECHANISMS)
        _reject_unknown(entry, ("kind", "declared", "note", "magnitude",
                                "target", "added"), path)
        _text(_require(entry, "magnitude", path), _at(path, "magnitude"))
        _validate_target(_require(entry, "target", path), _at(path, "target"))
        added = _array(_require(entry, "added", path), _at(path, "added"))
        for index, item in enumerate(added):
            where = _at(_at(path, "added"), index)
            record = _mapping(item, where)
            _reject_unknown(record, ("chamber_id", "latent", "offset"), where)
            _integer(_require(record, "chamber_id", where),
                     _at(where, "chamber_id"), minimum=1)
            _choice(_require(record, "latent", where), _at(where, "latent"),
                    _LATENTS)
            _number(_require(record, "offset", where), _at(where, "offset"))
    return kind


# ------------------------------------------------------------- the front door


def validate_truth(truth: Any) -> None:
    """Validate one `fabsim.truth/v1` artifact, or raise.

    Structure, types, vocabularies, ordering, ranges and the internal
    references that only truth can check — an event's `expected_impact`
    cohort cannot exceed its own affected-wafer list, `latent_summaries` must
    cover the chambers the events name, and `hidden_counts` must describe a
    hidden plane that exists.
    """
    root = _mapping(truth, "")
    _reject_unknown(root, _TOP_LEVEL, "")
    for field in _TOP_LEVEL:
        _require(root, field, "")

    if root["schema"] != TRUTH_SCHEMA:
        raise TruthValidationError(
            f"expected {TRUTH_SCHEMA!r}, got {root['schema']!r}", "schema")

    dataset_id = _text(root["dataset_id"], "dataset_id", _DATASET_ID)
    scenario_id = _text(root["scenario_id"], "scenario_id", _SCENARIO_ID)
    if not dataset_id.startswith(scenario_id + "-s"):
        raise TruthValidationError(
            f"dataset_id {dataset_id!r} does not extend scenario_id "
            f"{scenario_id!r}", "dataset_id")
    seed = _integer(root["seed"], "seed", minimum=0)
    if not dataset_id.endswith(f"-s{seed:03d}") and \
            not dataset_id.endswith(f"-s{seed}"):
        raise TruthValidationError(
            f"dataset_id {dataset_id!r} does not carry seed {seed}", "seed")

    # The scenario slug lives here and nowhere observable — this is the one
    # artifact allowed to name it (`GROUND_TRUTH_CONTRACT.md` §2).
    _text(root["scenario_name"], "scenario_name")
    _text(root["config_sha256"], "config_sha256", _SHA256)
    _text(root["world_sha256"], "world_sha256", _SHA256)
    _text(root["fabsim_version"], "fabsim_version",
          re.compile(r"\d+\.\d+\.\d+"))
    _text(root["schema_version"], "schema_version", re.compile(r"\d+\.\d+"))

    events = _array(root["events"], "events")
    named_chambers: set[int] = set()
    for index, event in enumerate(events):
        _validate_event(event, _at("events", index), index)
        named_chambers |= set(event["target"]["chamber_ids"])

    distractors = _array(root["distractors"], "distractors")
    if not distractors:
        # Even a null scenario has the standing benign offsets to be scored
        # against; an empty list would make false attribution unscoreable.
        raise TruthValidationError(
            "every world carries standing benign structure; an empty "
            "distractor list cannot be right", "distractors")
    kinds = [_validate_distractor(entry, _at("distractors", index))
             for index, entry in enumerate(distractors)]
    if "benign_offset_baseline" not in kinds:
        raise TruthValidationError(
            "the standing per-chamber benign offsets (rule F11) are missing; "
            "they exist in every world, declared or not", "distractors")

    summaries = _mapping(root["latent_summaries"], "latent_summaries")
    for key, series in summaries.items():
        where = _at("latent_summaries", key)
        _text(key, where, _SUMMARY_KEY)
        values = _array(series, where)
        if not values:
            raise TruthValidationError("an empty trajectory says nothing",
                                       where)
        for position, value in enumerate(values):
            _number(value, _at(where, position))
    covered = {int(key.split(":")[1].split(".")[0]) for key in summaries}
    missing = sorted(named_chambers - covered)
    if missing:
        raise TruthValidationError(
            f"no weekly trajectory for affected chamber(s) {missing}; onset "
            "error cannot be scored without one", "latent_summaries")
    if not events and summaries:
        raise TruthValidationError(
            "a scenario with no events has no affected entity to summarize",
            "latent_summaries")

    counts = _mapping(root["hidden_counts"], "hidden_counts")
    _reject_unknown(counts, _HIDDEN_COUNTS, "hidden_counts")
    for field in _HIDDEN_COUNTS:
        _integer(_require(counts, field, "hidden_counts"),
                 _at("hidden_counts", field), minimum=0)
    if counts["benign_offsets"] <= 0:
        raise TruthValidationError(
            "every chamber carries an offset on every latent (rule F11)",
            _at("hidden_counts", "benign_offsets"))


def validate_truth_file(path: Any) -> dict[str, Any]:
    """Read and validate a `truth.json`; return it. Raises on any fault."""
    import json
    from pathlib import Path

    target = Path(path)
    try:
        truth = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TruthValidationError(f"invalid JSON: {exc}", str(target)) from exc
    validate_truth(truth)
    return truth
