"""
benchmark.py — the Phase 6 harness: build a named population, score the engine
on it, and say what the population was.

`EXPANSION_ROADMAP` Phase 6 asks for "one command [that] emits the results
table". This is that command's engine room. What makes it a Phase 6 deliverable
rather than a convenience wrapper is the third clause: every number it produces
carries the population that produced it, and the population carries its role,
so a development figure cannot be read as a held-out one by someone skimming.

Three things live here and they are deliberately separate:

* **`build_population`** — the only function that writes, and it writes only
  into a root the caller supplies, exactly as `matrix.build_library` does and
  for the same reason. It builds in a process pool because a measurement the
  suite cannot afford to run is a measurement that stops being run.
* **`score_population`** — pure reading. It runs `diagnose` on each database,
  joins the report to that dataset's truth on `dataset_id`, and returns a
  `Score`. The engine is handed a path and nothing else; this module never
  passes it a scenario name, a role, or anything from the hidden plane.
* **`diversity`** — reads the scenario *configurations*, which
  `GROUND_TRUTH_CONTRACT.md` §4 permits the evaluator to do, and reports what
  the library actually covers. "Scenario diversity is demonstrated" has to be a
  measurement; asserted diversity is how a library of twelve near-copies passes
  for a benchmark.

**What this module must never do is choose anything.** It measures. Which
anchor rule, which statistic and which level are decisions recorded in
`ADR-031` and declared in the engine's own modules; the harness's job is to
produce the table those decisions were read off, and to be re-runnable by
someone who wants to check them.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from fabeval.diagnosisscore import Score, score_dataset
from fabeval.population import (DEVELOPMENT_SCENARIOS, HELD_OUT_SCENARIOS,
                                LIBRARY, role_of)

__all__ = [
    "SCENARIO_ROOT",
    "Built",
    "DiversityReport",
    "build_population",
    "build_one",
    "diversity",
    "main",
    "render_diversity",
    "score_population",
]

SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"

#: The world every library scenario shares (anti-leakage rule D7). Named once
#: so a population cannot be built against a second world by accident, which
#: would make its datasets incomparable while looking identical.
WORLD = "baseline_fab_v1"


@dataclass(frozen=True)
class Built:
    """One built dataset, as paths. Deliberately not an object holding both
    planes: the engine is handed `db_path` and the scorer opens `truth_path`,
    and keeping them as two fields makes the asymmetry visible at the call
    site."""

    scenario: str
    seed: int
    role: str
    dataset_id: str
    db_path: str
    truth_path: str

    @property
    def truth(self) -> dict[str, Any]:
        return json.loads(Path(self.truth_path).read_text(encoding="utf-8"))

    @property
    def time_origin(self) -> str | None:
        """The observable clock, which is what makes onset error measurable.

        Read from `dataset_meta` rather than from truth, because truth records
        an onset as an instant and the report speaks in days from the start of
        the window — neither artifact alone carries the conversion.
        """
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT time_origin FROM dataset_meta").fetchone()
        finally:
            connection.close()
        return row[0] if row else None


def build_one(job: tuple[str, int, str]) -> dict[str, Any]:
    """Build one dataset. Module level, because a process-pool worker can only
    unpickle a callable it can import by name."""
    scenario, seed, root = job
    from fabsim.emit import build_dataset
    from fabsim.scenario import load_scenario
    from fabsim.world import load_world

    dataset = build_dataset(
        load_scenario(SCENARIO_ROOT / f"{scenario}.json"), seed,
        world=load_world(WORLD), root=Path(root) / f"{scenario}-{seed}",
        created_at="benchmark")
    return {"scenario": scenario, "seed": seed,
            "dataset_id": dataset.dataset_id,
            "db_path": str(dataset.db_path),
            "truth_path": str(dataset.truth_path)}


def build_population(root: Path | str, scenarios: Sequence[str],
                     seeds: Sequence[int], *,
                     workers: int | None = None) -> tuple[Built, ...]:
    """Every (scenario, seed) pair, built into `root`.

    The caller owns `root` and owns deleting it. That is not fastidiousness
    about tidiness: a dataset is ~118 MB, so a calibration population is
    measured in tens of gigabytes, and the harness building in batches the
    caller can drop is what keeps the measurement runnable on an ordinary
    machine without this module acquiring the right to delete anything.
    """
    jobs = [(scenario, seed, str(root))
            for scenario in scenarios for seed in seeds]
    if not jobs:
        return ()
    count = workers if workers is not None else min(
        len(jobs), max(1, (os.cpu_count() or 2) - 1), 12)
    if count <= 1:
        records = [build_one(job) for job in jobs]
    else:
        try:
            with ProcessPoolExecutor(max_workers=count) as pool:
                records = list(pool.map(build_one, jobs))
        except Exception:                               # pragma: no cover
            # A machine that cannot spawn workers still has to be able to
            # produce the table; slower is acceptable, skipped is not.
            records = [build_one(job) for job in jobs]
    return tuple(
        Built(role=role_of(record["scenario"]), **record)
        for record in records)


def score_population(built: Sequence[Built], population: str, *,
                     notes: Sequence[str] = (), **engine: Any) -> Score:
    """Run the engine over a built population and join each report to truth.

    `**engine` is forwarded to `diagnose` unchanged — `statistic`, `alpha`,
    `permutations`, `anchor_fractions`. Every one of them is a number or a
    registry key; none can carry information about an answer, which is what
    makes it safe for a *grader* to vary them while measuring.
    """
    from fabops.diagnosis import diagnose

    pairs = []
    for record in built:
        report = diagnose(record.db_path, **engine).to_dict()
        pairs.append((report, record.truth, record.scenario,
                      record.time_origin))
    return Score(population=population,
                 outcomes=tuple(score_dataset(*row) for row in pairs),
                 notes=tuple(notes))


# --------------------------------------------------------------- diversity


#: The axes a library has to vary along before "diverse" means anything. Each
#: one is a dimension some part of the engine reasons over: the mechanism and
#: the equipment family decide which evidence families carry signal, the
#: profile and the onset position decide whether a change-point contrast can
#: see it, the grain decides what the correct answer even *is*, and the event
#: count and the confound decide whether one answer is enough.
DIVERSITY_AXES = ("mechanism", "tool_type", "severity", "profile",
                  "onset_band", "grain", "events", "confounded")

#: Where in the horizon an onset falls. Three bands rather than a number,
#: because the question the anchor rule asks is categorical: is there a
#: baseline to contrast against, is the fault in the middle where a declared
#: mid-horizon anchor sits, or is it late enough that the anchor is on the
#: wrong side of most of the record.
ONSET_BANDS = (("early", 0.0, 0.30), ("middle", 0.30, 0.60),
               ("late", 0.60, 1.0))


def _onset_band(fraction: float) -> str:
    for name, low, high in ONSET_BANDS:
        if low <= fraction < high:
            return name
    return ONSET_BANDS[-1][0]                            # pragma: no cover


@dataclass(frozen=True)
class DiversityReport:
    """What the library covers, per axis, split by population role."""

    per_scenario: Mapping[str, Mapping[str, tuple[str, ...]]]
    coverage: Mapping[str, Mapping[str, tuple[str, ...]]]

    def values(self, axis: str) -> tuple[str, ...]:
        return tuple(sorted({v for role in self.coverage.values()
                             for v in role.get(axis, ())}))

    def shared(self, axis: str) -> tuple[str, ...]:
        """Values present in *both* roles. A held-out set that shares nothing
        with development is not a generalization test, it is a different
        experiment."""
        development = set(self.coverage.get("development", {}).get(axis, ()))
        held_out = set(self.coverage.get("held-out", {}).get(axis, ()))
        return tuple(sorted(development & held_out))


def diversity(scenarios: Sequence[str] = LIBRARY) -> DiversityReport:
    """Measure the library's coverage from the configurations themselves."""
    from fabsim.scenario import load_scenario
    from fabsim.world import load_world

    world = load_world(WORLD)
    tool_type = {tool.tool_name: tool.tool_type for tool in world.tools}

    per_scenario: dict[str, dict[str, tuple[str, ...]]] = {}
    coverage: dict[str, dict[str, set[str]]] = {}
    for scenario in scenarios:
        config = load_scenario(SCENARIO_ROOT / f"{scenario}.json")
        canonical = config.canonical
        events = canonical["events"]
        horizon = float(canonical["horizon_days"])

        axes: dict[str, tuple[str, ...]] = {
            "mechanism": tuple(sorted({e["mechanism"] for e in events})) or ("-",),
            "tool_type": tuple(sorted({
                tool_type.get(e["target"]["tool"], "?") for e in events})) or ("-",),
            "severity": tuple(sorted({e["severity"] for e in events})) or ("-",),
            "profile": tuple(sorted({e["profile"]["type"] for e in events})) or ("-",),
            "onset_band": tuple(sorted({
                _onset_band(float(e["onset_day"]) / horizon)
                for e in events})) or ("-",),
            "grain": tuple(sorted({
                "chamber" if e["target"].get("chamber") else "tool"
                for e in events})) or ("-",),
            "events": (str(len(events)),),
            "confounded": ("yes" if canonical["routing_conditions"] else "no",),
        }
        # A declared benign distractor is standing structure rather than a
        # fault, so it is reported on its own axis rather than folded into the
        # mechanism list where it would look like one.
        if canonical["distractors"]:
            axes["mechanism"] = axes["mechanism"] + tuple(
                f"{d['mechanism']}(distractor)"
                for d in canonical["distractors"])
        per_scenario[scenario] = axes

        role = coverage.setdefault(role_of(scenario), {})
        for axis, values in axes.items():
            role.setdefault(axis, set()).update(values)

    return DiversityReport(
        per_scenario=per_scenario,
        coverage={role: {axis: tuple(sorted(values))
                         for axis, values in axes.items()}
                  for role, axes in coverage.items()})


