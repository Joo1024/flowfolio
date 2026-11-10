from inspect import isabstract

from flowfolio import Scenario, ScenarioGenerator, run


def test_generator_yields_scenario_for_run(root):
    class ExampleGenerator(ScenarioGenerator):
        def generate(self):
            yield Scenario.load(root / "cases/hand_fees.json")

    assert isabstract(ScenarioGenerator)
    scenario, = ExampleGenerator().generate()
    result = run(root / "strategies/dca", scenario, engine="reference")
    assert result.manifest["scenario"]["sha256"] == scenario.fingerprint
    assert result.manifest["status"] == "completed"
