from pathlib import Path
from flowfolio import run, D, EvaluationSpec, evaluate

ROOT = Path(__file__).resolve().parents[3]


def test_reserve_flat_cash_labels():
    result = run(ROOT / "strategies/reserve_drawdown", ROOT / "cases/flat.json")
    assert result.manifest["status"] == "completed"
    assert result.valuations[-1]["budgets"]["reserve"] == D(75)
    metrics = evaluate(result, EvaluationSpec()).metrics
    assert metrics["final_value"] == D(300)
    assert len(result.transfers) == 3
