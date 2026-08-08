"""
scenario.py — the FabSim scenario configuration contract (`fabsim.scenario/v1`).

A scenario declares one synthetic fab world and the events that happen in it
(`SCENARIO_SPECIFICATION.md` §1). This module is the only place that reads
that declaration: it parses the JSON, validates it strictly, normalizes it to
a canonical form, and derives the deterministic identities that the manifest,
the truth artifact and the benchmark quote.

**Format is JSON** (ADR-014). The design treated config syntax as cosmetic and
left YAML-vs-JSON open; JSON wins because FabSim stays stdlib-only
(`FABSIM_DESIGN.md` §8) and a hand-written YAML subset parser would be a
second, weaker JSON with its own bugs. The header field keeps the documented
spelling, so ``{"fabsim": "scenario/v1"}`` *is* the `fabsim.scenario/v1`
contract.

**Identity** (`SCENARIO_SPECIFICATION.md` §5, ADR-013)::

    canonical config (name/description excluded)
            │ sha256
            ▼
      config_sha256 ──first 12 hex──▶ scenario_id   scn-3f9a1c7b2e4d
                                           │ + seed
                                           ▼
                                      dataset_id    scn-3f9a1c7b2e4d-s042

    (config_sha256, seed, fabsim version, schema version)
            │ sha256
            ▼
      build_fingerprint — the four inputs that fully determine a dataset,
      in one comparable value (the input side of acceptance test A1)

`name` and `description` are documentation, not semantics: they are excluded
from the hashed representation, so renaming a scenario or editing its prose
does not change its identity, and no observable identifier can carry the
scenario's name (anti-leakage rule D5). Everything else is semantic — change
any of it, including the default seed or the order of the event list, and the
identity changes.

Nothing environmental enters any identity: no wall clock, no file path, no
machine or user name, no locale, no environment variable. The same bytes
produce the same ids everywhere.

Scope note: this module validates *structure*. Resolving `world` against a
world template, and `mechanism` against the mechanism registry, happens at
build time in the slices that own those registries — a config can name a
world that does not exist yet, and the loader is not the place to pretend
otherwise.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabsim import SCHEMA_VERSION
from fabsim import __version__ as FABSIM_VERSION
from fabsim.rng import MASTER_SEED_MAX, MASTER_SEED_MIN, validate_master_seed

__all__ = [
    "CONTRACT",
    "DOCUMENTATION_KEYS",
    "HEADER_KEY",
    "HEADER_VALUE",
    "MAGNITUDES",
    "PROFILE_TYPES",
    "RECOVERY_MODES",
    "SEVERITIES",
    "DatasetIdentity",
    "ScenarioConfig",
    "ScenarioConfigError",
    "canonicalize",
    "derive_build_fingerprint",
    "derive_dataset_id",
    "from_mapping",
    "load_scenario",
    "load_scenario_text",
]

# ------------------------------------------------------------------ contract

#: The configuration contract this loader implements. In a file it is spelled
#: as the header field ``"fabsim": "scenario/v1"``.
CONTRACT = "fabsim.scenario/v1"
HEADER_KEY = "fabsim"
HEADER_VALUE = "scenario/v1"

#: Fields that document a scenario rather than define it. Excluded from the
#: hashed representation (`SCENARIO_SPECIFICATION.md` §5).
DOCUMENTATION_KEYS = ("name", "description")

#: Closed vocabularies. Every categorical value a scenario may state comes
#: from one of these lists — configs shift frequencies, they never mint
#: vocabulary (`ANTI_LEAKAGE_DESIGN.md` D2).
SEVERITIES = ("subtle", "moderate", "obvious")
PROFILE_TYPES = ("step", "ramp", "intermittent")
RECOVERY_MODES = ("none", "partial", "full")
MAGNITUDES = ("small", "moderate", "large")

SCENARIO_ID_PREFIX = "scn-"
SCENARIO_ID_HEX_CHARS = 12

#: Domain-separation tag for the build fingerprint, versioned like the RNG's.
IDENTITY_DOMAIN = "fabsim.dataset/v1"

_TOP_LEVEL_REQUIRED = (HEADER_KEY, "name", "world", "horizon_days", "lots",
                       "default_seed")
_TOP_LEVEL_OPTIONAL = ("description", "events", "distractors")
_TOP_LEVEL_KEYS = _TOP_LEVEL_REQUIRED + _TOP_LEVEL_OPTIONAL

_EVENT_REQUIRED = ("mechanism", "target", "onset_day", "severity")
_EVENT_OPTIONAL = ("profile", "response")
_EVENT_KEYS = _EVENT_REQUIRED + _EVENT_OPTIONAL

_DISTRACTOR_KEYS = ("mechanism", "target", "magnitude")
_TARGET_KEYS = ("tool", "chamber")
_RESPONSE_KEYS = ("alarm", "repair_delay_days_mean", "recovery")

#: Defaults filled in during canonicalization. Omitting an optional field and
#: writing its default are the same configuration and hash the same way.
_DEFAULT_PROFILE = {"type": "step"}
_DEFAULT_RESPONSE = {"alarm": False, "repair_delay_days_mean": 0.0,
                     "recovery": "none"}

# Mechanism and world template names are code-level identifiers; tool and
# chamber names are world vocabulary (`ETCH-02`, `B`) and say nothing about
# fault status (`SCENARIO_SPECIFICATION.md` §2).
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_]*")
_ENTITY_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: Byte-order mark. Some editors write one; it carries no meaning.
_BOM = chr(0xFEFF)


class ScenarioConfigError(ValueError):
    """A scenario configuration was rejected.

    `path` locates the offending field (``events[0].profile.ramp_days``) so a
    rejection tells an author where to look, not merely that something is
    wrong.
    """

    def __init__(self, message: str, path: str = "") -> None:
        self.path = path
        self.reason = message
        super().__init__(f"{path}: {message}" if path else message)


# ------------------------------------------------------- validation plumbing


def _at(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _require(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise ScenarioConfigError(f"missing required field {key!r}",
                                  _at(path, key))
    return obj[key]


def _reject_unknown(obj: Mapping[str, Any], allowed: Sequence[str],
                    path: str) -> None:
    unknown = sorted(k for k in obj if k not in allowed)
    if unknown:
        raise ScenarioConfigError(
            "unknown field(s) "
            + ", ".join(repr(k) for k in unknown)
            + "; allowed: "
            + ", ".join(sorted(allowed)),
            path,
        )


def _as_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioConfigError(
            f"expected an object, got {_type_name(value)}", path)
    return value


def _as_array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ScenarioConfigError(
            f"expected an array, got {_type_name(value)}", path)
    return value


def _as_text(value: Any, path: str, pattern: re.Pattern[str] | None = None,
             *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ScenarioConfigError(
            f"expected a string, got {_type_name(value)}", path)
    text = value.strip()
    if not text and not allow_empty:
        raise ScenarioConfigError("must not be empty", path)
    if pattern is not None and text and not pattern.fullmatch(text):
        raise ScenarioConfigError(
            f"{text!r} does not match {pattern.pattern!r}", path)
    return text


def _as_choice(value: Any, path: str, choices: Sequence[str]) -> str:
    text = _as_text(value, path)
    if text not in choices:
        raise ScenarioConfigError(
            f"{text!r} is not one of: " + ", ".join(choices), path)
    return text


def _as_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ScenarioConfigError(
            f"expected a boolean, got {_type_name(value)}", path)
    return value


def _as_int(value: Any, path: str, *, minimum: int | None = None,
            maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioConfigError(
            f"expected an integer, got {_type_name(value)}", path)
    if minimum is not None and value < minimum:
        raise ScenarioConfigError(f"must be >= {minimum}, got {value}", path)
    if maximum is not None and value > maximum:
        raise ScenarioConfigError(f"must be <= {maximum}, got {value}", path)
    return value


def _as_number(value: Any, path: str, *, minimum: float | None = None,
               greater_than: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioConfigError(
            f"expected a number, got {_type_name(value)}", path)
    number = float(value)
    if minimum is not None and number < minimum:
        raise ScenarioConfigError(f"must be >= {minimum}, got {number}", path)
    if greater_than is not None and number <= greater_than:
        raise ScenarioConfigError(f"must be > {greater_than}, got {number}",
                                  path)
    return number


# ------------------------------------------------------------ canonical form


def _canonical_target(raw: Any, path: str) -> dict[str, Any]:
    """A target names a tool, optionally narrowed to one of its chambers.

    An absent `chamber` is not defaulted: tool-wide and chamber-scoped targets
    are different declarations, and chambers are the primary locus of
    behaviour (`SCHEMA_V2_DESIGN.md` §2.9).
    """
    obj = _as_object(raw, path)
    _reject_unknown(obj, _TARGET_KEYS, path)
    target: dict[str, Any] = {
        "tool": _as_text(_require(obj, "tool", path), _at(path, "tool"),
                         _ENTITY_NAME_RE),
    }
    if "chamber" in obj:
        target["chamber"] = _as_text(obj["chamber"], _at(path, "chamber"),
                                     _ENTITY_NAME_RE)
    return target


def _canonical_profile(raw: Any, path: str) -> dict[str, Any]:
    obj = _as_object(raw, path)
    profile_type = _as_choice(_require(obj, "type", path), _at(path, "type"),
                              PROFILE_TYPES)
    # `ramp_days` is the only profile parameter the design defines; the other
    # profile types take none until the mechanism slice gives them meaning.
    allowed = ("type", "ramp_days") if profile_type == "ramp" else ("type",)
    _reject_unknown(obj, allowed, path)
    profile: dict[str, Any] = {"type": profile_type}
    if profile_type == "ramp":
        profile["ramp_days"] = _as_number(
            _require(obj, "ramp_days", path), _at(path, "ramp_days"),
            greater_than=0.0)
    return profile


def _canonical_response(raw: Any, path: str) -> dict[str, Any]:
    """How the simulated fab reacts: alarms, repair lag, recovery degree."""
    obj = _as_object(raw, path)
    _reject_unknown(obj, _RESPONSE_KEYS, path)
    return {
        "alarm": _as_bool(obj.get("alarm", _DEFAULT_RESPONSE["alarm"]),
                          _at(path, "alarm")),
        "repair_delay_days_mean": _as_number(
            obj.get("repair_delay_days_mean",
                    _DEFAULT_RESPONSE["repair_delay_days_mean"]),
            _at(path, "repair_delay_days_mean"), minimum=0.0),
        "recovery": _as_choice(
            obj.get("recovery", _DEFAULT_RESPONSE["recovery"]),
            _at(path, "recovery"), RECOVERY_MODES),
    }


def _canonical_event(raw: Any, path: str, horizon_days: int) -> dict[str, Any]:
    obj = _as_object(raw, path)
    _reject_unknown(obj, _EVENT_KEYS, path)
    onset_day = _as_number(_require(obj, "onset_day", path),
                           _at(path, "onset_day"), minimum=0.0)
    if onset_day >= horizon_days:
        raise ScenarioConfigError(
            f"onset_day {onset_day} falls outside the {horizon_days}-day "
            "horizon; an event that never starts is not an event",
            _at(path, "onset_day"))
    return {
        "mechanism": _as_text(_require(obj, "mechanism", path),
                              _at(path, "mechanism"), _IDENTIFIER_RE),
        "target": _canonical_target(_require(obj, "target", path),
                                    _at(path, "target")),
        "onset_day": onset_day,
        "profile": _canonical_profile(
            obj.get("profile", dict(_DEFAULT_PROFILE)), _at(path, "profile")),
        "severity": _as_choice(_require(obj, "severity", path),
                               _at(path, "severity"), SEVERITIES),
        "response": _canonical_response(obj.get("response", {}),
                                        _at(path, "response")),
    }


def _canonical_distractor(raw: Any, path: str) -> dict[str, Any]:
    """Benign structure the diagnosis must not blame."""
    obj = _as_object(raw, path)
    _reject_unknown(obj, _DISTRACTOR_KEYS, path)
    return {
        "mechanism": _as_text(_require(obj, "mechanism", path),
                              _at(path, "mechanism"), _IDENTIFIER_RE),
        "target": _canonical_target(_require(obj, "target", path),
                                    _at(path, "target")),
        "magnitude": _as_choice(_require(obj, "magnitude", path),
                                _at(path, "magnitude"), MAGNITUDES),
    }


def canonicalize(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a parsed configuration and return its canonical form.

    Canonical means: every optional field present at its default, every
    number in one representation (counts are integers, days are floats), every
    string stripped, list order preserved because it is semantic. Two
    configurations that mean the same thing canonicalize to the same object
    and therefore to the same hash.
    """
    obj = _as_object(raw, "")
    _reject_unknown(obj, _TOP_LEVEL_KEYS, "")

    header = _as_text(_require(obj, HEADER_KEY, ""), HEADER_KEY)
    if header != HEADER_VALUE:
        raise ScenarioConfigError(
            f"unsupported configuration version {header!r}; this loader "
            f"implements {CONTRACT} (header {HEADER_KEY}: {HEADER_VALUE!r})",
            HEADER_KEY)

    horizon_days = _as_int(_require(obj, "horizon_days", ""), "horizon_days",
                           minimum=1)
    events = _as_array(obj.get("events", []), "events")
    distractors = _as_array(obj.get("distractors", []), "distractors")

    return {
        HEADER_KEY: HEADER_VALUE,
        "name": _as_text(_require(obj, "name", ""), "name"),
        "description": _as_text(obj.get("description", ""), "description",
                                allow_empty=True),
        "world": _as_text(_require(obj, "world", ""), "world",
                          _IDENTIFIER_RE),
        "horizon_days": horizon_days,
        "lots": _as_int(_require(obj, "lots", ""), "lots", minimum=1),
        "default_seed": _as_int(_require(obj, "default_seed", ""),
                                "default_seed", minimum=MASTER_SEED_MIN,
                                maximum=MASTER_SEED_MAX),
        "events": [_canonical_event(event, f"events[{i}]", horizon_days)
                   for i, event in enumerate(events)],
        "distractors": [_canonical_distractor(d, f"distractors[{i}]")
                        for i, d in enumerate(distractors)],
    }


