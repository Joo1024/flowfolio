"""Engine-independent scenario, strategy, account and run data."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from decimal import Context as DecimalContext, Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, TypedDict
import hashlib
import json

VERSION = "0.1.1"
PROTOCOL = "1.0"
ZERO = Decimal(0)
LOT = Decimal("0.00000001")
TOLERANCE = Decimal("0.0000001")
RETURN_TOLERANCE = Decimal("0.0000000001")
DECIMAL_CONTEXT = DecimalContext(prec=40, rounding=ROUND_HALF_EVEN)
EVALUATION_METRICS = (
    "final_value", "contributions", "profit", "total_fees", "time_weighted_return", "max_drawdown",
)


def D(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("financial values must be finite")
    return result


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    if value is None or isinstance(value, (str, int, bool, Decimal)):
        return value
    raise ValueError(f"unsupported state value: {type(value).__name__}")


def plain(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [plain(v) for v in (sorted(value) if isinstance(value, (set, frozenset)) else value)]
    return value


def canonical(value) -> str:
    return json.dumps(plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def keys(obj, allowed, required=()):
    unknown = set(obj) - set(allowed)
    missing = set(required) - set(obj)
    if unknown or missing:
        raise ValueError(f"invalid schema: unknown={sorted(unknown)}, missing={sorted(missing)}")


def number(value, *, nonnegative=True, precision=8, maximum="1000000000"):
    if not isinstance(value, str):
        raise ValueError("financial input must be a decimal string")
    n = D(value)
    if (nonnegative and n < 0) or abs(n) > D(maximum) or n.as_tuple().exponent < -precision:
        raise ValueError(f"out-of-range financial value: {value}")
    return n


def requirements(value):
    if not isinstance(value, (list, tuple)) or not all(isinstance(x, str) and x for x in value):
        raise ValueError("requires must be a list of capability names")
    return frozenset(value)


@dataclass(frozen=True)
class FeeModel:
    fixed: Decimal = ZERO
    rate: Decimal = ZERO

    def calculate(self, quantity: Decimal, price: Decimal) -> Decimal:
        if quantity == 0:
            return ZERO
        return (self.fixed + self.rate * abs(quantity * price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Bar:
    date: str
    open: Mapping[str, Decimal]
    close: Mapping[str, Decimal]

    def __post_init__(self):
        if date.fromisoformat(self.date).isoformat() != self.date:
            raise ValueError("dates must use YYYY-MM-DD")
        object.__setattr__(self, "open", freeze(self.open))
        object.__setattr__(self, "close", freeze(self.close))


class FutureDataError(LookupError):
    pass


@dataclass(frozen=True)
class MarketView:
    bars: tuple[Bar, ...]

    def at(self, day: str) -> Bar:
        if day > self.bars[-1].date:
            raise FutureDataError(f"{day} is after observation {self.bars[-1].date}")
        for bar in self.bars:
            if bar.date == day:
                return bar
        raise KeyError(day)

    @property
    def prices(self) -> Mapping[str, Decimal]:
        return self.bars[-1].close


@dataclass(frozen=True)
class AccountState:
    cash: Decimal
    positions: Mapping[str, Decimal]
    budgets: Mapping[str, Decimal]

    def __post_init__(self):
        object.__setattr__(self, "positions", freeze(self.positions))
        object.__setattr__(self, "budgets", freeze(self.budgets))


@dataclass(frozen=True)
class CashFlow:
    date: str
    amount: Decimal


@dataclass(frozen=True)
class Scenario:
    """Normalized scenario inputs and the separate expectations used by verification."""

    protocol: str
    name: str
    currency: str
    requires: frozenset[str]
    initial_account: AccountState
    bars: tuple[Bar, ...]
    cashflows: tuple[CashFlow, ...]
    execution: Mapping[str, Any]
    fees: FeeModel
    fingerprint: str = field(compare=False)
    expected_metrics: Mapping[str, str] = field(compare=False, repr=False)

    @classmethod
    def load(cls, path) -> Scenario:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data) -> Scenario:
        with localcontext(DECIMAL_CONTEXT):
            return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data) -> Scenario:
        required = {"protocol", "name", "currency", "initial_cash", "initial_positions",
                    "fees", "execution", "bars", "cashflows"}
        keys(data, required | {"requires", "evaluation"}, required)
        if not isinstance(data["currency"], str) or not data["currency"]:
            raise ValueError("currency must be a single nonempty code")
        if not isinstance(data["name"], str) or not data["name"]:
            raise ValueError("scenario name is required")
        requires = requirements(data.get("requires", []))
        cash = number(data["initial_cash"])
        keys(data["fees"], {"fixed", "rate"}, {"fixed", "rate"})
        fees = FeeModel(number(data["fees"]["fixed"]), number(data["fees"]["rate"], maximum="1"))
        execution = data["execution"]
        execution_keys = {"timing", "price_adjustment", "insufficient_cash", "slippage"}
        keys(execution, execution_keys | {"volume_limit"}, execution_keys)
        number(execution["slippage"])
        bars = []
        for row in data["bars"]:
            keys(row, {"date", "open", "close"}, {"date", "open", "close"})
            bar = Bar(row["date"], {s: number(p, precision=4) for s, p in row["open"].items()},
                      {s: number(p, precision=4) for s, p in row["close"].items()})
            if not bar.open or set(bar.open) != set(bar.close) or any(p <= 0 for p in (*bar.open.values(), *bar.close.values())):
                raise ValueError("bars require matching symbols and strictly positive prices")
            if bars and (bar.date <= bars[-1].date or set(bar.open) != set(bars[0].open)):
                raise ValueError("bars must be strictly ordered and have synchronized symbols")
            bars.append(bar)
        if len(bars) < 2:
            raise ValueError("at least a warmup bar and one execution bar are required")
        positions = {}
        for symbol, qty in data["initial_positions"].items():
            if symbol not in bars[0].close:
                raise ValueError(f"unknown initial symbol {symbol}")
            positions[symbol] = number(qty)
        initial_value = cash + sum((q * bars[0].close[s] for s, q in positions.items()), ZERO)
        if initial_value > D("1000000000"):
            raise ValueError("initial account value exceeds 1e9")
        months = set()
        days = {bar.date for bar in bars[1:]}
        cashflows = []
        for flow in data["cashflows"]:
            keys(flow, {"date", "amount"}, {"date", "amount"})
            day = flow["date"]
            date.fromisoformat(day)
            amount = number(flow["amount"], nonnegative=False)
            if day not in days or day[:7] in months:
                raise ValueError("cashflows require an execution date and at most one contribution per month")
            months.add(day[:7])
            cashflows.append(CashFlow(day, amount))
        evaluation = data.get("evaluation", {})
        keys(evaluation, {"expected_metrics"})
        expected_metrics = evaluation.get("expected_metrics", {})
        keys(expected_metrics, EVALUATION_METRICS)
        for value in expected_metrics.values():
            number(value, nonnegative=False, precision=20, maximum="1000000000000")
        return cls(
            protocol=data["protocol"],
            name=data["name"],
            currency=data["currency"],
            requires=requires,
            initial_account=AccountState(cash, positions, {"invest": cash, "reserve": ZERO}),
            bars=tuple(bars),
            cashflows=tuple(cashflows),
            execution=freeze(execution),
            fees=fees,
            fingerprint=digest({key: value for key, value in data.items() if key != "evaluation"}),
            expected_metrics=freeze(expected_metrics),
        )


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    amount: Decimal
    reason: str
    rule_id: str
    budget: str = "invest"


@dataclass(frozen=True)
class TransferIntent:
    source: str
    target: str
    amount: Decimal
    reason: str
    rule_id: str


ActionRequest = OrderIntent | TransferIntent


@dataclass(frozen=True)
class Fill:
    """Actual native execution of a prepared order."""

    intent_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal


class OrderResponse(TypedDict):
    date: str
    intent_id: str
    symbol: str
    side: str
    budget: str
    requested: Decimal | str
    quantity: Decimal | str
    price: Decimal | str | None
    notional: Decimal | str
    fee: Decimal | str
    cash_delta: Decimal | str
    residual: Decimal | str
    status: str
    rejection: str | None
    reason: str
    rule_id: str


class TransferResponse(TypedDict):
    date: str
    intent_id: str
    kind: str
    source: str
    target: str
    amount: Decimal | str
    status: str
    rejection: str | None
    reason: str
    rule_id: str


ActionResponse = OrderResponse | TransferResponse


@dataclass(frozen=True)
class StrategyDecision:
    intents: tuple[ActionRequest, ...] = ()
    state: Mapping = field(default_factory=dict)


class DecisionRecord(TypedDict):
    date: str
    state: dict
    intent_ids: list[str]


@dataclass(frozen=True)
class StrategyContext:
    date: str
    market: MarketView
    account: AccountState
    feedback: tuple[Mapping, ...]
    state: Mapping
    contribution: Decimal
    equity: Decimal


class Strategy(Protocol):
    def decide(self, context: StrategyContext) -> StrategyDecision: ...


@dataclass(frozen=True)
class RunResult:
    """Protocol 1.0 trajectory and verification diagnostics."""

    manifest: dict
    cashflows: list = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    intents: list = field(default_factory=list)
    fills: list[OrderResponse] = field(default_factory=list)
    transfers: list[TransferResponse] = field(default_factory=list)
    valuations: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)

    def to_json(self) -> str:
        return canonical(self) + "\n"

    def write(self, directory):
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=False)
        for f in fields(self):
            (root / f"{f.name}.json").write_text(canonical(getattr(self, f.name)) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, directory) -> RunResult:
        root = Path(directory)
        return cls(**{
            f.name: json.loads((root / f"{f.name}.json").read_text(encoding="utf-8"))
            for f in fields(cls)
        })
