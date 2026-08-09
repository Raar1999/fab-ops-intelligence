"""
fabops.diagnosis — the answer-blind investigation engine.

    diagnose(db_path) -> Investigation

One public function, one input, one artifact. The input is a **database
path**, not a directory and not an object that holds more than one plane: a
function given a directory could reach the hidden plane by joining a name, and
a function given a rich object could be handed one. Everything the engine
knows therefore comes from the 22 observable tables of schema v2.

The planes it runs through, in order:

    exposure / candidates   who could be responsible - every entity kind the
                            run log records, enumerated from the data
    evidence                residuals: an observation minus the fab's own
                            reference for it
    shared anchors          when - change points chosen once for the whole fab
                            and applied to every candidate alike
    statistic               how far each candidate departed, at anchors it did
                            not choose; a registry, not a constant
    decision                ranks among contemporaneous peers, families
                            combined by Fisher, calibrated by permuting the
                            candidate label inside this dataset
    report                  ranked candidates, evidence, rivals, onsets,
                            abstention

There is no calibration file, no reference population and no evaluator input:
the null is computed from the dataset being diagnosed. There is no separate
detection entry point either - a measured fab-level detector was seven times
weaker than the candidate-enumerating comparison, and a separately supplied
scope would make answer-blindness a property of the caller.

`insufficient_evidence` is a correct answer and on a fault-free world it is
the only correct one.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from fabops.diagnosis.anchors import DECLARED_FRACTIONS, select
from fabops.diagnosis.candidates import (BIN_DAYS, assess, build_panels,
                                         enumerate_candidates)
from fabops.diagnosis.channels import (load_observations, read_meta,
                                        read_roles)
from fabops.diagnosis.decide import ALPHA, PERMUTATIONS, score
from fabops.diagnosis.model import (ENTITY_KINDS, INVESTIGATION_SCHEMA,
                                    Candidate, Investigation)
from fabops.diagnosis.report import assemble, control_for_exposure_mix
from fabops.diagnosis.statistics import DEFAULT_STATISTIC, STATISTICS

__all__ = [
    "ENGINE",
    "ENGINE_VERSION",
    "INVESTIGATION_SCHEMA",
    "Investigation",
    "diagnose",
]

#: The engine's own version, carried in every report. It moves whenever a
#: change could alter a report for a fixed database - the same rule the
#: generator holds itself to.
ENGINE_VERSION = "1.0.0"
ENGINE = f"fabops.diagnosis/{ENGINE_VERSION}"


def diagnose(db_path: Path | str, *, statistic: str = DEFAULT_STATISTIC,
             alpha: float = ALPHA, permutations: int = PERMUTATIONS,
             kinds: Sequence[str] = ENTITY_KINDS,
             bin_days: float = BIN_DAYS,
             anchor_fractions: Sequence[float] = DECLARED_FRACTIONS
             ) -> Investigation:
    """Investigate one observable dataset.

    The keyword arguments are the declared, replaceable parts of the method -
    which statistic, at what level, with how much resolution. None of them can
    carry information about the answer: they are numbers and a registry key,
    and the defaults are what a caller who says nothing gets.
    """
    if statistic not in STATISTICS:
        raise ValueError(
            f"unknown statistic {statistic!r}; registered: "
            f"{sorted(STATISTICS)}")

    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        meta = read_meta(connection)
        roles = read_roles(connection)
        observations = load_observations(connection, meta)
    finally:
        connection.close()

    controlled_observations = control_for_exposure_mix(observations, bin_days)
    panels = build_panels(observations, meta.horizon_days, kinds, bin_days,
                          roles)
    anchors = select(meta.horizon_days, anchor_fractions)
    supports = assess(enumerate_candidates(observations), panels)
    decision = score(panels, anchors, statistic, permutations)

    controlled_panels = build_panels(controlled_observations,
                                     meta.horizon_days, kinds, bin_days, roles)
    controlled = _controlled_values(controlled_panels, anchors, statistic)

    return assemble(dataset_id=meta.dataset_id,
                    horizon_days=meta.horizon_days, anchors=anchors,
                    decision=decision, supports=supports,
                    controlled=controlled, statistic=statistic, engine=ENGINE,
                    alpha=alpha, bin_days=bin_days)


def _controlled_values(panels, anchors, statistic):
    """The same statistic after the exposure-mix control, for `confounders[]`."""
    from fabops.diagnosis.statistics import evaluate

    out: dict[str, dict[str, float]] = {}
    for panel in panels:
        if panel.kind not in ("chamber", "tool"):
            continue
        scale = panel.scale()
        if scale <= 0:
            continue
        for entity in panel.usable():
            best = 0.0
            for anchor in anchors:
                best = max(best, evaluate(statistic, panel.series[entity],
                                          panel.index[entity], anchor.day,
                                          scale))
            out.setdefault(entity, {})[panel.channel] = best
    return out