def _dumps(obj: Any) -> str:
    """The one serialization used for canonical text and for every hash.

    Sorted keys and compact separators remove formatting from the equation;
    `ensure_ascii` removes the encoding from it; `allow_nan` off keeps the
    output valid JSON.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _identity_view(canonical: Mapping[str, Any]) -> dict[str, Any]:
    """The canonical config minus the fields that are documentation."""
    return {k: v for k, v in canonical.items() if k not in DOCUMENTATION_KEYS}


# ---------------------------------------------------------------- identities


@dataclass(frozen=True)
class DatasetIdentity:
    """The deterministic identity of one dataset.

    A dataset is one scenario realized with one seed by one generator against
    one schema. `dataset_id` names it (`SCENARIO_SPECIFICATION.md` §5);
    `build_fingerprint` pins all four inputs, so two builds that agree on it
    are claiming to be the same dataset and must produce the same content
    (acceptance test A1).
    """

    scenario_id: str
    dataset_id: str
    config_sha256: str
    seed: int
    fabsim_version: str
    schema_version: str
    build_fingerprint: str


def derive_dataset_id(scenario_id: str, seed: int) -> str:
    """``<scenario_id>-s<seed>``, e.g. ``scn-3f9a1c7b2e4d-s042``."""
    validate_master_seed(seed)
    return f"{scenario_id}-s{seed:03d}"


def derive_build_fingerprint(*, config_sha256: str, seed: int,
                             fabsim_version: str,
                             schema_version: str) -> str:
    """SHA-256 over the four inputs that fully determine a dataset."""
    payload = _dumps({
        "config_sha256": config_sha256,
        "fabsim_version": fabsim_version,
        "schema_version": schema_version,
        "seed": seed,
    })
    return hashlib.sha256(
        IDENTITY_DOMAIN.encode("ascii") + b"\x00" + payload.encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ScenarioConfig:
    """A validated, canonicalized `fabsim.scenario/v1` configuration.

    Equality and hashing rest on the canonical text, so two configurations
    that mean the same thing are the same object as far as this class is
    concerned — which is exactly the property the identity model needs.
    """

    canonical_json: str
    identity_json: str
    config_sha256: str
    _data: Mapping[str, Any] = field(compare=False, repr=False)

    # -- the configuration ------------------------------------------------
    @property
    def canonical(self) -> dict[str, Any]:
        """A fresh, mutable copy of the canonical configuration."""
        return copy.deepcopy(dict(self._data))

    @property
    def name(self) -> str:
        """Human slug. Documentation only — never reaches an observable."""
        return self._data["name"]

    @property
    def description(self) -> str:
        return self._data["description"]

    @property
    def world(self) -> str:
        """World template name, resolved at build time."""
        return self._data["world"]

    @property
    def horizon_days(self) -> int:
        return self._data["horizon_days"]

    @property
    def lots(self) -> int:
        return self._data["lots"]

    @property
    def default_seed(self) -> int:
        """Seed used when a build does not override it."""
        return self._data["default_seed"]

    @property
    def events(self) -> list[dict[str, Any]]:
        """Mechanism activations, in declaration order (order is semantic)."""
        return copy.deepcopy(self._data["events"])

    @property
    def distractors(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._data["distractors"])

    # -- identity ---------------------------------------------------------
    @property
    def scenario_id(self) -> str:
        """``scn-`` + the first 12 hex characters of `config_sha256`."""
        return SCENARIO_ID_PREFIX + self.config_sha256[:SCENARIO_ID_HEX_CHARS]

    def dataset_identity(
        self,
        seed: int | None = None,
        *,
        fabsim_version: str = FABSIM_VERSION,
        schema_version: str = SCHEMA_VERSION,
    ) -> DatasetIdentity:
        """Identity of this scenario realized with `seed`.

        `seed` defaults to the configuration's `default_seed`. The version
        arguments exist so a caller can state which generator and schema a
        dataset belongs to; they default to this build of FabSim.
        """
        resolved_seed = self.default_seed if seed is None else seed
        validate_master_seed(resolved_seed)
        scenario_id = self.scenario_id
        return DatasetIdentity(
            scenario_id=scenario_id,
            dataset_id=derive_dataset_id(scenario_id, resolved_seed),
            config_sha256=self.config_sha256,
            seed=resolved_seed,
            fabsim_version=fabsim_version,
            schema_version=schema_version,
            build_fingerprint=derive_build_fingerprint(
                config_sha256=self.config_sha256,
                seed=resolved_seed,
                fabsim_version=fabsim_version,
                schema_version=schema_version,
            ),
        )


# -------------------------------------------------------------------- loading


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys instead of silently keeping the last one.

    ``{"lots": 20, "lots": 40}`` is valid JSON and a configuration bug: the
    author stated two intents and the parser would pick one.
    """
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ScenarioConfigError(f"duplicate key {key!r}")
        seen.add(key)
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise ScenarioConfigError(
        f"{name} is not a valid configuration value")


