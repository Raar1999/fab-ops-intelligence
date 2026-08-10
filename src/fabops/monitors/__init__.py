"""
fabops.monitors — the four families that watch the fab.

    monitor(db_path) -> MonitorReport

Process, equipment, yield and defect, over one observable dataset, producing
timestamped **signals** a human reads. It is the "what is happening" half of
`PROJECT_VISION`'s question, and it stops there: a monitor never ranks
candidates, never combines families into a score and never names a cause.
Doing any of those is `fabops.diagnosis`, which reaches its conclusion a
different way and is deliberately not a consumer of this module (ADR-029 §6 —
monitors are a legitimate separate capability and not a precondition of
diagnosis).

Same input discipline as the engine: **a path to `fab.db` and nothing else.**
There is no default database, no dataset directory and no sibling path, so the
hidden plane is not reachable by an accident of signature.

**What a signal rate means, measured.** Every family charts at the fab's own
three-sigma action convention, and a healthy fab still produces signals — its
chambers differ, its weeks differ, and rules F10/F11 put permanent benign
offsets on every chamber by design. On twelve fault-free worlds the default
configuration produces **42 signals per dataset** and its single-point rule
fires at **0.0060 per charted point against a nominal 0.0027**; the inflation
is measured, its cause is isolated in `model.CHART_RULES` — a spread estimated
from a dozen days of a serially correlated series, since the same chart reads
0.94x nominal when its spread is well estimated — and it was deliberately not
tuned away, because widening a limit until it matched the worlds it judges is
the circularity ADR-027 rejected.

So a signal is a **prompt to look**, not a claim.

**And a signal count is not attribution — measured, because the opposite looked
true.** On the demo at seed 42 the planted chamber does lead the fab, 10 of its
23 chamber-grain signals against the runner-up's 6, and it would have been easy
to ship that as a capability.
Running the same instrument over six library scenarios refutes it: one chamber
(`ETCH-02/B` on this world at this seed) leads in four of them, including three
where the fault was planted somewhere else entirely, and two planted chambers
produce no signals at all. The cause was measured rather than guessed — the
per-chamber *denominators* are equal to within 30%, so the concentration is not
an exposure artefact; it is that a chart whose dozen baseline days happened to
be quiet keeps tight limits for the rest of the horizon, and that luck varies
several-fold between chambers. The Student-t inflation prices that correctly
*on average over series* and cannot make it uniform *across* them.

The counter-design was measured too and is worse: charting each run instead of
each day gives every chart sixty baseline points instead of a dozen, and its
realized rate is 0.032 — twelve times nominal — because a lot's wafers share a
lot-level offset, so one unusual lot produces a run of alarms. That serial
dependence is the same one `effective_size` discounts for; at run grain it is
strong enough to overwhelm the extra points. Day-grain aggregation is the better
instrument and the concentration is the price.

Attribution therefore stays where it is calibrated: `fabops.diagnosis`, which
compares a candidate against a permutation of the candidate label inside the
same dataset. This module lists what moved. ADR-033 records the finding and the
acceptance restatement it forced.
"""
from __future__ import annotations

from pathlib import Path

from fabops.monitors.defect import monitor_defects
from fabops.monitors.equipment import monitor_equipment
from fabops.monitors.model import (CHART_RULES, DEFAULT_CHART_RULE,
                                   MonitorReport, Signal, order_signals)
from fabops.monitors.process import monitor_process
from fabops.monitors.yield_ import monitor_yield
from fabops.semantic import open_layer

__all__ = [
    "CHART_RULES",
    "DEFAULT_CHART_RULE",
    "FAMILIES",
    "MONITOR",
    "MONITOR_VERSION",
    "MonitorReport",
    "Signal",
    "monitor",
]

#: Moves whenever a rule, a limit or a baseline convention changes — anything
#: that could alter which signals a fixed database produces.
#:
#: 1.0.0 -> 1.1.0 for the effective-sample-size correction: a baseline of k
#: serially correlated days carries less information than k independent ones, so
#: the Student-t inflation is computed from the discounted count. It moves every
#: chart's limits and therefore every report — measured on twelve fault-free
#: worlds, the single-point rule went from 5.9x its nominal rate to 2.2x, and
#: the demo's planted chamber still leads its fab.
MONITOR_VERSION = "1.1.0"
MONITOR = f"fabops.monitors/{MONITOR_VERSION}"

FAMILIES = ("process", "equipment", "yield", "defect")

_RUNNERS = {
    "process": monitor_process,
    "equipment": monitor_equipment,
    "yield": monitor_yield,
    "defect": monitor_defects,
}


def monitor(db_path: Path | str, *,
            families: tuple[str, ...] = FAMILIES,
            chart_rule: str = DEFAULT_CHART_RULE) -> MonitorReport:
    """Watch one observable dataset with the named families.

    `chart_rule` is the declared, replaceable part of the method: which of two
    ordinary SPC limit conventions the charts use. It carries no information
    about any answer, and the default is what a caller who says nothing gets.
    """
    unknown = [name for name in families if name not in _RUNNERS]
    if unknown:
        raise ValueError(f"unknown families {unknown}; registered: "
                         f"{list(FAMILIES)}")
    if chart_rule not in CHART_RULES:
        raise ValueError(f"unknown chart rule {chart_rule!r}; registered: "
                         f"{list(CHART_RULES)}")

    connection = open_layer(db_path)
    try:
        dataset_id, horizon_days = connection.execute(
            "SELECT dataset_id, horizon_days FROM dataset_meta").fetchone()
        report = MonitorReport(dataset_id=str(dataset_id),
                               generated_by=MONITOR,
                               horizon_days=int(horizon_days),
                               chart_rule=chart_rule)
        for name in families:
            signals, measurements = _RUNNERS[name](connection,
                                                   int(horizon_days),
                                                   chart_rule)
            report.signals.extend(signals)
            report.measurements[name] = measurements
    finally:
        connection.close()

    report.signals = order_signals(report.signals)
    return report
