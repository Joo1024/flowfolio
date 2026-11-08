"""Simulator backed by Backtrader's native broker."""
from importlib.metadata import PackageNotFoundError, version

from ..adapters.backtrader import execute_backtrader
from ..model import VERSION
from . import Simulator


class BacktraderSimulator(Simulator):
    name = "backtrader"
    version = VERSION
    capabilities = frozenset({
        "single_account", "single_currency", "long_only", "fractional_shares",
        "monthly_contributions", "fixed_fee", "proportional_fee", "budget_transfers",
        "cash_budget_orders", "previous_close_next_open", "total_return_adjusted",
        "insufficient_reject", "insufficient_clip", "initial_positions", "deterministic",
    })

    @property
    def engine(self):
        try:
            installed = version("backtrader")
        except PackageNotFoundError:
            installed = "not-installed"
        return {"name": "backtrader", "version": installed}

    def availability(self):
        if self.engine["version"] != "1.9.78.123":
            return ["backtrader 1.9.78.123 is required; install flowfolio[backtrader]"]
        return []

    def execute(self, run):
        import backtrader

        execute_backtrader(backtrader, run)
