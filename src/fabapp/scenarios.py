"""
scenarios.py — the scenario picker's data, and the four fields it refuses.

A user has to choose something to build, so this package reads the scenario
configurations. What it publishes about them is deliberately narrow.

**What a scenario offers here.** Its slug, the world it runs in, the horizon in
days, the number of lots, and the seed it defaults to. Those are the *build
inputs* — between them they decide how long generation takes, how large the
dataset is, and what its identity will be. They are what a person needs in
order to press a button.

**What it never offers, and why that is not caution but correctness.** Not the
`description`, not the events, not the distractors, not the routing conditions,
and not so much as a *count* of any of them. Every configuration in this
library describes, in prose, which chamber it faults and when; and an event
count of zero is by itself the complete answer to `null_baseline`. A picker
that showed either would hand the reader the answer key on the way in, and the
investigation screen afterwards would be theatre. `scenarios/README.md` is the
maintainers' index and says so of itself; this module is the public face of the
same directory and behaves accordingly.

**Recovering the slug of an existing dataset.** A manifest carries
`scenario_id`, which is the first twelve hex characters of the digest of the
canonical configuration with its documentation fields removed. So the mapping
runs backwards for anybody holding the library: digest each configuration, and
the one that matches names the dataset. That is how the dataset browser shows a
scenario without anything having been written down, and it is why this package
needs no state file. It also costs nothing in blindness: a dataset on its own
still discloses nothing, `fabops` cannot read this directory, and the mapping
exists only where the configurations do.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fabsim.scenario import ScenarioConfigError, load_scenario

from fabapp.config import scenario_root

__all__ = [
    "PUBLISHED_FIELDS",
    "ScenarioOption",
    "available",
    "config_for",
    "option_for",
    "slug_for_scenario_id",
]

#: Everything a `ScenarioOption` carries, stated as data so a test can assert
#: the list has not quietly grown. Adding a field here is a decision about what
#: a user may know before they build, and it should look like one in a diff.
PUBLISHED_FIELDS = ("slug", "world", "horizon_days", "lots", "default_seed",
                    "scenario_id")


@dataclass(frozen=True)
class ScenarioOption:
    """One buildable scenario, as the picker sees it.

    The fields are the build inputs and the opaque identity, and there is
    nothing else on this object — not a private attribute, not a stashed
    configuration. A screen cannot render what it was not given.
    """

    slug: str
    world: str
    horizon_days: int
    lots: int
    default_seed: int
    scenario_id: str

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in PUBLISHED_FIELDS}


def _paths(root: Path | None = None) -> list[Path]:
    directory = Path(root) if root is not None else scenario_root()
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def option_for(path: Path) -> ScenarioOption:
    """Project one configuration file onto the build inputs.

    The full configuration is loaded — it has to be, to be validated and to
    have its identity derived — and then all but six values are dropped on the
    way out of this function.
    """
    config = load_scenario(path)
    return ScenarioOption(
        slug=path.stem,
        world=config.world,
        horizon_days=config.horizon_days,
        lots=config.lots,
        default_seed=config.default_seed,
        scenario_id=config.scenario_id,
    )


def available(root: Path | None = None) -> tuple[ScenarioOption, ...]:
    """Every buildable scenario, by slug.

    Read from disk every time, deliberately. Caching it was tried and removed:
    the whole library parses and digests in 1.2 ms, so the cache bought nothing
    measurable, and what it cost was correctness — a scenario file added or
    edited while the application is running would have stayed invisible until
    the process restarted. Module-level mutable state that can go stale is the
    kind of thing this repository spends test suites removing, and paying a
    millisecond not to have any is the right side of that trade.
    """
    directory = Path(root) if root is not None else scenario_root()
    options: list[ScenarioOption] = []
    for path in _paths(directory):
        try:
            options.append(option_for(path))
        except ScenarioConfigError:
            # A file that does not parse is not a scenario a user can build,
            # and refusing the whole library because one is malformed would
            # make an editing mistake look like a broken installation.
            continue
    return tuple(sorted(options, key=lambda option: option.slug))


def config_for(slug: str, root: Path | None = None) -> Any:
    """The validated configuration for one slug, for the generator alone.

    This returns the real `ScenarioConfig`, which does carry the events and the
    prose — it has to, because it is what gets built. It is called by
    `fabapp.generate` and by nothing that draws a screen, and the object never
    reaches a page: the generator hands it straight to `fabsim`.
    """
    directory = Path(root) if root is not None else scenario_root()
    path = directory / f"{slug}.json"
    if not path.is_file():
        known = ", ".join(option.slug for option in available(directory))
        raise FileNotFoundError(
            f"no scenario named {slug!r} in {directory}; available: "
            f"{known or '(none)'}")
    return load_scenario(path)


def slug_for_scenario_id(scenario_id: str,
                         root: Path | None = None) -> str | None:
    """Which scenario produced a dataset, from its manifest identity.

    `None` when nothing in the library matches — a dataset built from a
    configuration the user wrote themselves, or from a library that has moved
    on. The browser shows that as unknown rather than guessing, because a
    wrong provenance line is worse than an absent one.
    """
    for option in available(root):
        if option.scenario_id == scenario_id:
            return option.slug
    return None
