import json
import pytest
from flowfolio import D, run, ExperimentCase, StrategyPackage, FutureDataError
from flowfolio.model import Bar, MarketView
from flowfolio.runtimes.reference import ReferenceRuntime


def test_readonly_and_feedback(make_package, flat_data, runtime):
    pkg = make_package('''for mapping, key in ((c.positions, "EQUITY"), (c.budgets, "invest"), (c.market.prices, "EQUITY"), (c.state, "x")):
    try:
        mapping[key] = D(99)
        raise AssertionError("mutable context")
    except TypeError:
        pass
assert c.market.bars[-1].date < c.date
try:
    c.market.at(c.date)
    raise AssertionError("future data exposed")
except FutureDataError:
    pass
if c.state:
    assert c.feedback[0]["quantity"] == D(10)
    assert c.feedback[0]["rule_id"] == "monthly"
    assert c.state["nested"]["values"] == ("ok",)
return Decision((OrderIntent("EQUITY", "buy", D(100), "test", "monthly"),), {"nested": {"values": ["ok"]}})''')
    result = run(pkg, ExperimentCase.from_dict(flat_data), runtime)
    assert result.manifest["status"] == "completed"


@pytest.mark.parametrize("capability", ["withdrawals", "multi_currency", "short_sales", "volume_partial", "unknown"])
def test_unsupported_before_import(make_package, flat_data, capability, runtime):
    pkg = make_package("raise AssertionError('must not execute')", requires=[capability])
    (pkg / "strategy.py").write_text("raise AssertionError('must not import')\n")
    result = run(pkg, ExperimentCase.from_dict(flat_data), runtime)
    assert result.manifest["status"] == "unsupported"
    assert capability in result.diagnostics[0]["message"]
    assert not result.fills and not result.valuations


@pytest.mark.parametrize("change", ["withdrawal", "slippage", "volume", "timing", "adjustment", "policy", "protocol"])
def test_case_requirements_are_inferred(root, flat_data, runtime, change):
    if change == "withdrawal": flat_data["cashflows"][0]["amount"] = "-1"
    elif change == "slippage": flat_data["execution"]["slippage"] = "0.01"
    elif change == "volume": flat_data["execution"]["volume_limit"] = "10"
    elif change == "timing": flat_data["execution"]["timing"] = "single_account"
    elif change == "adjustment": flat_data["execution"]["price_adjustment"] = "raw"
    elif change == "policy": flat_data["execution"]["insufficient_cash"] = "silent"
    else: flat_data["protocol"] = "2.0"
    result = run(root / "strategies/dca", ExperimentCase.from_dict(flat_data), runtime)
    assert result.manifest["status"] == "unsupported"


def test_missing_fee_capability_is_not_ignored(root):
    class Limited(ReferenceRuntime):
        capabilities = ReferenceRuntime.capabilities - {"fixed_fee"}
    result = run(root / "strategies/dca", root / "cases/hand_fees.json", Limited())
    assert result.manifest["status"] == "unsupported"
    assert "fixed_fee" in result.diagnostics[0]["message"]


@pytest.mark.parametrize("corrupt", ["nan", "numeric", "zero_price", "missing_symbol", "duplicate_date", "bad_date", "unknown_key", "negative_fee", "too_precise", "same_month"])
def test_invalid_case_rejected(flat_data, corrupt):
    if corrupt == "nan": flat_data["initial_cash"] = "NaN"
    elif corrupt == "numeric": flat_data["initial_cash"] = 0
    elif corrupt == "zero_price": flat_data["bars"][0]["open"]["EQUITY"] = "0"
    elif corrupt == "missing_symbol": del flat_data["bars"][0]["open"]["EQUITY"]
    elif corrupt == "duplicate_date": flat_data["bars"][1]["date"] = flat_data["bars"][0]["date"]
    elif corrupt == "bad_date": flat_data["bars"][1]["date"] = "invalid"
    elif corrupt == "unknown_key": flat_data["silently_ignore_fee"] = True
    elif corrupt == "negative_fee": flat_data["fees"]["fixed"] = "-1"
    elif corrupt == "too_precise": flat_data["bars"][0]["open"]["EQUITY"] = "1.00001"
    elif corrupt == "same_month": flat_data["cashflows"].append(flat_data["cashflows"][0].copy())
    with pytest.raises(ValueError): ExperimentCase.from_dict(flat_data)


def test_parameter_validation_and_hash(root, tmp_path):
    package = StrategyPackage.load(root / "strategies/reserve_drawdown")
    with pytest.raises(ValueError): package.parameters({"drawdown": "-1"})
    with pytest.raises(ValueError): package.parameters({"typo": "1"})
    case = ExperimentCase.load(root / "cases/flat.json")
    changed = json.loads((root / "cases/flat.json").read_text())
    changed["cashflows"][0]["amount"] = "101"
    assert case.fingerprint != ExperimentCase.from_dict(changed).fingerprint


def test_same_inputs_reproduce_exactly(root, runtime):
    a = run(root / "strategies/reserve_drawdown", root / "cases/hand_fees.json", runtime)
    b = run(root / "strategies/reserve_drawdown", root / "cases/hand_fees.json", runtime)
    assert a.to_json() == b.to_json()


def test_changed_future_does_not_change_past(root, flat_data, runtime):
    before = run(root / "strategies/reserve_drawdown", ExperimentCase.from_dict(flat_data), runtime)
    flat_data["bars"][-1]["open"]["EQUITY"] = "100"
    flat_data["bars"][-1]["close"]["EQUITY"] = "200"
    flat_data["evaluation"] = {}
    after = run(root / "strategies/reserve_drawdown", ExperimentCase.from_dict(flat_data), runtime)
    assert before.intents == after.intents
    assert before.valuations[:-1] == after.valuations[:-1]


def test_package_cannot_depend_on_backends(root):
    import ast
    for file in (root / "strategies").glob("*/strategy.py"):
        code = file.read_text()
        tree = ast.parse(code)
        assert all(node.module == "flowfolio" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
        assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
        assert "backtrader" not in code and "reference" not in code


def test_host_decimal_context_cannot_change_results(root, runtime):
    from decimal import localcontext
    expected = run(root / "strategies/reserve_drawdown", root / "cases/allocation_cycle.json", runtime)
    with localcontext() as context:
        context.prec = 6
        actual = run(root / "strategies/reserve_drawdown", root / "cases/allocation_cycle.json", runtime)
        assert context.prec == 6
    assert actual.to_json() == expected.to_json()


def test_runtime_entrypoint(root, monkeypatch):
    from flowfolio import sdk
    class Plugin:
        def load(self): return ReferenceRuntime
    monkeypatch.setattr(sdk, "entry_points", lambda **kw: [Plugin()] if kw["name"] == "plugin" else [])
    result = run(root / "strategies/dca", root / "cases/flat.json", "plugin")
    assert result.metrics["final_value"] == 300
    with pytest.raises(ValueError): sdk.runtime_by_name("missing")
