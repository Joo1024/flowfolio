"""Run from any directory after installing flowfolio[backtrader]."""
from pathlib import Path
from flowfolio import run
from flowfolio.model import canonical
from flowfolio.verify import compare

ROOT = Path(__file__).resolve().parents[1]

for package in sorted((ROOT / "strategies").iterdir()):
    case = ROOT / "cases/allocation_cycle.json"
    left = run(package, case, "reference")
    right = run(package, case, "backtrader")
    differences = compare(left, right)
    print(canonical({"strategy": package.name, "differences": differences, "metrics": left.metrics}))
    assert left.manifest["status"] == right.manifest["status"] == "completed"
    assert not differences
