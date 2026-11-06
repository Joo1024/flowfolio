"""Shared protocol rules, never an execution engine or trade ledger."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from .model import D, LOT, ZERO, PROTOCOL, OrderIntent

CAPABILITIES = frozenset({
    "single_account", "single_currency", "long_only", "fractional_shares",
    "monthly_contributions", "fixed_fee", "proportional_fee", "budget_transfers",
    "cash_budget_orders", "previous_close_next_open", "total_return_adjusted",
    "insufficient_reject", "insufficient_clip", "initial_positions", "deterministic",
})
RESERVED = frozenset({"multi_currency", "withdrawals", "short_sales", "volume_partial", "slippage", "live_trading"})


def compatibility(package, case, runtime):
    reasons = []
    if package.metadata["protocol"] != PROTOCOL or case.data["protocol"] != PROTOCOL:
        reasons.append("protocol: only 1.0 is supported")
    required = set(package.metadata["requires"]) | set(case.data.get("requires", ()))
    required |= {"single_account", "single_currency", "long_only", "fractional_shares", "cash_budget_orders"}
    ex = case.data["execution"]
    for key, supported in (("timing", {"previous_close_next_open"}),
                           ("price_adjustment", {"total_return_adjusted"}),
                           ("insufficient_cash", {"clip", "reject"})):
        if ex[key] not in supported:
            reasons.append(f"execution:{key}={ex[key]}: unsupported")
    required |= {ex["timing"], ex["price_adjustment"], "insufficient_" + ex["insufficient_cash"]}
    if case.data["cashflows"]:
        required.add("monthly_contributions")
    if any(D(f["amount"]) < 0 for f in case.data["cashflows"]):
        required.add("withdrawals")
    if case.fees.fixed:
        required.add("fixed_fee")
    if case.fees.rate:
        required.add("proportional_fee")
    if case.data["initial_positions"]:
        required.add("initial_positions")
    if D(ex["slippage"]) != 0:
        required.add("slippage")
    if "volume_limit" in ex:
        required.add("volume_partial")
    reasons += [f"capability:{x}: unsupported by {runtime.name}" for x in sorted(required - runtime.capabilities)]
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


def prepare(intent_id, intent, price, available, holding, fees, policy):
    amount = D(intent.amount)
    if amount <= 0:
        return None, "nonpositive_amount"
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
    if amount > holding:
        return None, "oversell"
    quantity = (amount / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
    if quantity <= 0:
        return None, "quantity_below_lot"
    fee = fees.calculate(quantity, price)
    if quantity * price < fee:
        return None, "fee_exceeds_proceeds"
    return PreparedOrder(intent_id, intent, quantity, price, fee, ZERO, False), None
