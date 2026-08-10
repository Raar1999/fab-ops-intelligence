"""
model.py — what a monitor emits, and the statistics every family shares.

A monitor reports **signals**, not conclusions. A `Signal` says "this series,
on this entity, broke this rule, on this day, by this much" — it names no
cause, ranks no candidate and combines no channels. Ranking entities against
each other is `fabops.diagnosis`'s job and is deliberately not done twice
(ADR-029 §6: monitors are a legitimate separate capability and not a
precondition of diagnosis).

Two decisions are shared by every family and are made here rather than four
times.

**Charts are drawn on the peer-differenced series.** Every chamber in this fab
shares a fab-week wander, and a chart drawn on the raw series charts the week.
Subtracting the contemporaneous mean of an entity's *same-role* peers removes
it, which is both what an engineer does by eye and the only form under which
the null is close to stationary. Peers are same-role because a chamber every
wafer passes through and a chamber a third of them reach are not draws from one
distribution — the same correction ADR-029 §8 had to make in the engine.

**Limits are measured once, in a declared baseline window, and then frozen.**
A spread that kept adapting would widen to accommodate a slow ramp, and the
fault would become invisible by being persistent — the reasoning ADR-017 §3
already applied to the simulated fab's own alarm charts. The consequence is
stated rather than hidden: a fault that begins *inside* the baseline window is
absorbed into the limits and will not be charted. `BASELINE_FRACTION` is
therefore part of what a monitor's result means.
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

__all__ = [
    "BASELINE_FRACTION",
    "effective_size",
    "lag_one_autocorrelation",
    "MIN_BASELINE_POINTS",
    "MIN_PEERS",
    "SIGMA_LIMIT",
    "CHART_RULES",
    "Chart",
    "DEFAULT_CHART_RULE",
    "MonitorReport",
    "Series",
    "Signal",
    "peer_difference",
    "spc_signals",
]

#: The fraction of the horizon used to set a chart's centre and spread. A
#: quarter, so that a monitor has three quarters of the record to watch, and
#: because `PHASE_1_ACCEPTANCE.md` A5 requires a faulted scenario to carry at
#: least 30% of its horizon as baseline — one library member deliberately
#: violates that (an onset at 14%, built to test early detection) and this
#: monitor will not chart it. That is a stated limit of the instrument, not a
#: defect in the fab.
BASELINE_FRACTION = 0.25

#: Fewer points than this and a spread is a rumour.
MIN_BASELINE_POINTS = 8

#: A leave-one-out reference needs at least two peers to have a spread at all —
#: the same floor the engine and the reference queries carry, and the same
#: reason 13 of this world's 24 chambers cannot be charted against peers.
MIN_PEERS = 2

#: The fab's own control-limit convention: three sigma, the multiple eight of
#: the nine alarm rules in the simulated world declare. Borrowed rather than
#: invented, for the reason ADR-027 §2 gives — this fires per point on every
#: dataset, so it needs an *action* limit and not a screening one.
SIGMA_LIMIT = 3.0

#: …and the same convention as a per-point false-alarm probability, which is
#: what the limit inflation below is derived against. Three sigma on a normal
#: two-sided.
ACTION_ALPHA = 0.0027

#: How a chart's limits are set. Both members are ordinary SPC, they differ in
#: one assumption, and which of them is calibrated on *this* fab is a
#: measurement rather than a preference — so the choice is a declared,
#: versioned component and the loser is kept with its cost on the record. This
#: is the same shape as the engine's statistic registry (ADR-029 §5).
#:
#: Measured on twelve fault-free worlds, 9,052 charted points, as the
#: single-point rule's realized per-point rate against its nominal 0.0027:
#:
#:   individuals    0.0060  (2.2x)  one constant spread from the baseline daily
#:                  values — what a shop-floor individuals chart does. **The
#:                  default: measurably the best calibrated of the three.**
#:   xbar           0.0088  (3.3x)  limits scaled by 1/sqrt(n) for the day's own
#:                  subgroup size. Correct only if day-to-day variation were
#:                  within-day sampling noise; on this fab a between-day
#:                  component dominates, so scaling by n buys tight limits on
#:                  the days that least deserve them.
#:   moving_range   0.0162  (6.0x)  spread from the whole horizon's mean moving
#:                  range (MR-bar / 1.128). It was expected to win — sixty
#:                  degrees of freedom instead of ten, and immune to a step —
#:                  and it loses because MR measures *one-step* variation and
#:                  this series has slow structure that a one-step difference
#:                  cannot see. Kept because that is worth knowing.
#:
#: **Where the remaining 2.2x comes from, and where it does not.** The chart is
#: correct when its spread is: judging the same points against a spread
#: estimated from the *whole* series — enough points that estimation error is
#: negligible — reads 0.0025, which is 0.94x nominal, i.e. exactly right. So
#: neither the fab nor the rule is the problem, and two suspects were checked
#: and cleared: the fab is stationary (per-quarter median spreads 0.01309 /
#: 0.01358 / 0.01340 / 0.01350) and the series is only mildly heavy-tailed
#: (kurtosis median 3.09 against 3).
#:
#: What is left is the price of estimating a spread from about a dozen baseline
#: days of a *serially correlated* series (lag-1 +0.19). `effective_size` pays
#: most of it — without that discount the same measurement reads 0.0160, 5.9x —
#: and the rest is the irreducible cost of a short Phase I window.
#:
#: Widening the limits until the rate matched would be fitting a threshold to
#: the very worlds it judges — the circularity ADR-027 §2 rejected as option A.
#: So the limit stays the fab's own three-sigma convention, the two corrections
#: applied are both derivations rather than fits, and the realized rate is
#: published. This is why a monitor signal is a **prompt** and not a claim: the
#: one claim this project makes about a dataset is the engine's abstention,
#: which is calibrated exactly and by a different method.
CHART_RULES = ("individuals", "xbar", "moving_range")
DEFAULT_CHART_RULE = "individuals"

#: Hartley's d2 for a moving range of two consecutive observations. The
#: constant that turns a mean absolute successive difference into a standard
#: deviation, and it is a property of the Gaussian rather than of this fab.
D2_MOVING_RANGE = 1.128


@dataclass(frozen=True)
class Signal:
    """One rule violation on one series.

    `value` and `limit` are in the series' own units and `z` is the
    standardized departure, so a reader can recompute the decision by hand —
    which is the explainability guarantee this project trades on.
    """

    family: str
    entity_kind: str
    entity: str
    channel: str
    rule: str
    day_index: int
    value: float
    z: float
    limit: float
    support: int
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "entity": {"kind": self.entity_kind, "id": self.entity},
            "channel": self.channel,
            "rule": self.rule,
            "day_index": self.day_index,
            "value": round(self.value, 6),
            "z": round(self.z, 4),
            "limit": round(self.limit, 6),
            "support": self.support,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Series:
    """A daily series for one entity on one channel, with its support."""

    entity: str
    channel: str
    days: tuple[int, ...]
    values: tuple[float, ...]
    support: tuple[int, ...]


@dataclass(frozen=True)
class Chart:
    """A frozen control chart: where the centre is and how wide the limits are.

    `inflation` is the factor by which the limits are widened to pay for the
    fact that the spread was *estimated* from a short baseline rather than
    known. Ignoring it is the single largest source of false alarms a chart
    like this has: with about ten baseline points, a nominal three-sigma rule
    fires at roughly 1.5% per point instead of 0.27% — five times too often —
    because a spread estimated from ten numbers is itself uncertain by about a
    quarter. Measured on twelve fault-free worlds before the correction: 73.6
    single-point violations per dataset against 11.3 expected.
    """

    centre: float
    spread: float
    baseline_points: int
    baseline_last_day: int
    inflation: float = 1.0

    def z(self, value: float) -> float:
        scale = self.spread * self.inflation
        return 0.0 if scale <= 0 else (value - self.centre) / scale


@dataclass
class MonitorReport:
    """Everything the four families saw, as data rather than as printout."""

    dataset_id: str
    generated_by: str
    horizon_days: int
    chart_rule: str = DEFAULT_CHART_RULE
    signals: list[Signal] = field(default_factory=list)
    measurements: dict[str, object] = field(default_factory=dict)

    def by_family(self) -> dict[str, list[Signal]]:
        out: dict[str, list[Signal]] = {}
        for signal in self.signals:
            out.setdefault(signal.family, []).append(signal)
        return out

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "fabops.monitor/v1",
            "dataset_id": self.dataset_id,
            "generated_by": self.generated_by,
            "window": {"start_day": 0, "end_day": self.horizon_days},
            "settings": {"baseline_fraction": BASELINE_FRACTION,
                         "sigma_limit": SIGMA_LIMIT,
                         "min_peers": MIN_PEERS,
                         "chart_rule": self.chart_rule},
            "signals": [signal.to_dict() for signal in self.signals],
            "measurements": self.measurements,
        }


# ------------------------------------------------------------- the arithmetic


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's algorithm for the continued fraction of the incomplete beta."""
    # The first partial numerator is folded into the initial `d`, so the loop
    # starts at the second — applying it twice reads plausible and is wrong
    # only for some (a, b), which is exactly the kind of defect that ships.
    tiny = 1e-30
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    result = d
    for index in range(2, 302):
        m = index // 2
        if index % 2 == 0:
            numerator = m * (b - m) * x / ((a + 2.0 * m - 1.0) * (a + 2.0 * m))
        else:
            numerator = (-(a + m) * (a + b + m) * x
                         / ((a + 2.0 * m) * (a + 2.0 * m + 1.0)))
        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        d = 1.0 / d
        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c
        step = c * d
        result *= step
        if abs(step - 1.0) < 1e-12:
            break
    return result


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta `I_x(a, b)`."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log1p(-x) - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    mirror = math.exp(b * math.log1p(-x) + a * math.log(x) - _log_beta(b, a))
    return 1.0 - mirror * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_two_sided(t: float, degrees_of_freedom: int) -> float:
    """`P(|T| >= t)` for Student's t. Stdlib only, and exact to 1e-12."""
    if degrees_of_freedom <= 0:
        return 1.0
    df = float(degrees_of_freedom)
    return _incomplete_beta(df / 2.0, 0.5, df / (df + t * t))


