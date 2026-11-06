import json
from pathlib import Path
import pytest
from flowfolio import ExperimentCase

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def flat_data():
    return json.loads((ROOT / "cases/flat.json").read_text())


@pytest.fixture
def make_package(tmp_path):
    def make(body, requires=(), parameters=None):
        path = tmp_path / "strategy"
        path.mkdir(exist_ok=True)
        (path / "tests").mkdir(exist_ok=True)
        (path / "tests/test_contract.py").write_text("def test_contract(): pass\n")
        (path / "README.md").write_text("Test strategy package\n")
        (path / "strategy.json").write_text(json.dumps({
            "protocol": "1.0", "name": "test", "version": "1", "description": "Test",
            "entrypoint": "strategy.py:Strategy", "requires": list(requires), "parameters": parameters or {}}))
        (path / "strategy.py").write_text("from flowfolio import *\n\nclass Strategy:\n    def __init__(self, p): pass\n    def decide(self, c):\n" + "\n".join("        " + line for line in body.splitlines()) + "\n")
        return path
    return make


@pytest.fixture(params=["reference", "backtrader"])
def runtime(request):
    if request.param == "backtrader":
        pytest.importorskip("backtrader")
    return request.param


@pytest.fixture
def one_period(flat_data):
    flat_data["bars"] = flat_data["bars"][:2]
    flat_data["cashflows"] = flat_data["cashflows"][:1]
    flat_data["evaluation"] = {}
    return flat_data
