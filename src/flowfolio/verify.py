"""Independent ledger reconstruction and cross-runtime diagnostics."""
from decimal import InvalidOperation, ROUND_FLOOR, Context as DecimalContext, localcontext
from .model import D, ZERO, LOT, TOLERANCE, RETURN_TOLERANCE, plain


def verify(result, case):
    with localcontext(DecimalContext(prec=40)):
        try:
            return _verify(result, case)
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


def _verify(result, case):
    r = plain(result)
    findings = []
    if r["manifest"]["case"]["sha256"] != case.fingerprint:
        findings.append({"layer": "inputs", "path": "manifest.case.sha256", "message": "case hash mismatch"})

    def check(path, actual, expected, tolerance=TOLERANCE, layer="accounting"):
        if abs(D(actual) - D(expected)) > tolerance:
            findings.append({"layer": layer, "path": path, "actual": str(actual), "expected": str(expected)})

    if r["manifest"]["status"] != "completed":
        return [{"layer": "compatibility", "path": "manifest.status", "actual": r["manifest"]["status"], "expected": "completed"}]
    for key, expected in (("execution", plain(case.data["execution"])),
                          ("currency", case.data["currency"]), ("fees", plain(case.fees))):
        if r["manifest"][key] != expected:
            findings.append({"layer": "inputs", "path": "manifest." + key, "message": "manifest disagrees with the case"})
    cash = D(case.data["initial_cash"])
    holdings = {s: D(case.data["initial_positions"].get(s, "0")) for s in case.bars[0].close}
    budgets = {"invest": cash, "reserve": ZERO}
    initial = cash + sum((holdings[s] * case.bars[0].close[s] for s in holdings), ZERO)
    previous, wealth, peak, drawdown, total_fees, contributions = initial, D(1), D(1), ZERO, ZERO, ZERO
    fill_map = {f["intent_id"]: f for f in r["fills"]}
    transfer_map = {f["intent_id"]: f for f in r["transfers"]}
    for section in ("intents", "fills", "transfers"):
        ids = [x["intent_id"] for x in r[section]]
        if len(ids) != len(set(ids)):
            findings.append({"layer": "execution", "path": section, "message": "duplicate intent_id"})
        if any(x["date"] not in {b.date for b in case.bars[1:]} for x in r[section]):
            findings.append({"layer": "timing", "path": section, "message": "action outside execution dates"})
    if set(fill_map) | set(transfer_map) != {x["intent_id"] for x in r["intents"]} or set(fill_map) & set(transfer_map):
        findings.append({"layer": "execution", "path": "feedback", "message": "feedback/intent identity mismatch"})
    expected_flows = [{"date": f["date"], "amount": D(f["amount"]), "currency": case.data["currency"]}
                      for f in sorted(case.data["cashflows"], key=lambda x: x["date"]) if D(f["amount"])]
    if len(r["cashflows"]) != len(expected_flows):
        findings.append({"layer": "timing", "path": "cashflows", "message": "cashflow count mismatch"})
    for actual, expected in zip(r["cashflows"], expected_flows):
        if actual["date"] != expected["date"] or actual["currency"] != expected["currency"]:
            findings.append({"layer": "timing", "path": "cashflows", "message": "cashflow date/currency mismatch"})
        check("cashflows.amount", actual["amount"], expected["amount"], ZERO)
    if len(r["valuations"]) != len(case.bars):
        findings.append({"layer": "timing", "path": "valuations", "message": "valuation count mismatch"})
    for index, (bar, value) in enumerate(zip(case.bars, r["valuations"])):
        if value["date"] != bar.date:
            findings.append({"layer": "timing", "path": f"valuations[{index}].date", "actual": value["date"], "expected": bar.date})
        flow = sum((D(x["amount"]) for x in expected_flows if x["date"] == bar.date), ZERO)
        contributions += flow
        cash += flow
        budgets["invest"] += flow
        available, sellable = dict(budgets), dict(holdings)
        for intent in (x for x in r["intents"] if x["date"] == bar.date):
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
            error = _intent_error(intent, available, sellable, bar.open, case.fees,
                                  case.data["execution"]["insufficient_cash"])
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
            check(key + ".fee", fee, case.fees.calculate(quantity, price), ZERO, "execution")
            if quantity <= 0 or quantity % LOT != 0:
                findings.append({"layer": "execution", "path": key, "message": "invalid fill lot"})
            if side == "buy":
                limit = min(D(intent["amount"]), available[budget])
                if quantity * price + fee > limit:
                    findings.append({"layer": "execution", "path": key, "message": "fill exceeds unreserved budget"})
                if (quantity + LOT) * price + case.fees.calculate(quantity + LOT, price) <= limit:
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
            total_fees += fee
        equity = cash + sum((holdings[s] * bar.close[s] for s in holdings), ZERO)
        denominator = previous + flow
        ret = equity / denominator - 1 if index and denominator else ZERO
        if index:
            wealth *= 1 + ret
            peak = max(peak, wealth)
            drawdown = max(drawdown, 1 - wealth / peak)
        previous = equity
        for field, expected in (("cash", cash), ("equity", equity), ("external_flow", flow), ("unit_wealth", wealth), ("return", ret)):
            check(f"valuations[{index}].{field}", value[field], expected,
                  RETURN_TOLERANCE if field in {"unit_wealth", "return"} else TOLERANCE)
        for section, expected in (("positions", holdings), ("budgets", budgets)):
            if set(value[section]) != set(expected):
                findings.append({"layer": "accounting", "path": section, "message": "account keys mismatch"})
            for key, amount in expected.items():
                check(f"valuations[{index}].{section}.{key}", value[section].get(key, "0"), amount)
                if amount < 0:
                    findings.append({"layer": "accounting", "path": section + "." + key, "message": "negative balance"})
        check(f"valuations[{index}].budget_sum", sum((D(x) for x in value["budgets"].values()), ZERO), value["cash"])
    expected_metrics = {"final_value": previous, "contributions": contributions,
                        "profit": previous - initial - contributions, "total_fees": total_fees,
                        "time_weighted_return": wealth - 1, "max_drawdown": drawdown}
    for key, value in expected_metrics.items():
        check("metrics." + key, r["metrics"][key], value,
              RETURN_TOLERANCE if key in {"time_weighted_return", "max_drawdown"} else TOLERANCE, "metrics")
    for key, value in case.data.get("evaluation", {}).get("expected_metrics", {}).items():
        check("evaluation." + key, r["metrics"][key], value,
              RETURN_TOLERANCE if key in {"time_weighted_return", "max_drawdown"} else TOLERANCE, "evaluation")
    return findings


