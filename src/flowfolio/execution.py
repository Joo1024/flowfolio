"""Protocol orchestration: snapshots, intent normalization, result recording.

This layer never executes a trade or owns the account. Each runtime supplies
its native cash, positions, fills and valuations.
"""
from .model import (Context, Decision, MarketView, OrderIntent, TransferIntent,
                    D, ZERO, freeze, plain, TOLERANCE)
from .semantics import prepare


class Session:
    def __init__(self, package, case, parameters, result):
        self.case, self.result = case, result
        self.strategy = package.instantiate(parameters)
        self.budgets = {"invest": D(case.data["initial_cash"]), "reserve": ZERO}
        self.state = freeze({})
        self.feedback = ()
        self.pending = {}
        self.current_feedback = []
        self.index = 0
        self.contribution = ZERO
        self.wealth = D(1)
        self.peak = D(1)
        self.drawdown = ZERO

    def initial(self, cash, positions, equity):
        self.record_value(0, cash, positions, equity)

    def flow(self, index):
        day = self.case.bars[index].date
        return sum((D(x["amount"]) for x in self.case.data["cashflows"] if x["date"] == day), ZERO)

    def open(self, index, cash, positions):
        self.index = index
        self.current_feedback = []
        self.pending = {}
        self.contribution = self.flow(index)
        day = self.case.bars[index].date
        if self.contribution:
            self.budgets["invest"] += self.contribution
            self.result.cashflows.append({"date": day, "amount": self.contribution, "currency": self.case.data["currency"]})
        market = MarketView(self.case.bars[:index])
        context = Context(day, market, freeze(positions), cash, freeze(self.budgets),
                          self.feedback, self.state, self.contribution,
                          cash + sum((q * market.prices[s] for s, q in positions.items()), ZERO))
        decision = self.strategy.decide(context)
        if not isinstance(decision, Decision):
            raise TypeError("strategy must return Decision")
        self.state = freeze(decision.state)
        remaining = dict(self.budgets)
        holdings = dict(positions)
        orders = []
        for sequence, intent in enumerate(decision.intents):
            if not isinstance(intent, (OrderIntent, TransferIntent)):
                raise TypeError("decision contains a non-protocol intent")
            intent_id = f"{day}:{sequence:04d}"
            row = {"date": day, "intent_id": intent_id, "kind": "order" if isinstance(intent, OrderIntent) else "transfer", **plain(intent)}
            self.result.intents.append(row)
            error = None
            try:
                amount = D(intent.amount)
            except Exception:
                amount, error = ZERO, "invalid_amount"
            if not intent.reason or not intent.rule_id:
                error = "missing_provenance"
            if amount <= 0:
                error = error or "nonpositive_amount"
            if isinstance(intent, TransferIntent):
                if intent.source not in remaining or intent.target not in remaining or intent.source == intent.target:
                    error = error or "invalid_budget_transfer"
                elif amount > remaining[intent.source]:
                    error = error or "insufficient_budget"
                if error is None:
                    for ledger in (remaining, self.budgets):
                        ledger[intent.source] -= amount
                        ledger[intent.target] += amount
                feedback = {**row, "status": "rejected" if error else "transferred", "rejection": error}
                self.result.transfers.append(feedback)
                self.current_feedback.append(freeze(feedback))
                continue
            if intent.symbol not in self.case.bars[index].open:
                error = error or "unknown_symbol"
            if intent.budget not in remaining:
                error = error or "unknown_budget"
            order = None
            if error is None:
                order, error = prepare(intent_id, intent, self.case.bars[index].open[intent.symbol],
                                       remaining[intent.budget], holdings.get(intent.symbol, ZERO),
                                       self.case.fees, self.case.data["execution"]["insufficient_cash"])
            if error:
                self.reject(intent_id, intent, error)
                continue
            if intent.side == "buy":
                remaining[intent.budget] -= order.quantity * order.price + order.fee
            else:
                holdings[intent.symbol] -= order.quantity
            self.pending[intent_id] = order
            orders.append(order)
        return orders

    def reject(self, intent_id, intent, error):
        row = {"date": self.case.bars[self.index].date, "intent_id": intent_id,
               "symbol": intent.symbol, "side": intent.side, "budget": intent.budget,
               "requested": D(intent.amount), "quantity": ZERO, "price": None, "notional": ZERO,
               "fee": ZERO, "cash_delta": ZERO, "residual": D(intent.amount),
               "status": "rejected", "rejection": error, "reason": intent.reason, "rule_id": intent.rule_id}
        self.result.fills.append(row)
        self.current_feedback.append(freeze(row))

    def fill(self, intent_id, quantity, price, fee):
        order = self.pending.pop(intent_id)
        intent = order.intent
        notional = quantity * price
        delta = -notional - fee if intent.side == "buy" else notional - fee
        self.budgets[intent.budget] += delta
        row = {"date": self.case.bars[self.index].date, "intent_id": intent_id,
               "symbol": intent.symbol, "side": intent.side, "budget": intent.budget,
               "requested": D(intent.amount), "quantity": quantity, "price": price,
               "notional": notional, "fee": fee, "cash_delta": delta,
               "residual": D(intent.amount) + delta if intent.side == "buy" else D(intent.amount) - quantity,
               "status": "partial" if order.partial else "filled", "rejection": None,
               "reason": intent.reason, "rule_id": intent.rule_id}
        self.result.fills.append(row)
        self.current_feedback.append(freeze(row))

    def native_reject(self, intent_id, reason):
        order = self.pending.pop(intent_id)
        self.reject(intent_id, order.intent, "native:" + reason)
        self.result.diagnostics.append({"layer": "execution", "path": intent_id,
            "message": "native runtime rejected a protocol-affordable order: " + reason})

    def close(self, cash, positions, equity):
        if self.pending:
            raise RuntimeError("runtime left orders without final feedback")
        # Reservation feedback may be recorded before native fills: restore intent order.
        self.result.fills.sort(key=lambda x: x["intent_id"])
        self.feedback = tuple(sorted(self.current_feedback, key=lambda x: x["intent_id"]))
        self.record_value(self.index, cash, positions, equity)

    def record_value(self, index, cash, positions, equity):
        if abs(sum(self.budgets.values(), ZERO) - cash) > TOLERANCE:
            raise RuntimeError("budget/native cash reconciliation failed")
        if cash < -TOLERANCE or any(q < -TOLERANCE for q in positions.values()):
            raise RuntimeError("native runtime violated long-only cash account")
        if equity > D("1000000000"):
            raise RuntimeError("account value exceeds protocol 1.0 numeric range")
        ret = ZERO
        if self.result.valuations:
            denominator = self.result.valuations[-1]["equity"] + self.contribution
            if denominator == 0 and equity != 0:
                raise RuntimeError("nonzero equity without invested capital")
            ret = equity / denominator - 1 if denominator else ZERO
            self.wealth *= 1 + ret
            self.peak = max(self.peak, self.wealth)
            self.drawdown = max(self.drawdown, 1 - self.wealth / self.peak)
        self.result.valuations.append({"date": self.case.bars[index].date, "cash": cash,
            "positions": dict(positions), "budgets": dict(self.budgets), "equity": equity,
            "external_flow": self.contribution if index else ZERO, "return": ret, "unit_wealth": self.wealth})

    def finish(self):
        values = self.result.valuations
        flows = sum((x["amount"] for x in self.result.cashflows), ZERO)
        self.result.metrics.update({"final_value": values[-1]["equity"], "contributions": flows,
            "profit": values[-1]["equity"] - values[0]["equity"] - flows,
            "total_fees": sum((f["fee"] for f in self.result.fills), ZERO),
            "time_weighted_return": self.wealth - 1, "max_drawdown": self.drawdown})