def t_limit(degrees_of_freedom: int, alpha: float = ACTION_ALPHA) -> float:
    """The two-sided `t` critical value, by bisection on its own tail."""
    low, high = 0.5, 200.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if student_t_two_sided(middle, degrees_of_freedom) > alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def lag_one_autocorrelation(values: Sequence[float]) -> float:
    """The series' own lag-1 correlation, used to discount its sample size.

    A daily series of this fab is not independent day to day — a lot's wafers
    share a lot-level offset and run across consecutive days — so a baseline of
    *k* points carries less information than *k* independent ones. Estimated
    from the whole series rather than from the baseline window alone, because
    it is a second-order property that a level shift barely moves and a
    twelve-point estimate of it would be noise.
    """
    if len(values) < 5:
        return 0.0
    mean = st.fmean(values)
    denominator = sum((value - mean) ** 2 for value in values)
    if denominator <= 0:
        return 0.0
    numerator = sum((values[index] - mean) * (values[index + 1] - mean)
                    for index in range(len(values) - 1))
    return numerator / denominator


def effective_size(baseline_points: int, autocorrelation: float) -> int:
    """`k` discounted for serial correlation: k(1-rho)/(1+rho).

    The standard effective-sample-size correction for an AR(1)-like series.
    Negative correlation is not credited — it would *widen* the effective
    sample and buy tighter limits from a series that happens to zigzag — and
    the discount is capped so one pathological series cannot reduce itself to
    nothing.
    """
    rho = min(max(autocorrelation, 0.0), 0.8)
    return max(4, int(round(baseline_points * (1.0 - rho) / (1.0 + rho))))


