import json
from flowfolio.cli import main


def test_cli_run_compare_verify(root, tmp_path, capsys):
    package = str(root / "strategies/dca")
    case = str(root / "cases/hand_fees.json")
    output = tmp_path / "run"
    assert main(["run", "--strategy", package, "--case", case, "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["final_value"] == "300.00000000"
    assert main(["verify", "--result", str(output), "--case", case]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert main(["run", "--strategy", package, "--case", case, "--output", str(output)]) == 2
    assert "FileExistsError" in capsys.readouterr().err


def test_cli_compare(root, tmp_path, capsys):
    import pytest
    pytest.importorskip("backtrader")
    output = tmp_path / "comparison"
    assert main(["compare", "--strategy", str(root / "strategies/threshold_rebalance"),
                 "--case", str(root / "cases/allocation_cycle.json"), "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["conformant"]
    assert (output / "left/fills.json").is_file() and (output / "right/fills.json").is_file()


def test_cli_check_and_invalid_parameter(root, capsys):
    args = ["--strategy", str(root / "strategies/dca"), "--case", str(root / "cases/flat.json")]
    assert main(["check", *args]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "compatible"
    assert main(["run", *args, "--param", "unknown=1"]) == 2
    assert "unknown" in capsys.readouterr().err


def test_cli_capabilities(capsys):
    assert main(["capabilities"]) == 0
    assert "withdrawals" in json.loads(capsys.readouterr().out)["unsupported"]
