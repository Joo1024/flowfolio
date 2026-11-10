"""Small Decimal account, intentionally transparent enough to hand-check."""
from ..model import Fill, ZERO, VERSION
from ..protocol import CAPABILITIES
from . import ExecutionEngine


class ReferenceExecutionEngine(ExecutionEngine):
    name = "reference"
    version = VERSION
    capabilities = CAPABILITIES
    engine = {"name": "flowfolio-decimal", "version": VERSION}

    def execute(self, run):
        scenario = run.scenario
        cash = scenario.initial_account.cash
        positions = {s: scenario.initial_account.positions.get(s, ZERO) for s in scenario.bars[0].close}
        equity = cash + sum((q * scenario.bars[0].close[s] for s, q in positions.items()), ZERO)
        run.initial(cash, positions, equity)
        for index, bar in enumerate(scenario.bars[1:], 1):
            cash += run.flow(index)
            orders = run.open(index, cash, positions)
            for order in orders:
                # Native reference execution: debit/credit actual account here.
                intent, qty = order.intent, order.quantity
                price = bar.open[intent.symbol]
                fee = scenario.fees.calculate(qty, price)
                if intent.side == "buy":
                    cost = qty * price + fee
                    if cost > cash:
                        run.native_reject(order.intent_id, "insufficient_cash")
                        continue
                    cash -= cost
                    positions[intent.symbol] += qty
                else:
                    if qty > positions[intent.symbol]:
                        run.native_reject(order.intent_id, "oversell")
                        continue
                    cash += qty * price - fee
                    positions[intent.symbol] -= qty
                run.record_fill(Fill(order.intent_id, qty, price, fee))
            equity = cash + sum((q * bar.close[s] for s, q in positions.items()), ZERO)
            run.close(cash, positions, equity)
