"""Resolve inputs and execute a fresh Run."""
from importlib import import_module
from importlib.metadata import entry_points
from importlib.util import find_spec
from inspect import isabstract, isclass

from .model import Scenario
from .package import StrategyPackage
from .run import Run
from .simulators import Simulator


def load_simulator(name):
    module_name = f"{__package__}.simulators.{name}"
    if name.isidentifier() and find_spec(module_name) is not None:
        try:
            module = import_module(module_name)
            implementations = {
                cls for cls in vars(module).values()
                if isclass(cls) and cls.__module__ == module.__name__
                and cls is not Simulator and issubclass(cls, Simulator) and not isabstract(cls)
            }
            if len(implementations) != 1:
                raise ValueError(
                    f"{module_name} must define exactly one concrete Simulator subclass; "
                    f"found {len(implementations)}"
                )
            simulator_class, = implementations
            return simulator_class()
        except ImportError as error:
            raise ValueError(f"cannot load simulator {name!r} from {module_name}: {error}") from error

    matches = list(entry_points(group="flowfolio.simulators", name=name))
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous simulator: {name}")
    try:
        return matches[0].load()()
    except ImportError as error:
        raise ValueError(f"cannot load simulator plugin {name!r}: {error}") from error


def run(strategy, scenario, simulator="reference", parameters=None):
    package = strategy if isinstance(strategy, StrategyPackage) else StrategyPackage.load(strategy)
    scenario = scenario if isinstance(scenario, Scenario) else Scenario.load(scenario)
    if isinstance(simulator, str):
        simulator = load_simulator(simulator)
    return Run(scenario, package, parameters or {}, simulator).execute()
