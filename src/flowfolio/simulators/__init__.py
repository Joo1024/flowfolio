"""Simulator interface and built-in implementations."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..run import Run


class Simulator(ABC):
    name: str
    version: str
    capabilities: frozenset[str]
    engine: Mapping[str, str]

    @abstractmethod
    def execute(self, run: "Run") -> None:
        """Drive the run through native execution and valuation events."""