def limit_inflation(baseline_points: int, alpha: float = ACTION_ALPHA
                    ) -> float:
    """How much wider the limits must be because the spread was *estimated*.

    A control chart built on a short baseline is judging new points against a
    centre and a spread that are themselves estimates. The correct reference is
    Student's t on the baseline's own degrees of freedom, widened by
    `sqrt(1 + 1/k)` for the centre's uncertainty — the standard prediction
    interval for one new observation.

    Reported as a *factor on the sigma limit* so the rules can go on saying
    "three sigma" and still mean the stated false-alarm rate. With ten baseline
    points it is about 1.37, and skipping it is what made an earlier version of
    this module fire five times too often on a fab with nothing wrong in it.
    """
    if baseline_points < 3:
        return float("inf")
    normal = SIGMA_LIMIT
    return (t_limit(baseline_points - 1, alpha)
            * math.sqrt(1.0 + 1.0 / baseline_points)) / normal


def peer_difference(series: Mapping[str, Mapping[int, float]],
                    groups: Mapping[str, str]
                    ) -> dict[str, dict[int, float]]:
    """Subtract each entity's contemporaneous same-role peer mean.

    Leave-one-out: an entity is not part of its own reference, because
    including it shrinks exactly the departure being measured. An entity whose
    role has fewer than `MIN_PEERS` others is dropped rather than charted
    against itself — a refusal, and one the caller can see in the output.
    """
    out: dict[str, dict[int, float]] = {}
    for entity, points in series.items():
        role = groups.get(entity)
        peers = [other for other in series
                 if other != entity and groups.get(other) == role]
        if len(peers) < MIN_PEERS:
            continue
        differenced: dict[int, float] = {}
        for day, value in points.items():
            contemporaries = [series[peer][day] for peer in peers
                              if day in series[peer]]
            if len(contemporaries) < MIN_PEERS:
                continue
            differenced[day] = value - st.fmean(contemporaries)
        if differenced:
            out[entity] = differenced
    return out


