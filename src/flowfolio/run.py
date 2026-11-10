"""A single simulation and its mutable execution state."""
from decimal import localcontext
from platform import python_version

from . import protocol
from .model import (
    AccountState, D, DECIMAL_CONTEXT, Fill, OrderResponse, PROTOCOL, RunResult,
    Scenario, StrategyDecision, TransferIntent, TransferResponse, VERSION,
    ZERO, freeze, plain,
)
from .package import StrategyPackage
from .engines import ExecutionEngine
from .verify import verify


class Run:
    """One execution of a scenario and a configured strategy on an execution engine."""

    def __init__(self, scenario: Scenario, strategy: StrategyPackage, parameters, engine: ExecutionEngine):
        self._scenario = scenario
        self._parameters = strategy.parameters(parameters)
        self._engine = engine
        self._package = strategy
        self._phase = "new"
        self._index = 0
        self._contribution = ZERO
        self._budgets = dict(scenario.initial_account.budgets)
        self._state = freeze({})
        self._feedback = ()
        self._pending = {}
        self._current_feedback = []

    @property
    def scenario(self) -> Scenario:
        return self._scenario

    @property
    def parameters(self):
        return self._parameters

    @property
    def engine(self) -> ExecutionEngine:
        return self._engine

    def execute(self) -> RunResult:
        """Execute once and return the completed or unsupported result."""
        protocol.require_phase(self._phase, "new", "execute")
        self._phase = "initial"
        with localcontext(DECIMAL_CONTEXT):
            return self._execute()

    def _execute(self):
        scenario, package, execution_engine = self.scenario, self._package, self.engine
        reasons = protocol.compatibility(package, scenario, execution_engine)
        self._result = RunResult(manifest={
            "status": "unsupported" if reasons else "completed",
            "framework_version": VERSION,
            "protocol": PROTOCOL,
            "python": python_version(),
            "execution_engine": {"name": execution_engine.name, "version": execution_engine.version},
            "engine": plain(execution_engine.engine),
            "strategy": {
                "name": package.metadata["name"],
                "version": package.metadata["version"],
                "sha256": package.fingerprint,
            },
            "scenario": {"name": scenario.name, "sha256": scenario.fingerprint},
            "parameters": plain(self.parameters),
            "execution": plain(scenario.execution),
            "currency": scenario.currency,
            "fees": plain(scenario.fees),
            "capabilities": sorted(execution_engine.capabilities),
        })
        if reasons:
            self._result.diagnostics.extend(
                {"layer": "compatibility", "code": "unsupported", "message": reason}
                for reason in reasons
            )
        else:
            self._strategy = package.instantiate(self.parameters)
            execution_engine.execute(self)
            protocol.require_complete(self._phase, self._index, len(scenario.bars))
            self._result.diagnostics.extend(verify(self._result, scenario))
            if self._result.diagnostics:
                self._result.manifest["status"] = "invalid"
        self._phase = "finished"
        return self._result

    def initial(self, cash, positions, equity):
        protocol.require_phase(self._phase, "initial", "record initial valuation")
        self._record_value(0, cash, positions, equity)
        self._phase = "ready"

    def flow(self, index):
        protocol.require_phase(self._phase, "ready", "read cash flow")
        protocol.require_next_bar(index, self._index, len(self.scenario.bars))
        day = self.scenario.bars[index].date
        return sum((flow.amount for flow in self.scenario.cashflows if flow.date == day), ZERO)

    def open(self, index, cash, positions):
        contribution = self.flow(index)
        protocol.validate_account(cash, positions, sum(self._budgets.values(), ZERO) + contribution)
        self._phase = "open"
        self._index = index
        self._current_feedback = []
        self._contribution = contribution
        day = self.scenario.bars[index].date
        if self._contribution:
            self._budgets["invest"] += self._contribution
            self._result.cashflows.append({"date": day, "amount": self._contribution, "currency": self.scenario.currency})
        account = AccountState(cash, positions, self._budgets)
        context = protocol.decision_context(
            self.scenario, index, account, self._feedback, self._state, contribution
        )
        decision = self._strategy.decide(context)
        if not isinstance(decision, StrategyDecision):
            raise TypeError("strategy must return StrategyDecision")
        self._state = freeze(decision.state)
        intent_ids = []
        self._result.decisions.append({
            "date": day, "state": plain(self._state), "intent_ids": intent_ids,
        })
        remaining = dict(self._budgets)
        holdings = dict(positions)
        prices = self.scenario.bars[index].open
        policy = self.scenario.execution["insufficient_cash"]
        orders = []
        for sequence, intent in enumerate(decision.intents):
            intent_id = f"{day}:{sequence:04d}"
            order, error = protocol.prepare_request(
                intent_id, intent, prices, remaining, holdings, self.scenario.fees, policy
            )
            intent_ids.append(intent_id)
            is_transfer = isinstance(intent, TransferIntent)
            row = {"date": day, "intent_id": intent_id,
                   "kind": "transfer" if is_transfer else "order", **plain(intent)}
            self._result.intents.append(row)
            if is_transfer:
                if error is None:
                    amount = D(intent.amount)
                    for ledger in (remaining, self._budgets):
                        ledger[intent.source] -= amount
                        ledger[intent.target] += amount
                feedback: TransferResponse = {
                    **row, "status": "rejected" if error else "transferred", "rejection": error,
                }
                self._result.transfers.append(feedback)
                self._current_feedback.append(freeze(feedback))
                continue
            if error:
                self._reject(intent_id, intent, error)
                continue
            if intent.side == "buy":
                remaining[intent.budget] -= order.quantity * order.price + order.fee
            else:
                holdings[intent.symbol] -= order.quantity
            self._pending[intent_id] = order
            orders.append(order)
        return orders

    def _reject(self, intent_id, intent, error):
        row: OrderResponse = {"date": self.scenario.bars[self._index].date, "intent_id": intent_id,
               "symbol": intent.symbol, "side": intent.side, "budget": intent.budget,
               "requested": D(intent.amount), "quantity": ZERO, "price": None, "notional": ZERO,
               "fee": ZERO, "cash_delta": ZERO, "residual": D(intent.amount),
               "status": "rejected", "rejection": error, "reason": intent.reason, "rule_id": intent.rule_id}
        self._result.fills.append(row)
        self._current_feedback.append(freeze(row))

    def record_fill(self, fill: Fill):
        intent_id, quantity, price, fee = fill.intent_id, fill.quantity, fill.price, fill.fee
        order = protocol.feedback_order(self._phase, self._pending, intent_id)
        self._result.diagnostics.extend(protocol.fill_differences(fill, order))
        intent = order.intent
        notional = quantity * price
        delta = -notional - fee if intent.side == "buy" else notional - fee
        self._budgets[intent.budget] += delta
        row: OrderResponse = {"date": self.scenario.bars[self._index].date, "intent_id": intent_id,
               "symbol": intent.symbol, "side": intent.side, "budget": intent.budget,
               "requested": D(intent.amount), "quantity": quantity, "price": price,
               "notional": notional, "fee": fee, "cash_delta": delta,
               "residual": D(intent.amount) + delta if intent.side == "buy" else D(intent.amount) - quantity,
               "status": "partial" if order.partial else "filled", "rejection": None,
               "reason": intent.reason, "rule_id": intent.rule_id}
        self._result.fills.append(row)
        self._current_feedback.append(freeze(row))

    def native_reject(self, intent_id, reason):
        order = protocol.feedback_order(self._phase, self._pending, intent_id)
        self._reject(intent_id, order.intent, "native:" + reason)
        self._result.diagnostics.append({"layer": "execution", "path": intent_id,
            "message": "native engine rejected a protocol-affordable order: " + reason})

    def close(self, cash, positions, equity):
        protocol.require_phase(self._phase, "open", "record close valuation")
        if self._pending:
            raise RuntimeError("engine left orders without final feedback")
        # Reservation feedback may be recorded before native fills: restore intent order.
        self._result.fills.sort(key=lambda x: x["intent_id"])
        self._feedback = tuple(sorted(self._current_feedback, key=lambda x: x["intent_id"]))
        self._record_value(self._index, cash, positions, equity)
        self._phase = "ready"

    def _record_value(self, index, cash, positions, equity):
        protocol.validate_account(cash, positions, sum(self._budgets.values(), ZERO), equity)
        self._result.valuations.append({"date": self.scenario.bars[index].date, "cash": cash,
            "positions": dict(positions), "budgets": dict(self._budgets), "equity": equity,
            "external_flow": self._contribution if index else ZERO})
