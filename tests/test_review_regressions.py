"""Counterexamples found in the first correctness review."""
from copy import deepcopy
import os
import py_compile
import shutil
import sys

import pytest

from flowfolio import D, Scenario, StrategyPackage, run, EvaluationSpec, evaluate
from flowfolio.verify import compare, verify


def test_source_hash_controls_execution_even_with_stale_pyc(make_package, one_period):
    package = make_package('return StrategyDecision((OrderIntent("EQUITY", "buy", D(10), "test", "test"),))')
    source = package / "strategy.py"
    original = source.read_text()
    stamp = source.stat().st_mtime_ns
    py_compile.compile(str(source), doraise=True)
    source.write_text(original.replace("D(10)", "D(20)"))
    os.utime(source, ns=(stamp, stamp))
    one_period["bars"][-1]["close"]["EQUITY"] = "20"
    case = Scenario.from_dict(one_period)
    cached = run(package, case)
    shutil.rmtree(package / "__pycache__")
    clean = run(package, case)
    assert cached.to_json() == clean.to_json()
    metrics = evaluate(cached, EvaluationSpec()).metrics
    assert metrics["final_value"] == D(120)
    assert cached.fills[0]["quantity"] == D(2)
    assert not (package / "__pycache__").exists()


def test_dataclass_with_postponed_annotations(make_package):
    path = make_package("return StrategyDecision()")
    (path / "strategy.py").write_text(
        'from __future__ import annotations\nfrom dataclasses import dataclass\n'
        '@dataclass\nclass Memory:\n    value: str\n'
        'class Strategy:\n    def __init__(self, p): self.state = Memory("ok")\n')
    strategy = StrategyPackage.load(path).instantiate({})
    assert strategy.state.value == "ok"
    assert sys.modules[type(strategy).__module__].Strategy is type(strategy)


def test_failed_construction_restores_module_registration(make_package):
    path = make_package("return StrategyDecision()")
    (path / "strategy.py").write_text(
        'class Strategy:\n    def __init__(self, p):\n'
        '        if p.get("fail"): raise RuntimeError("construction failed")\n')
    package = StrategyPackage.load(path)
    name = f"flowfolio_strategy_{package.fingerprint}"
    with pytest.raises(RuntimeError, match="construction failed"):
        package.instantiate({"fail": True})
    assert name not in sys.modules
    previous = package.instantiate({})
    registered = sys.modules[type(previous).__module__]
    with pytest.raises(RuntimeError, match="construction failed"):
        package.instantiate({"fail": True})
    assert sys.modules[name] is registered


def test_package_changed_after_loading_is_rejected(make_package):
    path = make_package("return StrategyDecision()")
    package = StrategyPackage.load(path)
    with (path / "strategy.py").open("a") as source:
        source.write("\n# modified\n")
    with pytest.raises(ValueError, match="changed"):
        package.instantiate({})


@pytest.mark.parametrize("field,value", [("symbol", "BOND"), ("symbol", "UNKNOWN"), ("side", "sell"), ("budget", "reserve")])
def test_verifier_binds_fill_to_original_intent(root, one_period, field, value):
    case = Scenario.from_dict(one_period)
    result = run(root / "strategies/dca", case)
    result.intents[0][field] = value
    findings = verify(result, case)
    assert any(f["path"].endswith("." + field) for f in findings)


def test_verifier_checks_requested_amount(root, one_period):
    case = Scenario.from_dict(one_period)
    result = run(root / "strategies/dca", case)
    result.fills[0]["requested"] = D(99)
    assert any(f["path"].endswith(".requested") for f in verify(result, case))


def test_verifier_reject_policy_cannot_be_replaced_by_clip(root, one_period):
    result = run(root / "strategies/dca", Scenario.from_dict(one_period))
    result.intents[0]["amount"] = "150"
    result.fills[0].update(requested=D(150), residual=D(50), status="partial")
    one_period["execution"]["insufficient_cash"] = "reject"
    case = Scenario.from_dict(one_period)
    result.manifest["scenario"]["sha256"] = case.fingerprint
    result.manifest["execution"]["insufficient_cash"] = "reject"
    assert any(f["layer"] == "execution" for f in verify(result, case))


def test_comparison_tolerance_cannot_authorize_overspending(root, one_period):
    case = Scenario.from_dict(one_period)
    result = run(root / "strategies/dca", case)
    excess = D("0.0000001")
    result.fills[0].update(quantity=D("10.00000001"), notional=D(100) + excess,
                           cash_delta=-D(100) - excess, residual=-excess)
    result.valuations[-1]["positions"]["EQUITY"] = D("10.00000001")
    result.valuations[-1]["cash"] = -excess
    result.valuations[-1]["budgets"]["invest"] = -excess
    findings = verify(result, case)
    assert any(f.get("message") == "fill exceeds unreserved budget" for f in findings)