def _chart_of(days: Sequence[int], values: Sequence[float],
              support: Sequence[int], horizon_days: int,
              rule: str) -> tuple[Chart, dict[int, float]] | None:
    """Centre and spread from the declared baseline window, then frozen.

    Returns the chart and, for the `xbar` rule, the per-day spread each point
    is judged against. Freezing is deliberate: a spread that kept adapting
    would widen to accommodate a slow ramp and the fault would become invisible
    by being persistent (ADR-017 §3, applied to the analysis side).
    """
    cut = horizon_days * BASELINE_FRACTION
    baseline = [(value, max(1, int(n)))
                for day, value, n in zip(days, values, support) if day < cut]
    if len(baseline) < MIN_BASELINE_POINTS:
        return None

    # The baseline's *effective* size, not its point count: this fab's daily
    # series are serially correlated, so k points carry less information than k
    # independent ones and a Student-t correction computed from k - 1 is too
    # small. Measured on twelve fault-free worlds: the single-point rule reads
    # 5.93x nominal without this and 2.21x with it.
    effective = effective_size(len(baseline),
                               lag_one_autocorrelation(list(values)))
    inflation = limit_inflation(effective)
    if not math.isfinite(inflation):
        return None

    if rule == "moving_range":
        centre = st.fmean([value for value, _n in baseline])
        ranges = [abs(b - a) for a, b in zip(values, values[1:])]
        if len(ranges) < MIN_BASELINE_POINTS:
            return None
        spread = st.fmean(ranges) / D2_MOVING_RANGE
        if spread <= 0:
            return None
        # The dispersion now carries the whole horizon's degrees of freedom, so
        # the estimation penalty is the baseline's only through the centre.
        chart = Chart(centre=centre, spread=spread,
                      baseline_points=len(baseline),
                      baseline_last_day=int(cut),
                      inflation=limit_inflation(effective_size(
                          len(ranges) + 1,
                          lag_one_autocorrelation(list(values)))))
        return chart, {}

    if rule == "individuals":
        centre = st.fmean([value for value, _n in baseline])
        spread = st.stdev([value for value, _n in baseline])
        if spread <= 0:
            return None
        chart = Chart(centre=centre, spread=spread,
                      baseline_points=len(baseline),
                      baseline_last_day=int(cut), inflation=inflation)
        return chart, {}

    if rule != "xbar":
        raise ValueError(f"unknown chart rule {rule!r}; registered: "
                         f"{list(CHART_RULES)}")

    # An x-bar chart with variable subgroup size. A daily mean of n samples has
    # variance sigma_within^2 / n, so n*(x - centre)^2 estimates sigma_within^2
    # whatever n was — which is what lets days of unequal support share one
    # chart.
    total = sum(n for _value, n in baseline)
    centre = sum(value * n for value, n in baseline) / total
    within = math.sqrt(sum(n * (value - centre) ** 2 for value, n in baseline)
                       / (len(baseline) - 1))
    if within <= 0:
        return None
    mean_support = total / len(baseline)
    chart = Chart(centre=centre, spread=within / math.sqrt(mean_support),
                  baseline_points=len(baseline), baseline_last_day=int(cut),
                  inflation=inflation)
    per_day = {int(day): within / math.sqrt(max(1, int(n)))
               for day, n in zip(days, support)}
    return chart, per_day


