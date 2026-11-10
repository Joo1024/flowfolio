import json

import pytest

from flowfolio import run
from flowfolio.cli import main


def test_cli_run_compare_verify(root, tmp_path, capsys):
    package = str(root / "strategies/dca")
    case = str(root / "cases/hand_fees.json")
    output = tmp_path / "run"
    assert main(["run", "--strategy", package, "--scenario", case, "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["scenario"]["name"] == "hand_fees"
    assert manifest["execution_engine"]["name"] == "reference"
    assert manifest["execution_engine"]["version"] == manifest["framework_version"]
    assert manifest["engine"] == {"name": "flowfolio-decimal", "version": manifest["framework_version"]}
    assert (output / "decisions.json").is_file()
    assert not (output / "metrics.json").exists()
    assert main(["verify", "--result", str(output), "--scenario", case]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert main(["run", "--strategy", package, "--scenario", case, "--output", str(output)]) == 2
    assert "FileExistsError" in capsys.readouterr().err


def test_cli_compare(root, tmp_path, capsys):
    pytest.importorskip("backtrader")
    output = tmp_path / "comparison"
    assert main(["compare", "--strategy", str(root / "strategies/threshold_rebalance"),
                 "--scenario", str(root / "cases/allocation_cycle.json"),
                 "--engines", "reference", "backtrader", "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["conformant"]
    assert report["engines"] == ["reference", "backtrader"]
    assert (output / "left/fills.json").is_file() and (output / "right/fills.json").is_file()


def test_cli_check_and_invalid_parameter(root, capsys):
    args = ["--strategy", str(root / "strategies/dca"), "--scenario", str(root / "cases/flat.json")]
    assert main(["check", *args]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "compatible"
    assert main(["run", *args, "--param", "unknown=1"]) == 2
    assert "unknown" in capsys.readouterr().err


def test_cli_capabilities(capsys):
    assert main(["capabilities"]) == 0
    assert "withdrawals" in json.loads(capsys.readouterr().out)["unsupported"]


@pytest.mark.parametrize("unavailable", ["engine_version", "fixed_fee"])
def test_backtrader_preflight(root, make_package, monkeypatch, capsys, unavailable):
    from flowfolio.engines import backtrader
    package = make_package("raise AssertionError('must not decide')")
    (package / "strategy.py").write_text("raise AssertionError('must not import')\n")
    case = root / "cases/hand_fees.json"

    if unavailable == "engine_version":
        monkeypatch.setattr(backtrader, "version", lambda _: "0")
        expected = "backtrader 1.9.78.123 is required"
    else:
        monkeypatch.setattr(backtrader, "version", lambda _: "1.9.78.123")
        engine = backtrader.BacktraderExecutionEngine
        monkeypatch.setattr(engine, "capabilities", engine.capabilities - {"fixed_fee"})
        expected = "capability:fixed_fee: unsupported by backtrader"

    args = ["--strategy", str(package), "--scenario", str(case), "--engine", "backtrader"]
    assert main(["check", *args]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "unsupported"
    assert expected in report["reasons"][0]

    result = run(package, case, "backtrader")
    assert result.manifest["status"] == "unsupported"
    assert expected in result.diagnostics[0]["message"]
    assert not result.valuations
