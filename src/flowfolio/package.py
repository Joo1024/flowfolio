"""Load and instantiate content-addressed strategy packages."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
import hashlib
import importlib.util
import json
import sys
from threading import RLock

from .model import D, digest, freeze, keys, requirements


_IMPORT_LOCK = RLock()


@dataclass(frozen=True)
class StrategyPackage:
    path: Path
    metadata: Mapping
    fingerprint: str
    _source: bytes = field(repr=False)

    @classmethod
    def load(cls, path):
        root = Path(path).resolve()
        # Capture each artifact once. Metadata and executed source must be the
        # very bytes included in the package digest, not later filesystem reads.
        artifacts = {}
        for file in sorted(root.rglob("*")):
            if file.is_symlink():
                raise ValueError("package symlinks are not supported")
            relative = file.relative_to(root)
            if file.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in relative.parts):
                artifacts[relative.as_posix()] = file.read_bytes()
        for required in ("strategy.json", "strategy.py", "README.md"):
            if required not in artifacts:
                raise ValueError(f"missing package artifact {required}")
        data = json.loads(artifacts["strategy.json"].decode("utf-8"))
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
        if not any(Path(name).parent == Path("tests") and Path(name).match("test_*.py") for name in artifacts):
            raise ValueError("package must include test_*.py cases")
        items = [(name, hashlib.sha256(content).hexdigest()) for name, content in artifacts.items()]
        obj = cls(root, freeze(data), digest(items), artifacts["strategy.py"])
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
        name = f"flowfolio_strategy_{self.fingerprint}"
        spec = importlib.util.spec_from_file_location(name, self.path / "strategy.py")
        # compile(bytes) handles source encoding and never reads/writes a pyc.
        # Disable inherited future flags and host optimization-level differences.
        code = compile(self._source, str(self.path / "strategy.py"), "exec", dont_inherit=True, optimize=0)
        with _IMPORT_LOCK:
            module = importlib.util.module_from_spec(spec)
            previous = sys.modules.get(name)
            sys.modules[name] = module
            try:
                exec(code, module.__dict__)
                return module.Strategy(parameters)
            except BaseException:
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous
                raise
