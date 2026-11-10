import json
import sys

import pytest

from flowfolio import Scenario, run, engines
from flowfolio.cli import main
from flowfolio.sdk import load_engine


@pytest.fixture
def engine_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "__path__", [str(tmp_path), *engines.__path__])
    return tmp_path


def test_engine_is_discovered_by_module_name(root, engine_modules, capsys):
    (engine_modules / "new_engine.py").write_text(
        "from .reference import ReferenceExecutionEngine\n"
        "class Example(ReferenceExecutionEngine):\n"
        "    name = 'new_engine'\n"
    )
    (engine_modules / "unused_engine.py").write_text("raise AssertionError('must not import')\n")
    strategy = root / "strategies/dca"
    path = root / "cases/hand_fees.json"

    result = run(strategy, Scenario.load(path), engine="new_engine")
    assert result.manifest["execution_engine"]["name"] == "new_engine"
    assert result.manifest["status"] == "completed"

    assert main(["run", "--strategy", str(strategy), "--scenario", str(path),
                 "--engine", "new_engine"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["manifest"]["execution_engine"]["name"] == "new_engine"
    assert report == json.loads(result.to_json())
    assert "flowfolio.engines.unused_engine" not in sys.modules


@pytest.mark.parametrize(("name", "source", "expected"), [
    ("missing_dependency", "import flowfolio_missing_test_dependency",
     "cannot load engine.*flowfolio_missing_test_dependency"),
    ("abstract_engine", "from . import ExecutionEngine\nclass Abstract(ExecutionEngine): pass",
     "exactly one concrete ExecutionEngine subclass; found 0"),
    ("ambiguous_engine", "from .reference import ReferenceExecutionEngine\n"
     "class First(ReferenceExecutionEngine): pass\nclass Second(ReferenceExecutionEngine): pass",
     "exactly one concrete ExecutionEngine subclass; found 2"),
])
def test_invalid_engine_module_fails_without_plugin_fallback(
    engine_modules, monkeypatch, name, source, expected
):
    from flowfolio import sdk

    def unexpected_fallback(**kwargs):
        raise AssertionError("existing engine module must report its own load error")

    monkeypatch.setattr(sdk, "entry_points", unexpected_fallback)
    (engine_modules / f"{name}.py").write_text(source + "\n")
    with pytest.raises(ValueError, match=expected):
        load_engine(name)
