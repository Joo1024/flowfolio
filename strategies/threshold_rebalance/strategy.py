from flowfolio import Decision, OrderIntent, D


class Strategy:
    def __init__(self, parameters):
        if parameters["equity_symbol"] == parameters["bond_symbol"]:
            raise ValueError("rebalance requires two distinct symbols")
        self.p = parameters

    def decide(self, context):
        month = context.date[:7]
        if (context.state.get("month") == month and context.contribution == 0) or context.equity == 0:
            return Decision(state={"month": month})
        weights = {self.p["equity_symbol"]: self.p["equity_weight"],
                   self.p["bond_symbol"]: D(1) - self.p["equity_weight"]}
        differences = {}
        actions = []
        for symbol, weight in weights.items():
            value = context.positions.get(symbol, D(0)) * context.market.prices[symbol]
            differences[symbol] = context.equity * weight - value
            if value / context.equity - weight > self.p["threshold"]:
                quantity = -differences[symbol] / context.market.prices[symbol]
                actions.append(OrderIntent(symbol, "sell", quantity, "Trim overweight allocation", "rebalance.trim"))
        # Proceeds of sells arrive after this batch; only existing cash is allocated.
        deficits = {s: value for s, value in differences.items() if value > 0}
        total = sum(deficits.values(), D(0))
        cash = min(context.budgets["invest"], total)
        if cash > 0:
            remaining = cash
            for index, (symbol, value) in enumerate(deficits.items()):
                budget = remaining if index == len(deficits) - 1 else cash * value / total
                remaining -= budget
                actions.append(OrderIntent(symbol, "buy", budget, "Fund underweight allocation", "rebalance.fund"))
        return Decision(tuple(actions), {"month": month})
