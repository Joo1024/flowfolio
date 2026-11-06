from copy import deepcopy
from dataclasses import replace
import pytest
from flowfolio import D, run, ExperimentCase
from flowfolio.verify import verify, compare


def test_flat_contributions_are_not_profit(root, runtime):
    for package in (root / "strategies").iterdir():
        result = run(package, root / "cases/flat.json", runtime)
        assert result.manifest["status"] == "completed"
        assert result.metrics["final_value"] == 300
        assert result.metrics["profit"] == 0
        assert result.metrics["time_weighted_return"] == 0


def test_hand_fees(root, runtime):
    result = run(root / "strategies/dca", root / "cases/hand_fees.json", runtime)
    assert [f["quantity"] for f in result.fills] == [D(10), D(5)]
    assert [f["fee"] for f in result.fills] == [D(2), D(2)]
    assert result.metrics["final_value"] == 300
    assert result.metrics["total_fees"] == 4
    assert abs(result.metrics["max_drawdown"] - D(2)/102) < D("1e-10")


def test_individual_same_symbol_fees_and_provenance(make_package, one_period, runtime):
    one_period["fees"] = {"fixed": "1", "rate": "0.01"}
    pkg = make_package('return Decision((OrderIntent("EQUITY", "buy", D(50), "first", "a"), OrderIntent("EQUITY", "buy", D(50), "second", "b")))')
    result = run(pkg, ExperimentCase.from_dict(one_period), runtime)
    assert result.manifest["status"] == "completed"
    assert len(result.fills) == 2
    assert [f["reason"] for f in result.fills] == ["first", "second"]
    assert [f["rule_id"] for f in result.fills] == ["a", "b"]
    assert all(f["fee"] == D("1.49") and -f["cash_delta"] <= 50 for f in result.fills)
    assert result.metrics["total_fees"] == D("2.98")


def test_transfers_do_not_change_equity(make_package, one_period, runtime):
    pkg = make_package('return Decision((TransferIntent("invest", "reserve", D(70), "save", "save"), TransferIntent("reserve", "invest", D(20), "release", "release")))')
    result = run(pkg, ExperimentCase.from_dict(one_period), runtime)
    assert result.valuations[-1]["equity"] == 100
    assert result.valuations[-1]["budgets"] == {"invest": D(50), "reserve": D(50)}
    assert result.metrics["profit"] == 0


@pytest.mark.parametrize("intent,error", [
    ('OrderIntent("EQUITY", "sell", D(1), "sell", "test")', "oversell"),
    ('OrderIntent("NOPE", "buy", D(10), "bad", "test")', "unknown_symbol"),
    ('OrderIntent("EQUITY", "buy", D(-1), "bad", "test")', "nonpositive_amount"),
    ('OrderIntent("EQUITY", "buy", D(10), "bad", "test", "missing")', "unknown_budget"),
    ('OrderIntent("EQUITY", "short", D(10), "bad", "test")', "invalid_side"),
    ('OrderIntent("EQUITY", "buy", D(101), "bad", "test")', "insufficient_budget"),
])
def test_rejected_order_no_mutation(make_package, one_period, runtime, intent, error):
    one_period["execution"]["insufficient_cash"] = "reject"
    pkg = make_package(f"return Decision(({intent},))")
    result = run(pkg, ExperimentCase.from_dict(one_period), runtime)
    assert result.fills[0]["rejection"] == error
    assert result.valuations[-1]["cash"] == 100
    assert all(q == 0 for q in result.valuations[-1]["positions"].values())
    assert result.metrics["total_fees"] == 0


def test_clip_reports_residual_and_partial(make_package, one_period, runtime):
    pkg = make_package('return Decision((OrderIntent("EQUITY", "buy", D(150), "large", "clip"),))')
    result = run(pkg, ExperimentCase.from_dict(one_period), runtime)
    assert result.fills[0]["status"] == "partial"
    assert result.fills[0]["residual"] == 50
    assert result.fills[0]["quantity"] == 10