@pytest.mark.parametrize("field,value", [("status", "ignored"), ("rejection", "insufficient_budget"), ("price", None)])
def test_invalid_feedback_reports_diagnostics(root, one_period, field, value):
    case = Scenario.from_dict(one_period)
    result = run(root / "strategies/dca", case)
    result.fills[0][field] = value
    assert verify(result, case)


def test_rejection_reason_is_verified(make_package, one_period):
    path = make_package('return StrategyDecision((OrderIntent("EQUITY", "sell", D(1), "test", "test"),))')
    case = Scenario.from_dict(one_period)
    result = run(path, case)
    result.fills[0]["rejection"] = "unknown_symbol"
    assert any(f["path"].endswith(".rejection") for f in verify(result, case))


@pytest.mark.parametrize("field,value", [("source", "reserve"), ("target", "invest"), ("amount", "1"), ("status", "ignored")])
def test_verifier_checks_transfer_contract(root, one_period, field, value):
    case = Scenario.from_dict(one_period)
    result = run(root / "strategies/reserve_drawdown", case)
    result.transfers[0][field] = value
    assert verify(result, case)


def test_verifier_detects_silently_rejected_transfer(make_package, one_period):
    path = make_package('return StrategyDecision((TransferIntent("invest", "reserve", D(25), "save", "save"),))')
    case = Scenario.from_dict(one_period)
    result = run(path, case)
    result.transfers[0].update(status="rejected", rejection="unexplained")
    result.valuations[-1]["budgets"] = {"invest": D(100), "reserve": D(0)}
    assert verify(result, case)


@pytest.mark.parametrize("amount", ["134217728.00000001", "134217728.00000002", "999999999.99999994"])
def test_cashflow_never_loses_decimal_precision(root, one_period, amount):
    pytest.importorskip("backtrader")
    for bar in one_period["bars"]:
        bar["open"]["EQUITY"] = bar["close"]["EQUITY"] = "1"
    one_period["cashflows"][0]["amount"] = amount
    case = Scenario.from_dict(one_period)
    a = run(root / "strategies/dca", case)
    b = run(root / "strategies/dca", case, "backtrader")
    assert b.manifest["status"] == "completed", b.diagnostics
    assert b.fills[0]["quantity"] == D(amount)
    assert b.valuations[-1]["cash"] == 0
    assert not compare(a, b)


@pytest.mark.parametrize("strategy", ["dca", "reserve_drawdown", "threshold_rebalance"])
@pytest.mark.parametrize("initial_cash", ["0", "100"])
def test_midmonth_flow_is_invested_once(root, one_period, simulator, strategy, initial_cash):
    one_period["initial_cash"] = initial_cash
    for day in ("2024-02-15", "2024-02-16"):
        bar = deepcopy(one_period["bars"][-1])
        bar["date"] = day
        one_period["bars"].append(bar)
    one_period["cashflows"][0]["date"] = "2024-02-15"
    result = run(root / "strategies" / strategy, Scenario.from_dict(one_period), simulator)
    assert result.manifest["status"] == "completed", result.diagnostics
    assert any(f["date"] == "2024-02-15" and f["status"] == "filled" for f in result.fills)
    assert not any(f["date"] == "2024-02-16" for f in result.fills)
    assert result.valuations[-1]["budgets"]["invest"] == 0


def test_reserve_does_not_deploy_twice_in_same_month(root, one_period, simulator):
    one_period["initial_cash"] = "100"
    for day in ("2024-03-01", "2024-03-15", "2024-03-16"):
        bar = deepcopy(one_period["bars"][-1])
        bar["date"] = day
        bar["open"]["EQUITY"] = bar["close"]["EQUITY"] = "5"
        one_period["bars"].append(bar)
    one_period["bars"][1]["close"]["EQUITY"] = "5"
    one_period["cashflows"] = [{"date": "2024-03-15", "amount": "100"}]
    result = run(root / "strategies/reserve_drawdown", Scenario.from_dict(one_period), simulator)
    deployments = [f for f in result.fills if f["rule_id"] == "reserve.drawdown"]
    assert len(deployments) == 1
    assert deployments[0]["date"] == "2024-03-01"
    assert any(f["date"] == "2024-03-15" and f["rule_id"] == "reserve.base" for f in result.fills)