def chart_limits(series: Series, horizon_days: int,
                 chart_rule: str = DEFAULT_CHART_RULE
                 ) -> tuple[Chart, tuple[float, ...], tuple[float, ...]] | None:
    """The chart a presentation surface needs to draw: centre and both limits.

    Returned per point, because under the `xbar` rule the limits breathe with
    the day's subgroup size and a drawn chart that ignored that would not be
    the chart the rules were evaluated against. A picture that disagrees with
    the decision it illustrates is worse than no picture.
    """
    built = _chart_of(series.days, series.values, series.support,
                      horizon_days, chart_rule)
    if built is None:
        return None
    chart, per_day = built
    upper, lower = [], []
    for day in series.days:
        spread = (per_day.get(int(day), chart.spread) or chart.spread)
        half = SIGMA_LIMIT * spread * chart.inflation
        upper.append(chart.centre + half)
        lower.append(chart.centre - half)
    return chart, tuple(upper), tuple(lower)


def charted_points(series: Series, horizon_days: int,
                   chart_rule: str = DEFAULT_CHART_RULE) -> int:
    """How many points this series was actually judged on.

    The denominator every per-entity signal count needs. Without it a chamber
    that ran twice as much material looks twice as troubled, and a reader has
    no way to tell that apart from a chamber that is twice as bad.
    """
    built = _chart_of(series.days, series.values, series.support,
                      horizon_days, chart_rule)
    if built is None:
        return 0
    watched = [day for day in series.days
               if day >= built[0].baseline_last_day]
    return len(watched) if len(watched) >= 4 else 0