def test_sales_cannot_fund_same_batch_and_cannot_oversell(make_package, one_period, runtime):
    one_period["initial_positions"] = {"EQUITY": "10"}
    one_period["cashflows"] = []
    pkg = make_package('return Decision((OrderIntent("EQUITY", "sell", D(6), "first", "a"), OrderIntent("EQUITY", "sell", D(6), "second", "b"), OrderIntent("BOND", "buy", D(60), "cash", "c")))')
    result = run(pkg, ExperimentCase.from_dict(one_period), runtime)
    assert [f["status"] for f in result.fills] == ["filled", "rejected", "rejected"]
    assert result.valuations[-1]["positions"]["EQUITY"] == 4
    assert result.valuations[-1]["cash"] == 60


def test_fee_exceeds_sell_proceeds(make_package, one_period, runtime):
    one_period["initial_positions"] = {"EQUITY": "1"}
    one_period["fees"]["fixed"] = "11"
    pkg = make_package('return Decision((OrderIntent("EQUITY", "sell", D(1), "sale", "sell"),))')
    result = run(pkg, ExperimentCase.from_dict(one_period), runtime)
    assert result.fills[0]["rejection"] == "fee_exceeds_proceeds"
    assert result.valuations[-1]["positions"]["EQUITY"] == 1


def test_budget_reservations_prevent_double_spend(make_package, one_period, runtime):
    pkg = make_package('return Decision((OrderIntent("EQUITY", "buy", D(80), "one", "a"), TransferIntent("invest", "reserve", D(30), "save", "b"), OrderIntent("EQUITY", "buy", D(80), "two", "c")))')
    result = run(pkg, ExperimentCase.from_dict(one_period), runtime)
    assert result.transfers[0]["status"] == "rejected"
    assert [f["quantity"] for f in result.fills] == [D(8), D(2)]
    assert result.fills[1]["status"] == "partial"
    assert result.valuations[-1]["cash"] == 0


def test_fee_round_half_up_and_max_affordable():
    from flowfolio.model import FeeModel, LOT
    from flowfolio.semantics import affordable_quantity
    fees = FeeModel(D("0"), D("0.01"))
    assert fees.calculate(D(1), D("0.5")) == D("0.01")
    for fixed in ("0", "0.005", "1", "5"):
        for rate in ("0", "0.001", "0.01"):
            model = FeeModel(D(fixed), D(rate))
            for budget in map(D, ("0.01", "1", "100", "102.33")):
                price = D("13.1234")
                qty = affordable_quantity(budget, price, model)
                assert qty * price + model.calculate(qty, price) <= budget
                assert (qty + LOT) * price + model.calculate(qty + LOT, price) > budget


def test_independent_verifier_detects_tampered_ledger(root):
    case = ExperimentCase.load(root / "cases/hand_fees.json")
    result = run(root / "strategies/dca", case)
    altered = deepcopy(result)
    altered.fills[0]["fee"] += 1
    assert any(x["path"].endswith(".fee") for x in verify(altered, case))
    altered = deepcopy(result)
    altered.valuations[1]["cash"] += 1
    assert any("cash" in x["path"] for x in verify(altered, case))


def test_comparator_reports_execution_and_accounting(root):
    result = run(root / "strategies/dca", root / "cases/hand_fees.json")
    altered = deepcopy(result)
    altered.fills[0]["quantity"] += 1
    altered.valuations[1]["cash"] += 1
    differences = compare(result, altered)
    assert {x["layer"] for x in differences} == {"execution", "accounting"}


def test_numeric_looking_rule_ids_are_compared_exactly(root):
    result = run(root / "strategies/dca", root / "cases/hand_fees.json")
    altered = deepcopy(result)
    result.fills[0]["rule_id"] = "001"
    altered.fills[0]["rule_id"] = "1"
    assert compare(result, altered)[0]["path"] == "fills[0].rule_id"


def test_empty_account_without_flows(root, one_period, runtime):
    one_period["cashflows"] = []
    result = run(root / "strategies/dca", ExperimentCase.from_dict(one_period), runtime)
    assert result.manifest["status"] == "completed"
    assert result.metrics["final_value"] == 0
    assert result.metrics["time_weighted_return"] == 0
    assert not result.fills
