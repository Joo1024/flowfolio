"""Small standard-library CLI. Diagnostics are data, never silent fallbacks."""
import argparse
from pathlib import Path
import sys
from .model import RunResult, Scenario, canonical, VERSION
from .package import StrategyPackage
from .sdk import load_engine, run
from .protocol import compatibility, RESERVED
from .verify import compare, verify


def parameters(items):
    output = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or key in output:
            raise ValueError("--param requires distinct NAME=VALUE entries")
        output[key] = value
    return output


def parser():
    p = argparse.ArgumentParser(prog="flowfolio", description="Portable recurring-cash-flow allocation backtests")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities", help="show implemented and reserved capabilities")
    for name in ("run", "check", "compare"):
        command = sub.add_parser(name)
        command.add_argument("--strategy", required=True, type=Path)
        command.add_argument("--scenario", required=True, type=Path)
        command.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
        if name == "compare":
            command.add_argument("--engines", nargs=2, default=["reference", "backtrader"])
        else:
            command.add_argument("--engine", default="reference")
        if name != "check":
            command.add_argument("--output", type=Path, help="new output directory (never overwrite)")
    command = sub.add_parser("verify", help="independently verify a saved ledger")
    command.add_argument("--result", required=True, type=Path)
    command.add_argument("--scenario", required=True, type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "capabilities":
            capabilities = {name: sorted(load_engine(name).capabilities)
                            for name in ("reference", "backtrader")}
            print(canonical({**capabilities, "unsupported": sorted(RESERVED)}))
            return 0
        scenario = Scenario.load(args.scenario)
        if args.command == "verify":
            result = RunResult.load(args.result)
            findings = verify(result, scenario)
            print(canonical({"valid": not findings, "diagnostics": findings}))
            return 1 if findings else 0
        package = StrategyPackage.load(args.strategy)
        params = parameters(args.param)
        if args.command == "check":
            package.parameters(params)
            engine = load_engine(args.engine)
            reasons = compatibility(package, scenario, engine)
            print(canonical({"status": "unsupported" if reasons else "compatible", "reasons": reasons}))
            return 2 if reasons else 0
        if args.command == "compare":
            a, b = [run(package, scenario, engine, params) for engine in args.engines]
            differences = compare(a, b)
            completed = all(x.manifest["status"] == "completed" for x in (a, b))
            report = {"conformant": completed and not differences, "engines": args.engines,
                      "statuses": [a.manifest["status"], b.manifest["status"]], "differences": differences}
            if args.output:
                args.output.mkdir(parents=True, exist_ok=False)
                a.write(args.output / "left")
                b.write(args.output / "right")
                (args.output / "comparison.json").write_text(canonical(report) + "\n", encoding="utf-8")
            print(canonical(report))
            return 0 if report["conformant"] else 1
        result = run(package, scenario, args.engine, params)
        if args.output:
            result.write(args.output)
            print(canonical({"status": result.manifest["status"], "output": str(args.output),
                             "diagnostics": result.diagnostics}))
        else:
            print(result.to_json(), end="")
        return {"completed": 0, "unsupported": 2, "invalid": 1}[result.manifest["status"]]
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
        print(canonical({"error": type(error).__name__, "message": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
