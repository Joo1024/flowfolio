"""Protocol 1.0 simulation rules.

The warmup bar establishes the initial valuation. Each following bar begins
with external cash, then a decision with only previously published bars and
last-period feedback. The executor processes prepared orders at the open and
reports actual fills before the close valuation.

Budget labels and reservations belong to the protocol. Each executor owns
cash, positions, order execution and native valuations.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from .model import (
    AccountState, ActionRequest, D, Fill, LOT, MarketView, OrderIntent, PROTOCOL,
    Scenario, StrategyContext, TOLERANCE, TransferIntent, ZERO,
)


CAPABILITIES = frozenset({
    "single_account", "single_currency", "long_only", "fractional_shares",
    "monthly_contributions", "fixed_fee", "proportional_fee", "budget_transfers",
    "cash_budget_orders", "previous_close_next_open", "total_return_adjusted",
    "insufficient_reject", "insufficient_clip", "initial_positions", "deterministic",
})
RESERVED = frozenset({"multi_currency", "withdrawals", "short_sales", "volume_partial", "slippage", "live_trading"})


def compatibility(package, scenario: Scenario, simulator):
    reasons = []
    if package.metadata["protocol"] != PROTOCOL or scenario.protocol != PROTOCOL:
        reasons.append("protocol: only 1.0 is supported")
    required = set(package.metadata["requires"]) | scenario.requires
    required |= {"single_account", "single_currency", "long_only", "fractional_shares", "cash_budget_orders"}
    ex = scenario.execution
    for key, supported in (("timing", {"previous_close_next_open"}),
                           ("price_adjustment", {"total_return_adjusted"}),
                           ("insufficient_cash", {"clip", "reject"})):
        if ex[key] not in supported:
            reasons.append(f"execution:{key}={ex[key]}: unsupported")
    required |= {ex["timing"], ex["price_adjustment"], "insufficient_" + ex["insufficient_cash"]}
    if scenario.cashflows:
        required.add("monthly_contributions")
    if any(flow.amount < 0 for flow in scenario.cashflows):
        required.add("withdrawals")
    if scenario.fees.fixed:
        required.add("fixed_fee")
    if scenario.fees.rate:
        required.add("proportional_fee")
    if scenario.initial_account.positions:
        required.add("initial_positions")
    if D(ex["slippage"]) != 0:
        required.add("slippage")
    if "volume_limit" in ex:
        required.add("volume_partial")
    reasons += [f"capability:{x}: unsupported by protocol {PROTOCOL}" for x in sorted(required - CAPABILITIES)]
    reasons += [f"capability:{x}: unsupported by {simulator.name}"
                for x in sorted((required & CAPABILITIES) - simulator.capabilities)]
    if hasattr(simulator, "availability"):
        reasons += simulator.availability()
    return reasons


def affordable_quantity(budget, price, fees):
    """Maximum 1e-8 lot satisfying the exact rounded fee, by monotone search."""
    low, high = 0, int((budget / price / LOT).to_integral_value(rounding=ROUND_FLOOR))
    while low < high:
        mid = (low + high + 1) // 2
        quantity = mid * LOT
        if quantity * price + fees.calculate(quantity, price) <= budget:
            low = mid
        else:
            high = mid - 1
    return low * LOT


@dataclass(frozen=True)
class PreparedOrder:
    intent_id: str
    intent: OrderIntent
    quantity: Decimal
    price: Decimal
    fee: Decimal
    allocation: Decimal
    partial: bool


def prepare_request(intent_id, intent: ActionRequest, prices, budgets, holdings, fees, policy):
    """Check an action against the current batch reservations."""
    if not isinstance(intent, (OrderIntent, TransferIntent)):
        raise TypeError("decision contains a non-protocol intent")
    amount = D(intent.amount)
    if not intent.reason or not intent.rule_id:
        return None, "missing_provenance"
    if amount <= 0:
        return None, "nonpositive_amount"
    if isinstance(intent, TransferIntent):
        if intent.source == intent.target or intent.source not in budgets or intent.target not in budgets:
            return None, "invalid_budget_transfer"
        if amount > budgets[intent.source]:
            return None, "insufficient_budget"
        return None, None
    if intent.symbol not in prices:
        return None, "unknown_symbol"
    if intent.budget not in budgets:
        return None, "unknown_budget"
    price = prices[intent.symbol]
    available = budgets[intent.budget]
    if intent.side == "buy":
        if amount > available and policy == "reject":
            return None, "insufficient_budget"
        allocation = min(amount, available)
        quantity = affordable_quantity(allocation, price, fees)
        if quantity <= 0:
            return None, "budget_below_minimum_fill"
        return PreparedOrder(intent_id, intent, quantity, price, fees.calculate(quantity, price), allocation, amount > available), None
    if intent.side != "sell":
        return None, "invalid_side"
    if amount > holdings.get(intent.symbol, ZERO):
        return None, "oversell"
    quantity = (amount / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
    if quantity <= 0:
        return None, "quantity_below_lot"
    fee = fees.calculate(quantity, price)
    if quantity * price < fee:
        return None, "fee_exceeds_proceeds"
    return PreparedOrder(intent_id, intent, quantity, price, fee, ZERO, False), None


def require_phase(phase, expected, event):
    if phase != expected:
        raise RuntimeError(f"cannot {event} during {phase}; expected {expected}")


def require_next_bar(index, previous, bar_count):
    if index != previous + 1 or index >= bar_count:
        raise RuntimeError(f"invalid execution bar {index} after bar {previous}")


def require_complete(phase, index, bar_count):
    require_phase(phase, "ready", "complete run")
    if index != bar_count - 1:
        raise RuntimeError("simulator stopped before the final valuation")


def decision_context(scenario, index, account: AccountState, feedback, state, contribution):
    market = MarketView(scenario.bars[:index])
    equity = account.cash + sum(
        (quantity * market.prices[symbol] for symbol, quantity in account.positions.items()), ZERO
    )
    return StrategyContext(scenario.bars[index].date, market, account, feedback, state,
                           contribution, equity)


def validate_account(cash, positions, budget_total, equity=None):
    if abs(budget_total - cash) > TOLERANCE:
        raise RuntimeError("budget/native cash reconciliation failed")
    if cash < -TOLERANCE or any(quantity < -TOLERANCE for quantity in positions.values()):
        raise RuntimeError("native simulator violated long-only cash account")
    if equity is not None and equity > D("1000000000"):
        raise RuntimeError("account value exceeds protocol 1.0 numeric range")


def feedback_order(phase, pending, intent_id):
    require_phase(phase, "open", "report order feedback")
    if intent_id not in pending:
        raise RuntimeError(f"no pending order for feedback {intent_id}")
    return pending.pop(intent_id)


def fill_differences(fill: Fill, order: PreparedOrder):
    for field in ("quantity", "price", "fee"):
        actual, expected = getattr(fill, field), getattr(order, field)
        if actual != expected:
            yield {"layer": "execution", "path": f"{fill.intent_id}.{field}",
                   "actual": str(actual), "expected": str(expected)}