def render_diversity(report: DiversityReport) -> str:
    """A plain-text coverage table. No colour, no width assumptions."""
    lines = ["scenario library  -  diversity", ""]
    header = f"{'scenario':30s} {'role':11s} " + " ".join(
        f"{axis:14s}" for axis in DIVERSITY_AXES)
    lines += [header, "-" * len(header)]
    for scenario in sorted(report.per_scenario):
        axes = report.per_scenario[scenario]
        lines.append(
            f"{scenario:30s} {role_of(scenario):11s} " + " ".join(
                f"{','.join(axes[axis])[:14]:14s}" for axis in DIVERSITY_AXES))

    lines += ["", "coverage per axis", "-" * 17]
    for axis in DIVERSITY_AXES:
        values = report.values(axis)
        shared = report.shared(axis)
        lines.append(f"  {axis:12s} {len(values):2d} value(s): "
                     f"{', '.join(values)}")
        lines.append(f"  {'':12s}    shared by both roles: "
                     f"{', '.join(shared) or 'none'}")
    lines += ["",
              f"development {len(DEVELOPMENT_SCENARIOS)}, "
              f"held out {len(HELD_OUT_SCENARIOS)}, "
              f"total {len(LIBRARY)}"]
    return "\n".join(lines)


# ------------------------------------------------------------------ one command


