"""Run from any directory after installing flowfolio[backtrader]."""
from pathlib import Path
from flowfolio import EvaluationSpec, Scenario, evaluate, run
from flowfolio.model import canonical
from flowfolio.verify import compare

ROOT = Path(__file__).resolve().parents[1]

scenario = Scenario.load(ROOT / "cases/allocation_cycle.json")
for package in sorted((ROOT / "strategies").iterdir()):
    left = run(package, scenario, "reference")
    right = run(package, scenario, "backtrader")
    differences = compare(left, right)
    metrics = evaluate(left, EvaluationSpec()).metrics
    print(canonical({"strategy": package.name, "differences": differences, "metrics": metrics}))
    assert left.manifest["status"] == right.manifest["status"] == "completed"
    assert not differences
