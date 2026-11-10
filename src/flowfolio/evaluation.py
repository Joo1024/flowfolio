"""Evaluate a recorded trajectory using a stated metric and cash flow convention."""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from collections.abc import Mapping

from .model import D, DECIMAL_CONTEXT, EVALUATION_METRICS, RunResult, ZERO, freeze


@dataclass(frozen=True)
class EvaluationSpec:
    metrics: tuple[str, ...] = EVALUATION_METRICS
    cash_flow_timing: str = "period_start"

    def __post_init__(self):
        metrics = tuple(self.metrics)
        if not metrics or len(metrics) != len(set(metrics)) or set(metrics) - set(EVALUATION_METRICS):
            raise ValueError("metrics must be a nonempty selection of distinct supported metrics")
        if self.cash_flow_timing != "period_start":
            raise ValueError("only period_start cash flows are supported")
        object.__setattr__(self, "metrics", metrics)


@dataclass(frozen=True)
class EvaluationResult:
    spec: EvaluationSpec
    metrics: Mapping[str, Decimal]
    baseline_metrics: Mapping[str, Decimal] | None = None
    differences: Mapping[str, Decimal] | None = None


def evaluate(result: RunResult, spec: EvaluationSpec, *, baseline: RunResult | None = None) -> EvaluationResult:
    """Calculate metrics from raw ledgers, optionally subtracting a paired baseline."""
    with localcontext(DECIMAL_CONTEXT):
        periods = _periods(result)
        metrics = freeze(_metrics(result, periods, spec))
        if baseline is None:
            return EvaluationResult(spec, metrics)
        baseline_periods = _periods(baseline)
        if (result.manifest["scenario"]["sha256"] != baseline.manifest["scenario"]["sha256"]
                or result.manifest["currency"] != baseline.manifest["currency"]
                or [(day, flow) for day, _, flow in periods]
                != [(day, flow) for day, _, flow in baseline_periods]):
            raise ValueError("baseline must share the scenario, currency, valuation dates and external cash flows")
        baseline_metrics = freeze(_metrics(baseline, baseline_periods, spec))
        differences = freeze({key: value - baseline_metrics[key] for key, value in metrics.items()})
        return EvaluationResult(spec, metrics, baseline_metrics, differences)


def _periods(result):
    if result.manifest["status"] != "completed" or len(result.valuations) < 2:
        raise ValueError("evaluation requires a completed run with initial and period valuations")
    flows = {flow["date"]: D(flow["amount"]) for flow in result.cashflows}
    if len(flows) != len(result.cashflows):
        raise ValueError("evaluation requires at most one cash flow per valuation period")
    periods = [(row["date"], D(row["equity"]), flows.pop(row["date"], ZERO))
               for row in result.valuations]
    dates = [day for day, _, _ in periods]
    if flows or periods[0][2] or sorted(set(dates)) != dates:
        raise ValueError("cash flows and valuations must follow protocol period order")
    if any(D(row["external_flow"]) != period[2] for row, period in zip(result.valuations, periods)):
        raise ValueError("valuation cash flows disagree with the cash flow ledger")
    return periods


def _metrics(result, periods, spec):
    initial = previous = periods[0][1]
    wealth = peak = D(1)
    drawdown = contributions = ZERO
    for _, equity, contribution in periods[1:]:
        denominator = previous + contribution
        if denominator:
            wealth *= equity / denominator
        elif equity:
            raise ValueError("nonzero equity without invested capital")
        peak = max(peak, wealth)
        drawdown = max(drawdown, 1 - wealth / peak)
        contributions += contribution
        previous = equity
    metrics = {
        "final_value": previous,
        "contributions": contributions,
        "profit": previous - initial - contributions,
        "total_fees": sum((D(fill["fee"]) for fill in result.fills), ZERO),
        "time_weighted_return": wealth - 1,
        "max_drawdown": drawdown,
    }
    return {key: metrics[key] for key in spec.metrics}
