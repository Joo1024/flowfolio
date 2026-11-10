"""Backtrader adapter: Cerebro clock + native BackBroker execution/accounting.

Shared code supplies order requests and records notifications; ReferenceExecutionEngine
is never imported or invoked. No trades are replayed from another engine.
"""
from datetime import datetime
from decimal import Decimal
from ..model import D, Fill, LOT


class NativeDecimal(Decimal):
    """Decimal scalar interoperable with Backtrader's float constants.

    The native broker still performs every cash operation and margin check.
    Price feed lines remain native floats; CommissionInfo converts monetary
    operands at its public boundary. No rounding epsilon or extra cash is added.
    """
    def __new__(cls, value=0):
        return super().__new__(cls, str(value))

    def __add__(self, other):
        return NativeDecimal(Decimal(self) + D(other))

    __radd__ = __add__

    def __sub__(self, other):
        return NativeDecimal(Decimal(self) - D(other))

    def __rsub__(self, other):
        return NativeDecimal(D(other) - Decimal(self))

    def __mul__(self, other):
        return NativeDecimal(Decimal(self) * D(int(other) if isinstance(other, bool) else other))

    __rmul__ = __mul__

    def __truediv__(self, other):
        return NativeDecimal(Decimal(self) / D(other))

    def __rtruediv__(self, other):
        return NativeDecimal(D(other) / Decimal(self))

    def __neg__(self):
        return NativeDecimal(-Decimal(self))

    def __abs__(self):
        return NativeDecimal(abs(Decimal(self)))

    def __pos__(self):
        return NativeDecimal(+Decimal(self))


def execute_backtrader(bt, run):
    scenario = run.scenario

    class FixedData(bt.feed.DataBase):
        params = (("rows", None), ("symbol", None))

        def start(self):
            super().start()
            self.rows = iter(self.p.rows)

        def _load(self):
            try:
                row = next(self.rows)
            except StopIteration:
                return False
            op, cl = float(row.open[self.p.symbol]), float(row.close[self.p.symbol])
            self.lines.datetime[0] = bt.date2num(datetime.fromisoformat(row.date))
            self.lines.open[0], self.lines.close[0] = op, cl
            self.lines.high[0], self.lines.low[0] = max(op, cl), min(op, cl)
            self.lines.volume[0], self.lines.openinterest[0] = 0, 0
            return True

    class ProtocolCommission(bt.CommInfoBase):
        params = (("stocklike", True), ("commtype", bt.CommInfoBase.COMM_FIXED), ("percabs", True))

        def _getcommission(self, size, price, pseudoexec):
            # Backtrader calls this for affordability simulation AND execution.
            # Pure quotation here; only its broker debits the actual fee.
            return NativeDecimal(scenario.fees.calculate(D(abs(size)).quantize(LOT), D(price)))

        def getvaluesize(self, size, price):
            return NativeDecimal(D(size).quantize(LOT) * D(price))

        def getoperationcost(self, size, price):
            return self.getvaluesize(abs(size), price)

        def profitandloss(self, size, price, newprice):
            return NativeDecimal(D(size).quantize(LOT) * (D(newprice) - D(price)))

    class CashBroker(bt.brokers.BackBroker):
        def start(self):
            super().start()
            # Backtrader's unused fund-share analyzer divides by zero for a
            # truly empty initial account. Seed bookkeeping units, NOT cash.
            if self._fundshares == 0:
                self._fundshares = 1.0

        def add_cash(self, cash):
            if self._fundval == 0:
                self._fundval = self.p.fundstartval
                self._fundshares = 0.0
            super().add_cash(cash)

    class Bridge(bt.Strategy):
        def start(self):
            self.by_symbol = {data._name: data for data in self.datas}
            for symbol, quantity in scenario.initial_account.positions.items():
                price = float(scenario.bars[0].close[symbol])
                self.broker.positions[self.by_symbol[symbol]].update(NativeDecimal(quantity), price)

        def snapshot(self):
            # Native monetary account values are preserved. Quantities
            # normalize to the declared 1e-8 lot at the adapter boundary.
            positions = {s: D(self.getposition(data).size).quantize(LOT) for s, data in self.by_symbol.items()}
            return D(self.broker.getcash()), positions, D(self.broker.getvalue())

        def next_open(self):
            index = len(self.data) - 1
            if index == 0:
                return
            amount = run.flow(index)
            if amount:
                self.broker.add_cash(NativeDecimal(amount))
                # Pinned native method flushes Backtrader's deferred cash
                # additions before native submitted-order affordability checks.
                self.broker._get_value()
            cash, positions, _ = self.snapshot()
            for request in run.open(index, cash, positions):
                data = self.by_symbol[request.intent.symbol]
                submit = self.buy if request.intent.side == "buy" else self.sell
                submit(data=data, size=NativeDecimal(request.quantity), exectype=bt.Order.Market,
                       flowfolio_id=request.intent_id)

        def notify_order(self, order):
            if order.status == order.Completed:
                bits = list(order.executed.exbits)
                if len(bits) != 1:
                    raise RuntimeError("expected one native execution bit per market order")
                bit = bits[0]
                # Read the actual execution bit. Backtrader's aggregate
                # executed.price recomputes a weighted average even for one
                # fill, introducing an unnecessary float multiply/divide.
                run.record_fill(Fill(
                    intent_id=order.info.flowfolio_id,
                    quantity=D(abs(bit.size)).quantize(LOT),
                    price=D(bit.price),
                    fee=D(bit.comm),
                ))
            elif order.status in (order.Canceled, order.Margin, order.Rejected, order.Expired):
                run.native_reject(order.info.flowfolio_id, order.getstatusname())
            elif order.status == order.Partial:
                raise RuntimeError("unexpected native volume partial fill: outside declared capabilities")

        def next(self):
            if len(self.data) == 1:
                run.initial(*self.snapshot())
            else:
                run.close(*self.snapshot())

    cerebro = bt.Cerebro(stdstats=False, cheat_on_open=True)
    broker = CashBroker(cash=NativeDecimal(scenario.initial_account.cash), coo=True)
    broker.addcommissioninfo(ProtocolCommission())
    cerebro.setbroker(broker)
    for symbol in scenario.bars[0].close:
        cerebro.adddata(FixedData(rows=scenario.bars, symbol=symbol), name=symbol)
    cerebro.addstrategy(Bridge)
    cerebro.run(runonce=False, preload=False)
