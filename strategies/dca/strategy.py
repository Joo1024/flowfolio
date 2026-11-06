from flowfolio import Decision, OrderIntent


class Strategy:
    def __init__(self, parameters):
        self.symbol = parameters["symbol"]

    def decide(self, context):
        month = context.date[:7]
        if context.state.get("month") == month:
            return Decision(state=context.state)
        budget = context.budgets["invest"]
        orders = (OrderIntent(self.symbol, "buy", budget, "Monthly cash investment", "dca.monthly"),) if budget > 0 else ()
        return Decision(orders, {"month": month})
