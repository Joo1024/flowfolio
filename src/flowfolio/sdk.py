from importlib.metadata import entry_points
import platform
from decimal import Context as DecimalContext, localcontext, ROUND_HALF_EVEN
from .model import RunResult, VERSION, PROTOCOL, plain
from .package import StrategyPackage, ExperimentCase
from .semantics import compatibility
from .execution import Session


def runtime_by_name(name):
    if name == "reference":
        from .runtimes.reference import ReferenceRuntime
        return ReferenceRuntime()
    if name == "backtrader":
        from .runtimes.backtrader import BacktraderRuntime
        return BacktraderRuntime()
    matches = list(entry_points(group="flowfolio.runtimes", name=name))
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous runtime: {name}")
    return matches[0].load()()


def run(strategy, case, runtime="reference", parameters=None):
    # Host applications may change their Decimal context; protocol arithmetic
    # must remain reproducible and must restore the caller's context afterward.
    with localcontext(DecimalContext(prec=40, rounding=ROUND_HALF_EVEN)):
        return _run(strategy, case, runtime, parameters)


def _run(strategy, case, runtime, parameters):
    package = strategy if isinstance(strategy, StrategyPackage) else StrategyPackage.load(strategy)
    case = case if isinstance(case, ExperimentCase) else ExperimentCase.load(case)
    backend = runtime_by_name(runtime) if isinstance(runtime, str) else runtime
    parameters = package.parameters(parameters or {})
    reasons = compatibility(package, case, backend)
    if hasattr(backend, "availability"):
        reasons += backend.availability()
    result = RunResult(manifest={"status": "unsupported" if reasons else "completed",
        "framework_version": VERSION, "protocol": PROTOCOL, "python": platform.python_version(),
        "runtime": {"name": backend.name, "version": backend.version}, "engine": backend.engine,
        "strategy": {"name": package.metadata["name"], "version": package.metadata["version"], "sha256": package.fingerprint},
        "case": {"name": case.data["name"], "sha256": case.fingerprint},
        "parameters": plain(parameters), "execution": plain(case.data["execution"]),
        "currency": case.data["currency"], "fees": plain(case.fees),
        "capabilities": sorted(backend.capabilities)})
    if reasons:
        result.diagnostics.extend({"layer": "compatibility", "code": "unsupported", "message": r} for r in reasons)
        return result
    session = Session(package, case, parameters, result)
    backend.execute(session)
    session.finish()
    from .verify import verify
    findings = verify(result, case)
    result.diagnostics.extend(findings)
    if result.diagnostics:
        result.manifest["status"] = "invalid"
    return result