def main(argv: Sequence[str] | None = None) -> int:
    """`EXPANSION_ROADMAP` Phase 6: one command emits the results table.

    It refuses to run without an explicit `--root`, for the reason
    `build_population` documents: a dataset is ~118 MB and the caller has to
    own both the disk and the deleting. Nothing is written anywhere else.
    """
    import argparse

    from fabeval.population import DEVELOPMENT_SEEDS, HELD_OUT_SEEDS

    parser = argparse.ArgumentParser(
        prog="fabops-benchmark",
        description="Build a named population and score the diagnosis engine "
                    "on it. Every number carries the population it came from.")
    parser.add_argument("--root", required=True, type=Path,
                        help="where to build datasets; the caller owns it")
    parser.add_argument("--population", default="development",
                        choices=("development", "held-out", "both"),
                        help="which declared population to score")
    parser.add_argument("--seeds", type=int, nargs="*", default=None,
                        help="override the population's declared seeds")
    parser.add_argument("--diversity-only", action="store_true",
                        help="print the coverage table and build nothing")
    parser.add_argument("--emit", type=Path, default=None,
                        help=("write a fabeval.results/v1 document here. It is "
                              "what `fabops-publish` renders the README's "
                              "benchmark section from, so a public number and "
                              "a measured one cannot part company."))
    arguments = parser.parse_args(argv)

    print(render_diversity(diversity()))
    if arguments.diversity_only:
        return 0

    plans = []
    if arguments.population in ("development", "both"):
        plans.append(("development", DEVELOPMENT_SCENARIOS,
                      arguments.seeds or DEVELOPMENT_SEEDS))
    if arguments.population in ("held-out", "both"):
        plans.append(("held-out", HELD_OUT_SCENARIOS,
                      arguments.seeds or HELD_OUT_SEEDS))

    scores = []
    for role, scenarios, seeds in plans:
        built = build_population(arguments.root / role, scenarios, seeds)
        note = ("the method was selected on this population; these numbers "
                "describe the fit, not the capability"
                if role == "development" else
                "scored with the anchor rule, statistic and level frozen "
                "beforehand")
        score = score_population(
            built, f"{role} ({len(scenarios)} scenarios x {len(seeds)} seeds)",
            notes=(note,))
        scores.append(score)
        print()
        print(score.render())

    if len(scores) > 1:
        # The whole library, scored as one population. This is the row a claim
        # is actually permitted on: `population.claimable` requires ten
        # scenarios *and* the declared split, and neither half of the library
        # reaches ten on its own. Reporting only the halves would leave the
        # project unable to state, in one sentence, what its engine does.
        combined = Score(
            population=f"library ({len(LIBRARY)} scenarios, both roles)",
            outcomes=tuple(outcome for score in scores
                           for outcome in score.outcomes),
            notes=("development and held-out pooled; the split is reported "
                   "separately above because a development number describes "
                   "the fit and a held-out one describes the capability",))
        scores.append(combined)
        print()
        print(combined.render())

    if arguments.emit is not None:
        _emit(arguments.emit, scores)
        print(f"wrote {arguments.emit}")
    return 0


def _emit(path: Path, scores: Sequence[Any]) -> None:
    """Write the results document the public surfaces are rendered from."""
    from fabops.diagnosis import ENGINE
    from fabops.diagnosis.anchors import DECLARED_FRACTIONS
    from fabops.diagnosis.decide import ALPHA, PERMUTATIONS
    from fabops.diagnosis.statistics import DEFAULT_STATISTIC
    from fabeval.publish import results_document

    report = diversity()
    document = results_document(
        scores, engine=ENGINE,
        settings={"statistic": DEFAULT_STATISTIC, "alpha": ALPHA,
                  "permutations": PERMUTATIONS,
                  "anchor_fractions": list(DECLARED_FRACTIONS)},
        diversity_axes={axis: len(report.values(axis))
                        for axis in DIVERSITY_AXES})
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=False)
    path.write_text(payload + chr(10), encoding="utf-8")


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
