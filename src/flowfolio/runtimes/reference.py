"""Small Decimal account, intentionally transparent enough to hand-check."""
from ..model import D, ZERO, VERSION
from ..semantics import CAPABILITIES


class ReferenceRuntime:
    name = "reference"
    version = VERSION
    capabilities = CAPABILITIES
    engine = {"name": "flowfolio-decimal", "version": VERSION}

    def execute(self, session):
        case = session.case
        cash = D(case.data["initial_cash"])
        positions = {s: D(case.data["initial_positions"].get(s, "0")) for s in case.bars[0].close}
        equity = cash + sum((q * case.bars[0].close[s] for s, q in positions.items()), ZERO)
        session.initial(cash, positions, equity)
        for index, bar in enumerate(case.bars[1:], 1):
            cash += session.flow(index)
            orders = session.open(index, cash, positions)
            for order in orders:
                # Native reference execution: debit/credit actual account here.
                intent, qty = order.intent, order.quantity
                price = bar.open[intent.symbol]
                fee = case.fees.calculate(qty, price)
                if intent.side == "buy":
                    cost = qty * price + fee
                    if cost > cash:
                        session.native_reject(order.intent_id, "insufficient_cash")
                        continue
                    cash -= cost
                    positions[intent.symbol] += qty
                else:
                    if qty > positions[intent.symbol]:
                        session.native_reject(order.intent_id, "oversell")
                        continue
                    cash += qty * price - fee
                    positions[intent.symbol] -= qty
                session.fill(order.intent_id, qty, price, fee)
            equity = cash + sum((q * bar.close[s] for s, q in positions.items()), ZERO)
            session.close(cash, positions, equity)
