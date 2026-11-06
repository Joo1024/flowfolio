"""Portable, content-addressed strategy packages and independent cases."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import date
from collections.abc import Mapping
import hashlib
import importlib.util
import json

from .model import Bar, D, FeeModel, PROTOCOL, ZERO, digest, freeze


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


@dataclass(frozen=True)
class ExperimentCase:
    data: Mapping
    bars: tuple[Bar, ...]
    fees: FeeModel

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data):
        keys(data, {"protocol", "name", "currency", "requires", "initial_cash", "initial_positions", "fees", "execution", "bars", "cashflows", "evaluation"},
             {"protocol", "name", "currency", "initial_cash", "initial_positions", "fees", "execution", "bars", "cashflows"})
        if not isinstance(data["currency"], str) or not data["currency"]:
            raise ValueError("currency must be a single nonempty code")
        if not isinstance(data["name"], str) or not data["name"]:
            raise ValueError("case name is required")
        requirements(data.get("requires", []))
        number(data["initial_cash"])
        keys(data["fees"], {"fixed", "rate"}, {"fixed", "rate"})
        fees = FeeModel(number(data["fees"]["fixed"]), number(data["fees"]["rate"], maximum="1"))
        keys(data["execution"], {"timing", "price_adjustment", "insufficient_cash", "slippage", "volume_limit"}, {"timing", "price_adjustment", "insufficient_cash", "slippage"})
        number(data["execution"]["slippage"])
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
        for symbol, qty in data["initial_positions"].items():
            if symbol not in bars[0].close:
                raise ValueError(f"unknown initial symbol {symbol}")
            number(qty)
        initial_value = D(data["initial_cash"]) + sum(D(q) * bars[0].close[s] for s, q in data["initial_positions"].items())
        if initial_value > D("1000000000"):
            raise ValueError("initial account value exceeds 1e9")
        months = set()
        days = {b.date for b in bars[1:]}
        for flow in data["cashflows"]:
            keys(flow, {"date", "amount"}, {"date", "amount"})
            date.fromisoformat(flow["date"])
            number(flow["amount"], nonnegative=False)
            if flow["date"] not in days or flow["date"][:7] in months:
                raise ValueError("cashflows require an execution date and at most one contribution per month")
            months.add(flow["date"][:7])
        evaluation = data.get("evaluation", {})
        keys(evaluation, {"expected_metrics"})
        allowed_metrics = {"final_value", "profit", "contributions", "total_fees", "time_weighted_return", "max_drawdown"}
        keys(evaluation.get("expected_metrics", {}), allowed_metrics)
        for value in evaluation.get("expected_metrics", {}).values():
            number(value, nonnegative=False, precision=20, maximum="1000000000000")
        return cls(freeze(data), tuple(bars), fees)

    @property
    def fingerprint(self):
        return digest(self.data)


def requirements(value):
    if not isinstance(value, (list, tuple)) or not all(isinstance(x, str) and x for x in value):
        raise ValueError("requires must be a list of capability names")
    return frozenset(value)


@dataclass(frozen=True)
class StrategyPackage:
    path: Path
    metadata: Mapping
    fingerprint: str

    @classmethod
    def load(cls, path):
        root = Path(path).resolve()
        data = json.loads((root / "strategy.json").read_text(encoding="utf-8"))
        keys(data, {"protocol", "name", "version", "entrypoint", "requires", "parameters", "description"},
             {"protocol", "name", "version", "entrypoint", "requires", "parameters", "description"})
        if data["entrypoint"] != "strategy.py:Strategy":
            raise ValueError("protocol 1.0 entrypoint must be strategy.py:Strategy")
        requirements(data["requires"])
        for key in ("name", "version", "description"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError(f"package {key} is required")
        for name, spec in data["parameters"].items():
            keys(spec, {"type", "default", "minimum", "maximum", "description"}, {"type", "default", "description"})
            if spec["type"] not in {"decimal", "string"}:
                raise ValueError(f"unknown parameter type: {name}")
        for required in ("strategy.py", "README.md", "tests"):
            if not (root / required).exists():
                raise ValueError(f"missing package artifact {required}")
        if not any((root / "tests").glob("test_*.py")):
            raise ValueError("package must include test_*.py cases")
        items = []
        for file in sorted(root.rglob("*")):
            if file.is_symlink():
                raise ValueError("package symlinks are not supported")
            if file.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in file.relative_to(root).parts):
                items.append((file.relative_to(root).as_posix(), hashlib.sha256(file.read_bytes()).hexdigest()))
        obj = cls(root, freeze(data), digest(items))
        obj.parameters({})
        return obj

    def parameters(self, overrides):
        keys(overrides, self.metadata["parameters"])
        output = {}
        for name, spec in self.metadata["parameters"].items():
            value = overrides.get(name, spec["default"])
            if spec["type"] == "decimal":
                value = D(value)
                if ("minimum" in spec and value < D(spec["minimum"])) or ("maximum" in spec and value > D(spec["maximum"])):
                    raise ValueError(f"parameter {name} out of range")
            elif not isinstance(value, str) or not value:
                raise ValueError(f"parameter {name} must be a nonempty string")
            output[name] = value
        return freeze(output)

    def instantiate(self, parameters):
        # Check that the package did not change between preflight and import.
        if self.load(self.path).fingerprint != self.fingerprint:
            raise ValueError("strategy package changed after loading")
        spec = importlib.util.spec_from_file_location(f"flowfolio_strategy_{self.fingerprint}", self.path / "strategy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Strategy(parameters)
