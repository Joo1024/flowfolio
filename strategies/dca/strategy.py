from flowfolio import StrategyDecision, OrderIntent


class Strategy:
    def __init__(self, parameters):
        self.symbol = parameters["symbol"]

    def decide(self, context):
        month = context.date[:7]
        if context.state.get("month") == month and context.contribution == 0:
            return StrategyDecision(state=context.state)
        budget = context.account.budgets["invest"]
        orders = (OrderIntent(self.symbol, "buy", budget, "Monthly cash investment", "dca.monthly"),) if budget > 0 else ()
        return StrategyDecision(orders, {"month": month})
