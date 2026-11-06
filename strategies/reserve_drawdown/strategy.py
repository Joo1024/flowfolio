from flowfolio import Decision, OrderIntent, TransferIntent, D


class Strategy:
    def __init__(self, parameters):
        self.p = parameters

    def decide(self, context):
        symbol = self.p["symbol"]
        price = context.market.prices[symbol]
        peak = max(price, context.state.get("peak", price))
        month = context.date[:7]
        state = {"peak": peak, "month": month,
                 "deployment_month": context.state.get("deployment_month")}
        if context.state.get("month") == month and context.contribution == 0:
            return Decision(state=state)
        invest = context.budgets["invest"]
        saving = invest * self.p["reserve_fraction"]
        reserve = context.budgets["reserve"] + saving
        actions = []
        if saving > 0:
            actions.append(TransferIntent("invest", "reserve", saving, "Build cash reserve", "reserve.save"))
        if invest - saving > 0:
            actions.append(OrderIntent(symbol, "buy", invest - saving, "Monthly base investment", "reserve.base"))
        if state["deployment_month"] != month and D(1) - price / peak >= self.p["drawdown"]:
            deployment = reserve * self.p["deploy_fraction"]
            if deployment > 0:
                actions.append(TransferIntent("reserve", "invest", deployment, "Release reserve after drawdown", "reserve.release"))
                actions.append(OrderIntent(symbol, "buy", deployment, "Observed drawdown purchase", "reserve.drawdown"))
                state["deployment_month"] = month
        return Decision(tuple(actions), state)
