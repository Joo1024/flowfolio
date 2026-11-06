"""Backend-neutral protocol types. No third-party imports."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol, Any
import hashlib
import json

VERSION = "0.1.1"
PROTOCOL = "1.0"
ZERO = Decimal(0)
LOT = Decimal("0.00000001")
TOLERANCE = Decimal("0.0000001")
RETURN_TOLERANCE = Decimal("0.0000000001")


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


@dataclass(frozen=True)
class Decision:
    intents: tuple[OrderIntent | TransferIntent, ...] = ()
    state: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    date: str
    market: MarketView
    positions: Mapping[str, Decimal]
    cash: Decimal
    budgets: Mapping[str, Decimal]
    feedback: tuple[Mapping, ...]
    state: Mapping
    contribution: Decimal
    equity: Decimal


class Strategy(Protocol):
    def decide(self, context: Context) -> Decision: ...


@dataclass(frozen=True)
class RunResult:
    manifest: dict
    cashflows: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    transfers: list = field(default_factory=list)
    valuations: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)

    def to_json(self) -> str:
        return canonical(self) + "\n"

    def write(self, directory):
        from pathlib import Path
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=False)
        for f in fields(self):
            (root / f"{f.name}.json").write_text(canonical(getattr(self, f.name)) + "\n", encoding="utf-8")


class BacktestRuntime(Protocol):
    name: str
    version: str
    capabilities: frozenset[str]
    engine: Mapping[str, str]

    def execute(self, session) -> None: ...


RuntimeAdapter = BacktestRuntime
