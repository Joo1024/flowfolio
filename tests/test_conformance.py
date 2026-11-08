import json
import random
import pytest
from flowfolio import D, run, Scenario, EvaluationSpec, evaluate
from flowfolio.verify import compare

pytest.importorskip("backtrader")


@pytest.mark.parametrize("strategy", ["dca", "reserve_drawdown", "threshold_rebalance"])
@pytest.mark.parametrize("case", ["flat", "hand_fees", "allocation_cycle"])
def test_all_packages_cross_simulator(root, strategy, case):
    a = run(root / "strategies" / strategy, root / "cases" / f"{case}.json")
    b = run(root / "strategies" / strategy, root / "cases" / f"{case}.json", "backtrader")
    assert a.manifest["status"] == b.manifest["status"] == "completed", (a.diagnostics, b.diagnostics)
    assert not compare(a, b), json.dumps(compare(a, b), indent=2)


@pytest.mark.parametrize("seed", range(12))
def test_seeded_prices_cashflows_and_fees(root, seed):
    rng = random.Random(seed)
    data = json.loads((root / "cases/allocation_cycle.json").read_text())
    data["fees"] = {"fixed": str(rng.choice([0, 0.005, 0.5, 1])), "rate": str(rng.choice([0, 0.001, 0.01]))}
    for bar in data["bars"]:
        for field in ("open", "close"):
            for symbol in bar[field]:
                bar[field][symbol] = str(D(rng.randint(10000, 2000000)) / 10000)
    for flow in data["cashflows"]:
        flow["amount"] = str(D(rng.randint(1, 100000)) / 100)
    case = Scenario.from_dict(data)
    for name in ("dca", "reserve_drawdown", "threshold_rebalance"):
        a = run(root / "strategies" / name, case)
        b = run(root / "strategies" / name, case, "backtrader")
        assert a.manifest["status"] == b.manifest["status"] == "completed", (seed, name, a.diagnostics, b.diagnostics)
        assert not compare(a, b), (seed, name, compare(a, b))


def test_native_engine_executes_and_accounts(root, monkeypatch):
    import backtrader as bt
    from flowfolio.simulators.reference import ReferenceSimulator
    observed = []
    original = bt.brokers.BackBroker._execute

    def native(self, order, *args, **kwargs):
        before = self.getcash()
        result = original(self, order, *args, **kwargs)
        if kwargs.get("ago") is not None:
            assert order.exectype == bt.Order.Market
            observed.append((D(before) - D(self.getcash()), D(order.executed.size),
                             D(order.executed.comm), D(self.getposition(order.data).size)))
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("third-party simulator called reference engine")

    monkeypatch.setattr(bt.brokers.BackBroker, "_execute", native)
    monkeypatch.setattr(ReferenceSimulator, "execute", forbidden)
    result = run(root / "strategies/dca", root / "cases/hand_fees.json", "backtrader")
    assert observed == [(D(102), D(10), D(2), D(10)), (D(102), D(5), D(2), D(15))]
    assert result.valuations[-1]["equity"] == 300


def test_exact_budget_margin_regression(root):
    result = run(root / "strategies/threshold_rebalance", root / "cases/hand_fees.json", "backtrader")
    assert len(result.fills) == 4
    assert all(f["status"] == "filled" for f in result.fills)
    assert result.valuations[-1]["cash"] == 0
    metrics = evaluate(result, EvaluationSpec()).metrics
    assert metrics["final_value"] == D("257.62")


def test_drawdown_and_sales_actually_trigger(root):
    reserve = run(root / "strategies/reserve_drawdown", root / "cases/allocation_cycle.json", "backtrader")
    assert any(f["rule_id"] == "reserve.drawdown" for f in reserve.fills)
    assert len({f["date"] for f in reserve.fills}) < len(reserve.fills)
    rebalance = run(root / "strategies/threshold_rebalance", root / "cases/allocation_cycle.json", "backtrader")
    assert any(f["side"] == "sell" and f["status"] == "filled" for f in rebalance.fills)


@pytest.mark.parametrize("price,contribution", [("0.0001", "999999999.99999999"), ("999999999", "100"), ("0.1234", "1000000.12345678")])
def test_numeric_boundaries(root, one_period, price, contribution):
    for bar in one_period["bars"]:
        for field in ("open", "close"):
            bar[field]["EQUITY"] = price
    one_period["cashflows"][0]["amount"] = contribution
    case = Scenario.from_dict(one_period)
    a = run(root / "strategies/dca", case)
    b = run(root / "strategies/dca", case, "backtrader")
    assert a.manifest["status"] == b.manifest["status"] == "completed"
    assert not compare(a, b), compare(a, b)
    assert b.fills[0]["cash_delta"] >= -D(contribution)
