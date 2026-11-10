from pathlib import Path
from flowfolio import run, D, EvaluationSpec, evaluate

ROOT = Path(__file__).resolve().parents[3]


def test_rebalance_flat_allocation():
    result = run(ROOT / "strategies/threshold_rebalance", ROOT / "cases/flat.json")
    assert result.manifest["status"] == "completed"
    assert result.valuations[-1]["positions"] == {"EQUITY": D(18), "BOND": D(12)}
    metrics = evaluate(result, EvaluationSpec()).metrics
    assert metrics["profit"] == 0
