import pytest

from flowfolio import Run, RunResult, Scenario, StrategyPackage, run
from flowfolio.engines.reference import ReferenceExecutionEngine
from flowfolio.verify import compare, verify


def test_decisions_include_state_only_updates_and_link_ordered_intents(make_package, one_period, tmp_path):
    package = make_package("""
if not c.state:
    return StrategyDecision(state={"period": 1})
return StrategyDecision((TransferIntent("invest", "reserve", D(25), "test", "transfer"),
                         OrderIntent("EQUITY", "buy", D(75), "test", "buy")), {"period": 2})
""".strip())
    one_period["bars"].append({**one_period["bars"][-1], "date": "2024-03-01"})
    scenario = Scenario.from_dict(one_period)
    result = run(package, scenario)
    result.write(tmp_path / "run")
    saved = RunResult.load(tmp_path / "run")

    assert saved.decisions == [
        {"date": "2024-02-01", "state": {"period": 1}, "intent_ids": []},
        {"date": "2024-03-01", "state": {"period": 2}, "intent_ids": [
            "2024-03-01:0000", "2024-03-01:0001",
        ]},
    ]
    assert verify(saved, scenario) == []
    saved.decisions[1]["intent_ids"].reverse()
    assert verify(saved, scenario)[0]["path"] == "decisions[1].intent_ids"
    assert compare(result, saved)[0]["layer"] == "strategy"


def test_repeated_configuration_creates_independent_runs(make_package, flat_data, monkeypatch):
    path = make_package("""
assert self.calls == c.state.get("calls", 0)
assert bool(c.feedback) == bool(self.calls)
self.calls += 1
return StrategyDecision((OrderIntent("EQUITY", "buy", c.contribution, "test", "buy"),),
                        {"calls": self.calls})
""".strip())
    source = path / "strategy.py"
    source.write_text(source.read_text().replace("def __init__(self, p): pass",
                                                 "def __init__(self, p): self.calls = 0"))
    strategy = StrategyPackage.load(path)
    scenario = Scenario.from_dict(flat_data)
    instances = []
    instantiate = StrategyPackage.instantiate

    def capture_instance(self, parameters):
        instance = instantiate(self, parameters)
        instances.append(instance)
        return instance

    monkeypatch.setattr(StrategyPackage, "instantiate", capture_instance)

    class RecordingExecutionEngine(ReferenceExecutionEngine):
        def __init__(self):
            self.runs = []

        def execute(self, run):
            self.runs.append(run)
            super().execute(run)

    engine = RecordingExecutionEngine()
    first = run(strategy, scenario, engine)
    second = run(strategy, scenario, engine)
    first_run, second_run = engine.runs
    first_strategy, second_strategy = instances
    assert isinstance(first_run, Run) and isinstance(second_run, Run)
    assert first_run is not second_run
    assert first_strategy is not second_strategy
    assert first_strategy.calls == second_strategy.calls == len(scenario.bars) - 1
    assert first.manifest["status"] == second.manifest["status"] == "completed"
    assert first.to_json() == second.to_json()
    assert first.fills is not second.fills
    assert first.valuations is not second.valuations
    with pytest.raises(RuntimeError, match="cannot execute"):
        first_run.execute()


@pytest.mark.parametrize("event", ["before_initial", "skip_bar", "no_feedback", "stop_early"])
def test_run_rejects_invalid_engine_event_order(root, flat_data, event):
    class BrokenExecutionEngine(ReferenceExecutionEngine):
        def execute(self, run):
            if event == "before_initial":
                run.open(1, 0, {})
            run.initial(0, {}, 0)
            if event == "skip_bar":
                run.open(2, 0, {})
            if event == "no_feedback":
                cash = run.flow(1)
                assert run.open(1, cash, {})
                run.close(cash, {}, cash)

    errors = {
        "before_initial": "expected ready",
        "skip_bar": "invalid execution bar",
        "no_feedback": "without final feedback",
        "stop_early": "before the final valuation",
    }
    scenario = Scenario.from_dict(flat_data)
    with pytest.raises(RuntimeError, match=errors[event]):
        run(root / "strategies/dca", scenario, BrokenExecutionEngine())
