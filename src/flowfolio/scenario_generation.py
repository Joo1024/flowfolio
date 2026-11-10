"""Interface for producing scenarios independently of execution."""

from abc import ABC, abstractmethod
from collections.abc import Iterable

from .model import Scenario


class ScenarioGenerator(ABC):
    @abstractmethod
    def generate(self) -> Iterable[Scenario]:
        """Yield scenarios ready to pass to run()."""
