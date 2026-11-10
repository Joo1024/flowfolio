import json
import pytest
from flowfolio import D, run, Scenario, StrategyPackage, FutureDataError, EvaluationSpec, evaluate
from flowfolio.model import Bar, MarketView
from flowfolio.engines.reference import ReferenceExecutionEngine
from flowfolio.verify import verify


def test_readonly_and_feedback(make_package, flat_data, engine):
    pkg = make_package('''assert isinstance(c, StrategyContext)
assert isinstance(c.account, AccountState)
for mapping, key in ((c.account.positions, "EQUITY"), (c.account.budgets, "invest"), (c.market.prices, "EQUITY"), (c.state, "x")):
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
return StrategyDecision((OrderIntent("EQUITY", "buy", D(100), "test", "monthly"),), {"nested": {"values": ["ok"]}})''')
    result = run(pkg, Scenario.from_dict(flat_data), engine)
    assert result.manifest["status"] == "completed"


def test_verification_expectations_are_separate_from_scenario(root, flat_data):
    original = Scenario.from_dict(flat_data)
    flat_data["evaluation"] = {"expected_metrics": {"final_value": "301"}}
    changed = Scenario.from_dict(flat_data)
    assert original == changed
    assert original.fingerprint == changed.fingerprint

    strategy = root / "strategies/dca"
    baseline = run(strategy, original)
    assert [finding["path"] for finding in verify(baseline, changed)] == ["evaluation.final_value"]
    result = run(strategy, changed)
    assert result.valuations == baseline.valuations
    assert result.manifest["status"] == "invalid"
    assert [finding["path"] for finding in result.diagnostics] == ["evaluation.final_value"]


@pytest.mark.parametrize("capability", ["withdrawals", "multi_currency", "short_sales", "volume_partial", "unknown"])
def test_unsupported_before_import(make_package, flat_data, capability, engine):
    pkg = make_package("raise AssertionError('must not execute')", requires=[capability])
    (pkg / "strategy.py").write_text("raise AssertionError('must not import')\n")
    result = run(pkg, Scenario.from_dict(flat_data), engine)
    assert result.manifest["status"] == "unsupported"
    assert capability in result.diagnostics[0]["message"]
    assert not result.fills and not result.valuations


@pytest.mark.parametrize("change", ["withdrawal", "slippage", "volume", "timing", "adjustment", "policy", "protocol"])
def test_case_requirements_are_inferred(root, flat_data, engine, change):
    if change == "withdrawal": flat_data["cashflows"][0]["amount"] = "-1"
    elif change == "slippage": flat_data["execution"]["slippage"] = "0.01"
    elif change == "volume": flat_data["execution"]["volume_limit"] = "10"
    elif change == "timing": flat_data["execution"]["timing"] = "single_account"
    elif change == "adjustment": flat_data["execution"]["price_adjustment"] = "raw"
    elif change == "policy": flat_data["execution"]["insufficient_cash"] = "silent"
    else: flat_data["protocol"] = "2.0"
    result = run(root / "strategies/dca", Scenario.from_dict(flat_data), engine)
    assert result.manifest["status"] == "unsupported"


def test_missing_fee_capability_is_not_ignored(root):
    class Limited(ReferenceExecutionEngine):
        capabilities = ReferenceExecutionEngine.capabilities - {"fixed_fee"}
    result = run(root / "strategies/dca", root / "cases/hand_fees.json", Limited())
    assert result.manifest["status"] == "unsupported"
    assert "fixed_fee" in result.diagnostics[0]["message"]


def test_engine_cannot_enable_undefined_slippage(root, flat_data):
    class ClaimsSlippage(ReferenceExecutionEngine):
        capabilities = ReferenceExecutionEngine.capabilities | {"slippage"}

        def execute(self, run):
            raise AssertionError("must reject before engine execution")

    flat_data["execution"]["slippage"] = "0.01"
    result = run(root / "strategies/dca", Scenario.from_dict(flat_data), ClaimsSlippage())
    assert result.manifest["status"] == "unsupported"
    assert "slippage: unsupported by protocol" in result.diagnostics[0]["message"]


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
    with pytest.raises(ValueError): Scenario.from_dict(flat_data)


def test_parameter_validation_and_hash(root, tmp_path):
    package = StrategyPackage.load(root / "strategies/reserve_drawdown")
    with pytest.raises(ValueError): package.parameters({"drawdown": "-1"})
    with pytest.raises(ValueError): package.parameters({"typo": "1"})
    case = Scenario.load(root / "cases/flat.json")
    changed = json.loads((root / "cases/flat.json").read_text())
    changed["cashflows"][0]["amount"] = "101"
    assert case.fingerprint != Scenario.from_dict(changed).fingerprint


def test_same_inputs_reproduce_exactly(root, engine):
    a = run(root / "strategies/reserve_drawdown", root / "cases/hand_fees.json", engine)
    b = run(root / "strategies/reserve_drawdown", root / "cases/hand_fees.json", engine)
    assert a.to_json() == b.to_json()


def test_changed_future_does_not_change_past(root, flat_data, engine):
    before = run(root / "strategies/reserve_drawdown", Scenario.from_dict(flat_data), engine)
    flat_data["bars"][-1]["open"]["EQUITY"] = "100"
    flat_data["bars"][-1]["close"]["EQUITY"] = "200"
    flat_data["evaluation"] = {}
    after = run(root / "strategies/reserve_drawdown", Scenario.from_dict(flat_data), engine)
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


def test_host_decimal_context_cannot_change_results(root, engine):
    from decimal import localcontext
    expected = run(root / "strategies/reserve_drawdown", root / "cases/allocation_cycle.json", engine)
    with localcontext() as context:
        context.prec = 6
        actual = run(root / "strategies/reserve_drawdown", root / "cases/allocation_cycle.json", engine)
        assert context.prec == 6
    assert actual.to_json() == expected.to_json()


def test_engine_entrypoint(root, tmp_path, monkeypatch):
    from flowfolio import sdk
    metadata = tmp_path / "flowfolio_test_plugin-0.1.dist-info"
    metadata.mkdir()
    (metadata / "entry_points.txt").write_text(
        "[flowfolio.engines]\nentrypoint-reference = flowfolio.engines.reference:ReferenceExecutionEngine\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    result = run(root / "strategies/dca", root / "cases/flat.json", "entrypoint-reference")
    metrics = evaluate(result, EvaluationSpec()).metrics
    assert metrics["final_value"] == 300
    with pytest.raises(ValueError, match="unknown or ambiguous engine"):
        sdk.load_engine("missing")
