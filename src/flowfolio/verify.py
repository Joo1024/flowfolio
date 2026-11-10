"""Independent ledger reconstruction and cross-engine diagnostics."""
from decimal import InvalidOperation, ROUND_FLOOR, Context as DecimalContext, localcontext
from .evaluation import EvaluationSpec, evaluate
from .model import D, ZERO, LOT, TOLERANCE, RETURN_TOLERANCE, plain


def verify(result, scenario):
    with localcontext(DecimalContext(prec=40)):
        try:
            return _verify(result, scenario)
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            return [{"layer": "schema", "path": "result", "message": f"{type(error).__name__}: {error}"}]


def _intent_error(intent, available, sellable, prices, fees, policy):
    """Validate against the pre-execution reservations, independently of prepare.

    This is not a fill generator: the ledger below must still prove actual
    quantities, fee quotations, maximal affordability and account conservation.
    """
    amount = D(intent["amount"])
    error = "missing_provenance" if not intent["reason"] or not intent["rule_id"] else None
    if amount <= 0:
        error = error or "nonpositive_amount"
    if intent["kind"] == "transfer":
        source, target = intent["source"], intent["target"]
        if source not in available or target not in available or source == target:
            return error or "invalid_budget_transfer"
        return error or ("insufficient_budget" if amount > available[source] else None)
    symbol, budget, side = intent["symbol"], intent["budget"], intent["side"]
    if symbol not in prices:
        error = error or "unknown_symbol"
    if budget not in available:
        error = error or "unknown_budget"
    if error:
        return error
    price = prices[symbol]
    if side == "buy":
        if policy == "reject" and amount > available[budget]:
            return "insufficient_budget"
        if min(amount, available[budget]) < LOT * price + fees.calculate(LOT, price):
            return "budget_below_minimum_fill"
    elif side == "sell":
        if amount > sellable[symbol]:
            return "oversell"
        quantity = (amount / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
        if quantity == 0:
            return "quantity_below_lot"
        if quantity * price < fees.calculate(quantity, price):
            return "fee_exceeds_proceeds"
    else:
        return "invalid_side"
    return None


def _validate_execution_engine(manifest):
    execution_engine = manifest["execution_engine"]
    if not all(isinstance(execution_engine[field], str) and execution_engine[field]
               for field in ("name", "version")):
        raise ValueError("manifest.execution_engine requires a name and version")


def _verify(result, scenario):
    r = plain(result)
    findings = []
    _validate_execution_engine(r["manifest"])
    if r["manifest"]["scenario"]["sha256"] != scenario.fingerprint:
        findings.append({"layer": "inputs", "path": "manifest.scenario.sha256", "message": "scenario hash mismatch"})

    def check(path, actual, expected, tolerance=TOLERANCE, layer="accounting"):
        if abs(D(actual) - D(expected)) > tolerance:
            findings.append({"layer": layer, "path": path, "actual": str(actual), "expected": str(expected)})

    if r["manifest"]["status"] != "completed":
        return [{"layer": "compatibility", "path": "manifest.status", "actual": r["manifest"]["status"], "expected": "completed"}]
    for key, expected in (("execution", plain(scenario.execution)),
                          ("currency", scenario.currency), ("fees", plain(scenario.fees))):
        if r["manifest"][key] != expected:
            findings.append({"layer": "inputs", "path": "manifest." + key, "message": "manifest disagrees with the scenario"})
    cash = scenario.initial_account.cash
    holdings = {s: scenario.initial_account.positions.get(s, ZERO) for s in scenario.bars[0].close}
    budgets = dict(scenario.initial_account.budgets)
    fill_map = {f["intent_id"]: f for f in r["fills"]}
    transfer_map = {f["intent_id"]: f for f in r["transfers"]}
    intents_by_date = {bar.date: [] for bar in scenario.bars[1:]}
    for intent in r["intents"]:
        if intent["date"] in intents_by_date:
            intents_by_date[intent["date"]].append(intent)
    if [decision["date"] for decision in r["decisions"]] != list(intents_by_date):
        findings.append({"layer": "timing", "path": "decisions", "message": "decision dates do not match execution dates"})
    for index, decision in enumerate(r["decisions"]):
        expected_ids = [intent["intent_id"] for intent in intents_by_date.get(decision["date"], ())]
        if decision["intent_ids"] != expected_ids:
            findings.append({"layer": "timing", "path": f"decisions[{index}].intent_ids",
                             "message": "decision does not match its ordered intents"})
        if not isinstance(decision["state"], dict):
            findings.append({"layer": "schema", "path": f"decisions[{index}].state",
                             "message": "decision state must be an object"})
    for section in ("intents", "fills", "transfers"):
        ids = [x["intent_id"] for x in r[section]]
        if len(ids) != len(set(ids)):
            findings.append({"layer": "execution", "path": section, "message": "duplicate intent_id"})
        if any(x["date"] not in intents_by_date for x in r[section]):
            findings.append({"layer": "timing", "path": section, "message": "action outside execution dates"})
    if set(fill_map) | set(transfer_map) != {x["intent_id"] for x in r["intents"]} or set(fill_map) & set(transfer_map):
        findings.append({"layer": "execution", "path": "feedback", "message": "feedback/intent identity mismatch"})
    expected_flows = [{"date": flow.date, "amount": flow.amount, "currency": scenario.currency}
                      for flow in sorted(scenario.cashflows, key=lambda x: x.date) if flow.amount]
    if len(r["cashflows"]) != len(expected_flows):
        findings.append({"layer": "timing", "path": "cashflows", "message": "cashflow count mismatch"})
    for actual, expected in zip(r["cashflows"], expected_flows):
        if actual["date"] != expected["date"] or actual["currency"] != expected["currency"]:
            findings.append({"layer": "timing", "path": "cashflows", "message": "cashflow date/currency mismatch"})
        check("cashflows.amount", actual["amount"], expected["amount"], ZERO)
    if len(r["valuations"]) != len(scenario.bars):
        findings.append({"layer": "timing", "path": "valuations", "message": "valuation count mismatch"})
    for index, (bar, value) in enumerate(zip(scenario.bars, r["valuations"])):
        if value["date"] != bar.date:
            findings.append({"layer": "timing", "path": f"valuations[{index}].date", "actual": value["date"], "expected": bar.date})
        flow = sum((D(x["amount"]) for x in expected_flows if x["date"] == bar.date), ZERO)
        cash += flow
        budgets["invest"] += flow
        available, sellable = dict(budgets), dict(holdings)
        for intent in intents_by_date.get(bar.date, ()):
            key = intent["intent_id"]
            if intent["kind"] not in {"order", "transfer"}:
                findings.append({"layer": "execution", "path": key + ".kind", "message": "unknown intent kind"})
                continue
            f = (fill_map if intent["kind"] == "order" else transfer_map).get(key)
            if f is None:
                findings.append({"layer": "execution", "path": key, "message": "missing feedback of the required kind"})
                continue
            before_binding = len(findings)
            binding_fields = ("date", "reason", "rule_id") + (
                ("symbol", "side", "budget") if intent["kind"] == "order" else ("kind", "source", "target"))
            for field in binding_fields:
                if f[field] != intent[field]:
                    findings.append({"layer": "execution", "path": f"{key}.{field}", "actual": f[field], "expected": intent[field]})
            amount_field = "requested" if intent["kind"] == "order" else "amount"
            check(f"{key}.{amount_field}", f[amount_field], intent["amount"], ZERO, "execution")
            if len(findings) != before_binding:
                # Never interpret mismatched feedback as a different trade.
                continue
            error = _intent_error(intent, available, sellable, bar.open, scenario.fees,
                                  scenario.execution["insufficient_cash"])
            if intent["kind"] == "transfer":
                expected_status = "rejected" if error else "transferred"
                if f["status"] != expected_status or f["rejection"] != error:
                    findings.append({"layer": "execution", "path": key + ".status",
                                     "actual": f["status"], "expected": expected_status,
                                     "message": f"expected rejection: {error}"})
                    continue
                if f["status"] == "transferred":
                    amount = D(intent["amount"])
                    budgets[intent["source"]] -= amount
                    budgets[intent["target"]] += amount
                    available[intent["source"]] -= amount
                    available[intent["target"]] += amount
                continue
            quantity, fee, notional = D(f["quantity"]), D(f["fee"]), D(f["notional"])
            if f["status"] == "rejected":
                for field in ("quantity", "fee", "notional", "cash_delta"):
                    check(key + "." + field, f[field], ZERO, ZERO, "execution")
                check(key + ".residual", f["residual"], intent["amount"], ZERO, "execution")
                if f["price"] is not None:
                    findings.append({"layer": "execution", "path": key + ".price", "message": "rejected order must not have a fill price"})
                if error is None:
                    findings.append({"layer": "execution", "path": key, "message": "rejected an executable intent"})
                elif f["rejection"] != error:
                    findings.append({"layer": "execution", "path": key + ".rejection", "actual": f["rejection"], "expected": error})
                continue
            if error or f["status"] not in {"filled", "partial"} or f["rejection"] is not None:
                findings.append({"layer": "execution", "path": key + ".status",
                                 "message": f"invalid execution status or executed an invalid intent: {error}"})
                continue
            symbol, budget, side = intent["symbol"], intent["budget"], intent["side"]
            price = bar.open[symbol]
            check(key + ".price", f["price"], price, ZERO, "execution")
            check(key + ".notional", notional, quantity * price, ZERO, "execution")
            check(key + ".fee", fee, scenario.fees.calculate(quantity, price), ZERO, "execution")
            if quantity <= 0 or quantity % LOT != 0:
                findings.append({"layer": "execution", "path": key, "message": "invalid fill lot"})
            if side == "buy":
                limit = min(D(intent["amount"]), available[budget])
                if quantity * price + fee > limit:
                    findings.append({"layer": "execution", "path": key, "message": "fill exceeds unreserved budget"})
                if (quantity + LOT) * price + scenario.fees.calculate(quantity + LOT, price) <= limit:
                    findings.append({"layer": "execution", "path": key, "message": "buy did not execute maximum affordable lot"})
                expected_status = "partial" if D(intent["amount"]) > available[budget] else "filled"
                available[budget] -= quantity * price + fee
                residual = D(intent["amount"]) - quantity * price - fee
            else:
                expected_quantity = (D(intent["amount"]) / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
                check(key + ".quantity", quantity, expected_quantity, ZERO, "execution")
                if quantity > sellable[symbol] + TOLERANCE:
                    findings.append({"layer": "execution", "path": key, "message": "fill exceeds unreserved holdings"})
                sellable[symbol] -= quantity
                expected_status = "filled"
                residual = D(intent["amount"]) - quantity
            if f["status"] != expected_status:
                findings.append({"layer": "execution", "path": key + ".status", "actual": f["status"], "expected": expected_status})
            check(key + ".residual", f["residual"], residual)
            signed = quantity if side == "buy" else -quantity
            delta = -signed * price - fee
            check(key + ".cash_delta", f["cash_delta"], delta)
            if side == "buy" and -delta > D(intent["amount"]):
                findings.append({"layer": "execution", "path": key, "message": "buy exceeded intent budget"})
            holdings[symbol] += signed
            cash += delta
            budgets[budget] += delta
        equity = cash + sum((holdings[s] * bar.close[s] for s in holdings), ZERO)
        for field, expected in (("cash", cash), ("equity", equity), ("external_flow", flow)):
            check(f"valuations[{index}].{field}", value[field], expected)
        for section, expected in (("positions", holdings), ("budgets", budgets)):
            if set(value[section]) != set(expected):
                findings.append({"layer": "accounting", "path": section, "message": "account keys mismatch"})
            for key, amount in expected.items():
                check(f"valuations[{index}].{section}.{key}", value[section].get(key, "0"), amount)
                if amount < 0:
                    findings.append({"layer": "accounting", "path": section + "." + key, "message": "negative balance"})
        check(f"valuations[{index}].budget_sum", sum((D(x) for x in value["budgets"].values()), ZERO), value["cash"])
    if not findings and scenario.expected_metrics:
        evaluation = evaluate(result, EvaluationSpec(tuple(scenario.expected_metrics)))
        for key, value in scenario.expected_metrics.items():
            check("evaluation." + key, evaluation.metrics[key], value,
                  RETURN_TOLERANCE if key in {"time_weighted_return", "max_drawdown"} else TOLERANCE, "evaluation")
    return findings


def compare(left, right):
    """Compare run inputs and ledgers across execution engines."""
    a, b = plain(left), plain(right)
    _validate_execution_engine(a["manifest"])
    _validate_execution_engine(b["manifest"])
    differences = []

    def walk(x, y, path, layer):
        if isinstance(x, dict) and isinstance(y, dict):
            for key in sorted(set(x) | set(y)):
                walk(x.get(key), y.get(key), path + "." + key, layer)
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                differences.append({"layer": layer, "path": path + ".length", "left": len(x), "right": len(y)})
            for index, (xx, yy) in enumerate(zip(x, y)):
                walk(xx, yy, f"{path}[{index}]", layer)
        elif x != y:
            tolerance = ZERO if path.endswith((".fee", ".price")) else TOLERANCE
            numeric_fields = {"amount", "requested", "quantity", "price", "notional", "fee", "cash_delta", "residual",
                              "cash", "equity", "external_flow"}
            numeric = not path.startswith(("manifest.", "decisions[")) and (
                path.rsplit(".", 1)[-1] in numeric_fields or ".positions." in path or ".budgets." in path
            )
            try:
                if numeric and x is not None and y is not None and abs(D(x) - D(y)) <= tolerance:
                    return
            except (ValueError, InvalidOperation):
                pass
            differences.append({"layer": layer, "path": path, "left": x, "right": y})

    for key in ("status", "framework_version", "protocol", "strategy", "scenario", "parameters", "execution", "currency", "fees"):
        walk(a["manifest"][key], b["manifest"][key], "manifest." + key, "inputs")
    for section, layer in (("cashflows", "timing"), ("decisions", "strategy"), ("intents", "timing"),
                            ("transfers", "accounting"), ("fills", "execution"),
                            ("valuations", "accounting"), ("diagnostics", "verification")):
        walk(a[section], b[section], section, layer)
    return differences
