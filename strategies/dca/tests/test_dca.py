from pathlib import Path
from flowfolio import run, D, EvaluationSpec, evaluate

ROOT = Path(__file__).resolve().parents[3]


def test_dca_hand_reference():
    result = run(ROOT / "strategies/dca", ROOT / "cases/hand_fees.json")
    assert result.manifest["status"] == "completed"
    assert [f["quantity"] for f in result.fills] == [D(10), D(5)]
    assert [f["fee"] for f in result.fills] == [D(2), D(2)]
    metrics = evaluate(result, EvaluationSpec()).metrics
    assert metrics["final_value"] == D(300)
    assert metrics["profit"] == D(96)
    assert abs(metrics["time_weighted_return"] - (D(100) / 102 * D(300) / 202 - 1)) < D("1e-20")
