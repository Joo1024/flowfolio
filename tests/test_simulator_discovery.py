import json
import sys

import pytest

from flowfolio import Scenario, run, simulators
from flowfolio.cli import main
from flowfolio.sdk import load_simulator


@pytest.fixture
def simulator_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(simulators, "__path__", [str(tmp_path), *simulators.__path__])
    return tmp_path


def test_simulator_is_discovered_by_module_name(root, simulator_modules, capsys):
    (simulator_modules / "new_engine.py").write_text(
        "from .reference import ReferenceSimulator\n"
        "class Example(ReferenceSimulator):\n"
        "    name = 'new_engine'\n"
    )
    (simulator_modules / "unused_engine.py").write_text("raise AssertionError('must not import')\n")
    strategy = root / "strategies/dca"
    path = root / "cases/hand_fees.json"

    result = run(strategy, Scenario.load(path), simulator="new_engine")
    assert result.manifest["simulator"]["name"] == "new_engine"
    assert result.manifest["status"] == "completed"

    assert main(["run", "--strategy", str(strategy), "--scenario", str(path),
                 "--simulator", "new_engine"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["manifest"]["simulator"]["name"] == "new_engine"
    assert report == json.loads(result.to_json())
    assert "flowfolio.simulators.unused_engine" not in sys.modules


@pytest.mark.parametrize(("name", "source", "expected"), [
    ("missing_dependency", "import flowfolio_missing_test_dependency",
     "cannot load simulator.*flowfolio_missing_test_dependency"),
    ("abstract_engine", "from . import Simulator\nclass Abstract(Simulator): pass",
     "exactly one concrete Simulator subclass; found 0"),
    ("ambiguous_engine", "from .reference import ReferenceSimulator\n"
     "class First(ReferenceSimulator): pass\nclass Second(ReferenceSimulator): pass",
     "exactly one concrete Simulator subclass; found 2"),
])
def test_invalid_simulator_module_fails_without_plugin_fallback(
    simulator_modules, monkeypatch, name, source, expected
):
    from flowfolio import sdk

    def unexpected_fallback(**kwargs):
        raise AssertionError("existing simulator module must report its own load error")

    monkeypatch.setattr(sdk, "entry_points", unexpected_fallback)
    (simulator_modules / f"{name}.py").write_text(source + "\n")
    with pytest.raises(ValueError, match=expected):
        load_simulator(name)
