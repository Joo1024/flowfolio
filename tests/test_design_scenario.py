"""Hand-calculated account trajectories from the Flowfolio design article."""

import pytest

from flowfolio import D, EvaluationSpec, RunResult, Scenario, evaluate, run


@pytest.fixture
def design_scenario():
    return Scenario.from_dict({
        "protocol": "1.0",
        "name": "design_article",
        "currency": "CNY",
        "initial_cash": "1000",
        "initial_positions": {},
        "fees": {"fixed": "10", "rate": "0"},
        "execution": {
            "timing": "previous_close_next_open",
            "price_adjustment": "total_return_adjusted",
            "insufficient_cash": "reject",
            "slippage": "0",
        },
        "bars": [
            {"date": "2026-11-06", "open": {"EQUITY": "8"}, "close": {"EQUITY": "8"}},
            {"date": "2026-11-09", "open": {"EQUITY": "10"}, "close": {"EQUITY": "12"}},
            {"date": "2026-11-10", "open": {"EQUITY": "12"}, "close": {"EQUITY": "9"}},
        ],
        "cashflows": [{"date": "2026-11-09", "amount": "1000"}],
    })


@pytest.mark.parametrize(
    "strategy,parameters,shares,cash,day_one,day_two",
    [
        ("reserve_drawdown", {"reserve_fraction": "0.5", "deploy_fraction": "0"}, 99, 1000, 2188, 1891),
        ("dca", {}, 199, 0, 2388, 1791),
    ],
)
def test_design_article_trajectory(root, design_scenario, engine, strategy, parameters, shares, cash, day_one, day_two):
    result = run(root / "strategies" / strategy, design_scenario, engine, parameters)

    assert result.manifest["status"] == "completed", result.diagnostics
    assert [(f["date"], f["quantity"], f["price"], f["fee"]) for f in result.fills] == [
        ("2026-11-09", D(shares), D(10), D(10)),
    ]
    assert [v["external_flow"] for v in result.valuations] == [0, 1000, 0]
    assert [v["cash"] for v in result.valuations] == [1000, cash, cash]
    assert [v["positions"]["EQUITY"] for v in result.valuations] == [0, shares, shares]
    assert [v["equity"] for v in result.valuations] == [1000, day_one, day_two]


def test_evaluate_saved_design_trajectories(root, design_scenario, tmp_path):
    strategy = run(root / "strategies/reserve_drawdown", design_scenario,
                   parameters={"reserve_fraction": "0.5", "deploy_fraction": "0"})
    baseline = run(root / "strategies/dca", design_scenario)
    strategy.write(tmp_path / "strategy")
    baseline.write(tmp_path / "baseline")
    saved = RunResult.load(tmp_path / "strategy")
    saved_baseline = RunResult.load(tmp_path / "baseline")
    snapshot = saved.to_json()

    spec = EvaluationSpec(("final_value", "time_weighted_return", "max_drawdown", "total_fees"))
    report = evaluate(saved, spec, baseline=saved_baseline)
    assert report.metrics["final_value"] == 1891
    assert report.baseline_metrics["final_value"] == 1791
    assert report.differences["final_value"] == 100
    assert report.metrics["time_weighted_return"].quantize(D("0.0001")) == D("-0.0545")
    assert report.baseline_metrics["time_weighted_return"].quantize(D("0.0001")) == D("-0.1045")
    assert report.differences["time_weighted_return"].quantize(D("0.0001")) == D("0.05")
    assert report.metrics["max_drawdown"].quantize(D("0.0001")) == D("0.1357")
    assert report.baseline_metrics["max_drawdown"] == D("0.25")
    assert report.metrics["total_fees"] == report.baseline_metrics["total_fees"] == 10

    assert evaluate(saved, EvaluationSpec(("total_fees",))).metrics == {"total_fees": D(10)}
    assert saved.to_json() == snapshot
    assert saved.to_json() == strategy.to_json()

    saved_baseline.cashflows[0]["amount"] = "999"
    saved_baseline.valuations[1]["external_flow"] = "999"
    with pytest.raises(ValueError, match="baseline must share"):
        evaluate(saved, spec, baseline=saved_baseline)