def _parse_json(text: str) -> Any:
    # A byte-order mark is an editor artefact, not a semantic difference: it
    # must not decide whether a configuration loads or what its identity is.
    try:
        return json.loads(text.lstrip(_BOM),
                          object_pairs_hook=_object_pairs_hook,
                          parse_constant=_reject_constant)
    except ScenarioConfigError:
        raise
    except json.JSONDecodeError as exc:
        raise ScenarioConfigError(f"invalid JSON: {exc}") from exc


def from_mapping(raw: Mapping[str, Any]) -> ScenarioConfig:
    """Build a `ScenarioConfig` from an already-parsed configuration."""
    canonical = canonicalize(raw)
    canonical_json = _dumps(canonical)
    identity_json = _dumps(_identity_view(canonical))
    config_sha256 = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
    return ScenarioConfig(
        canonical_json=canonical_json,
        identity_json=identity_json,
        config_sha256=config_sha256,
        _data=canonical,
    )


def load_scenario_text(text: str) -> ScenarioConfig:
    """Parse, validate and canonicalize a configuration from JSON text."""
    return from_mapping(_parse_json(text))


def load_scenario(path: str | Path) -> ScenarioConfig:
    """Load a configuration from a JSON file.

    The path never influences identity — only the file's content does — so a
    scenario may be filed under any descriptive name.
    """
    raw = Path(path).read_bytes()
    try:
        # utf-8-sig tolerates the BOM some editors add; a BOM is not a
        # semantic difference and must not change a scenario's identity.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScenarioConfigError(
            f"{path}: configuration must be UTF-8 text ({exc})") from exc
    return load_scenario_text(text)
