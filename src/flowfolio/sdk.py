"""Resolve inputs and execute a fresh Run."""
from importlib import import_module
from importlib.metadata import entry_points
from importlib.util import find_spec
from inspect import isabstract, isclass

from .model import Scenario
from .package import StrategyPackage
from .run import Run
from .engines import ExecutionEngine


def load_engine(name):
    module_name = f"{__package__}.engines.{name}"
    if name.isidentifier() and find_spec(module_name) is not None:
        try:
            module = import_module(module_name)
            implementations = {
                cls for cls in vars(module).values()
                if isclass(cls) and cls.__module__ == module.__name__
                and cls is not ExecutionEngine and issubclass(cls, ExecutionEngine) and not isabstract(cls)
            }
            if len(implementations) != 1:
                raise ValueError(
                    f"{module_name} must define exactly one concrete ExecutionEngine subclass; "
                    f"found {len(implementations)}"
                )
            engine_class, = implementations
            return engine_class()
        except ImportError as error:
            raise ValueError(f"cannot load engine {name!r} from {module_name}: {error}") from error

    matches = list(entry_points(group="flowfolio.engines", name=name))
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous engine: {name}")
    try:
        return matches[0].load()()
    except ImportError as error:
        raise ValueError(f"cannot load engine plugin {name!r}: {error}") from error


def run(strategy, scenario, engine="reference", parameters=None):
    package = strategy if isinstance(strategy, StrategyPackage) else StrategyPackage.load(strategy)
    scenario = scenario if isinstance(scenario, Scenario) else Scenario.load(scenario)
    if isinstance(engine, str):
        engine = load_engine(engine)
    return Run(scenario, package, parameters or {}, engine).execute()