def compare(left, right):
    """Fixed tolerances; report every differing path, including raw ledger rows."""
    a, b = plain(left), plain(right)
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
            if path.endswith((".return", ".unit_wealth", ".time_weighted_return", ".max_drawdown")):
                tolerance = RETURN_TOLERANCE
            numeric_fields = {"amount", "requested", "quantity", "price", "notional", "fee", "cash_delta", "residual",
                              "cash", "equity", "external_flow", "return", "unit_wealth", "final_value", "contributions",
                              "profit", "total_fees", "time_weighted_return", "max_drawdown"}
            numeric = not path.startswith("manifest.") and (path.rsplit(".", 1)[-1] in numeric_fields or ".positions." in path or ".budgets." in path)
            try:
                if numeric and x is not None and y is not None and abs(D(x) - D(y)) <= tolerance:
                    return
            except (ValueError, InvalidOperation):
                pass
            differences.append({"layer": layer, "path": path, "left": x, "right": y})

    for key in ("status", "framework_version", "protocol", "strategy", "case", "parameters", "execution", "currency", "fees"):
        walk(a["manifest"][key], b["manifest"][key], "manifest." + key, "inputs")
    for section, layer in (("cashflows", "timing"), ("intents", "timing"), ("transfers", "accounting"),
                            ("fills", "execution"), ("valuations", "accounting"), ("metrics", "metrics"), ("diagnostics", "verification")):
        walk(a[section], b[section], section, layer)
    return differences