def spc_signals(series: Series, horizon_days: int, *, family: str,
                entity_kind: str,
                chart_rule: str = DEFAULT_CHART_RULE) -> list[Signal]:
    """Western Electric rules 1–4, plus EWMA and a tabular CUSUM.

    The four WE rules are the ones an engineer would recognize on a shop-floor
    chart, and each answers a different shape: a spike, a shift, a drift, a
    bias. EWMA and CUSUM are added because a *slow* departure is what this
    project's mechanisms produce and rule 1 is nearly blind to it.

    Every rule fires only after the baseline window, and every one reports the
    day it fired on — which is what makes a monitor's output usable as onset
    evidence a human can read.
    """
    built = _chart_of(series.days, series.values, series.support,
                      horizon_days, chart_rule)
    if built is None:
        return []
    chart, per_day = built

    def standardize(day: int, value: float) -> float:
        spread = per_day.get(int(day), chart.spread) or chart.spread
        scale = spread * chart.inflation
        return 0.0 if scale <= 0 else (value - chart.centre) / scale

    watched = [(day, value) for day, value in zip(series.days, series.values)
               if day >= chart.baseline_last_day]
    if len(watched) < 4:
        return []

    zs = [(day, standardize(day, value), value) for day, value in watched]
    signals: list[Signal] = []
    support = int(sum(series.support)) if series.support else len(series.days)
    seen: set[tuple[str, int]] = set()

    def emit(rule: str, day: int, value: float, z: float, limit: float,
             detail: str) -> None:
        # One signal per rule per day. A sliding-window rule can recognize the
        # same point from two windows, and a report that counted it twice
        # would make the same fab look noisier than it is.
        if (rule, day) in seen:
            return
        seen.add((rule, day))
        signals.append(Signal(family=family, entity_kind=entity_kind,
                              entity=series.entity, channel=series.channel,
                              rule=rule, day_index=day, value=value, z=z,
                              limit=limit, support=support, detail=detail))

    # WE1 — one point beyond the action limit.
    for day, z, value in zs:
        if abs(z) >= SIGMA_LIMIT:
            emit("we1_beyond_3_sigma", day, value, z,
                 chart.centre + math.copysign(SIGMA_LIMIT * chart.spread, z),
                 "a single point outside the frozen control limits")

    # WE2 — two of three consecutive beyond 2 sigma on the same side.
    for index in range(len(zs) - 2):
        window = zs[index:index + 3]
        for sign in (1, -1):
            hits = [row for row in window if row[1] * sign >= 2.0]
            if len(hits) >= 2:
                day, z, value = hits[-1]
                emit("we2_two_of_three_beyond_2_sigma", day, value, z,
                     chart.centre + sign * 2.0 * chart.spread,
                     "two of three consecutive points beyond two sigma on the "
                     "same side")
                break

    # WE3 — four of five consecutive beyond 1 sigma on the same side.
    for index in range(len(zs) - 4):
        window = zs[index:index + 5]
        for sign in (1, -1):
            hits = [row for row in window if row[1] * sign >= 1.0]
            if len(hits) >= 4:
                day, z, value = hits[-1]
                emit("we3_four_of_five_beyond_1_sigma", day, value, z,
                     chart.centre + sign * chart.spread,
                     "four of five consecutive points beyond one sigma on the "
                     "same side")
                break

    # WE4 — eight consecutive on one side of the centre.
    run_sign, run_length = 0, 0
    for day, z, value in zs:
        sign = 1 if z > 0 else -1 if z < 0 else 0
        run_length = run_length + 1 if sign == run_sign and sign else 1
        run_sign = sign
        if run_length == 8:
            emit("we4_eight_on_one_side", day, value, z, chart.centre,
                 "eight consecutive points on one side of the centre line")

    # EWMA — the standard smoother, at the same action level. Its steady-state
    # spread is sigma * sqrt(l / (2 - l)), which is what makes the limit
    # comparable to rule 1's rather than merely tighter.
    weight = 0.2
    ewma = 0.0
    ewma_spread = math.sqrt(weight / (2.0 - weight))
    for day, z, value in zs:
        ewma = weight * z + (1.0 - weight) * ewma
        if abs(ewma) >= SIGMA_LIMIT * ewma_spread:
            emit("ewma_beyond_3_sigma", day, value, ewma / ewma_spread,
                 chart.centre + SIGMA_LIMIT * ewma_spread * chart.spread,
                 f"exponentially weighted mean (lambda {weight}) outside its "
                 f"own limits")
            ewma = 0.0

    # CUSUM — tabular, slack 0.5 sigma, decision interval 5 sigma. Designed for
    # a sustained shift of about one sigma, which is the size this fab's
    # mechanisms actually produce.
    slack, interval = 0.5, 5.0
    high = low = 0.0
    for day, z, value in zs:
        high = max(0.0, high + z - slack)
        low = min(0.0, low + z + slack)
        if high >= interval or low <= -interval:
            emit("cusum_shift", day, value, high if high >= interval else low,
                 interval,
                 f"cumulative sum passed its decision interval "
                 f"(k={slack}, h={interval})")
            high = low = 0.0

    return signals


def series_from(points: Mapping[str, Mapping[int, float]],
                support: Mapping[str, Mapping[int, int]],
                channel: str) -> list[Series]:
    """Turn `{entity: {day: value}}` into ordered `Series`, entity order fixed."""
    out: list[Series] = []
    for entity in sorted(points):
        days = sorted(points[entity])
        out.append(Series(entity=entity, channel=channel,
                          days=tuple(days),
                          values=tuple(points[entity][day] for day in days),
                          support=tuple(support.get(entity, {}).get(day, 1)
                                        for day in days)))
    return out


def order_signals(signals: Iterable[Signal]) -> list[Signal]:
    """One total order, so two runs print the same report."""
    return sorted(signals, key=lambda s: (s.family, s.entity_kind, s.entity,
                                          s.channel, s.day_index, s.rule))
