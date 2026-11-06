"""Small standard-library CLI. Diagnostics are data, never silent fallbacks."""
import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys
from .model import RunResult, canonical, VERSION
from .package import StrategyPackage, ExperimentCase
from .sdk import run, runtime_by_name
from .semantics import compatibility, CAPABILITIES, RESERVED
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
        command.add_argument("--case", required=True, type=Path)
        command.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
        if name == "compare":
            command.add_argument("--runtimes", nargs=2, default=["reference", "backtrader"])
        else:
            command.add_argument("--runtime", default="reference")
        if name != "check":
            command.add_argument("--output", type=Path, help="new output directory (never overwrite)")
    command = sub.add_parser("verify", help="independently verify a saved ledger")
    command.add_argument("--result", required=True, type=Path)
    command.add_argument("--case", required=True, type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "capabilities":
            print(canonical({"reference": sorted(CAPABILITIES), "backtrader": sorted(CAPABILITIES), "unsupported": sorted(RESERVED)}))
            return 0
        case = ExperimentCase.load(args.case)
        if args.command == "verify":
            result = RunResult(**{f.name: json.loads((args.result / f"{f.name}.json").read_text(encoding="utf-8")) for f in fields(RunResult)})
            findings = verify(result, case)
            print(canonical({"valid": not findings, "diagnostics": findings}))
            return 1 if findings else 0
        package = StrategyPackage.load(args.strategy)
        params = parameters(args.param)
        if args.command == "check":
            package.parameters(params)
            backend = runtime_by_name(args.runtime)
            reasons = compatibility(package, case, backend)
            if hasattr(backend, "availability"):
                reasons += backend.availability()
            print(canonical({"status": "unsupported" if reasons else "compatible", "reasons": reasons}))
            return 2 if reasons else 0
        if args.command == "compare":
            a, b = [run(package, case, runtime, params) for runtime in args.runtimes]
            differences = compare(a, b)
            completed = all(x.manifest["status"] == "completed" for x in (a, b))
            report = {"conformant": completed and not differences, "runtimes": args.runtimes,
                      "statuses": [a.manifest["status"], b.manifest["status"]], "differences": differences}
            if args.output:
                args.output.mkdir(parents=True, exist_ok=False)
                a.write(args.output / "left")
                b.write(args.output / "right")
                (args.output / "comparison.json").write_text(canonical(report) + "\n", encoding="utf-8")
            print(canonical(report))
            return 0 if report["conformant"] else 1
        result = run(package, case, args.runtime, params)
        if args.output:
            result.write(args.output)
            print(canonical({"status": result.manifest["status"], "output": str(args.output), "metrics": result.metrics, "diagnostics": result.diagnostics}))
        else:
            print(result.to_json(), end="")
        return {"completed": 0, "unsupported": 2, "invalid": 1}[result.manifest["status"]]
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ArithmeticError) as error:
        print(canonical({"error": type(error).__name__, "message": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
