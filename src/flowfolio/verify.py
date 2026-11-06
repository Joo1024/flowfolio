"""Independent ledger reconstruction and cross-runtime diagnostics."""
from decimal import InvalidOperation, ROUND_FLOOR, Context as DecimalContext, localcontext
from .model import D, ZERO, LOT, TOLERANCE, RETURN_TOLERANCE, plain


def verify(result, case):
    with localcontext(DecimalContext(prec=40)):
        return _verify(result, case)


def _verify(result, case):
    r = plain(result)
    findings = []
    if r["manifest"]["case"]["sha256"] != case.fingerprint:
        findings.append({"layer": "inputs", "path": "manifest.case.sha256", "message": "case hash mismatch"})

    def check(path, actual, expected, tolerance=TOLERANCE, layer="accounting"):
        if abs(D(actual) - D(expected)) > tolerance:
            findings.append({"layer": layer, "path": path, "actual": str(actual), "expected": str(expected)})

    if r["manifest"]["status"] == "unsupported":
        return [{"layer": "compatibility", "path": "manifest.status", "actual": "unsupported", "expected": "completed"}]
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
            f = (fill_map if intent["kind"] == "order" else transfer_map).get(key)
            if f is None:
                continue
            for field in ("date", "reason", "rule_id"):
                if f[field] != intent[field]:
                    findings.append({"layer": "execution", "path": f"{key}.{field}", "message": "provenance mismatch"})
            if intent["kind"] == "transfer":
                if f["status"] == "transferred":
                    amount = D(intent["amount"])
                    if amount > available[intent["source"]] + TOLERANCE:
                        findings.append({"layer": "execution", "path": key, "message": "transfer exceeds unreserved budget"})
                    budgets[intent["source"]] -= amount
                    budgets[intent["target"]] += amount
                    available[intent["source"]] -= amount
                    available[intent["target"]] += amount
                continue
            quantity, fee, notional = D(f["quantity"]), D(f["fee"]), D(f["notional"])
            if f["status"] == "rejected":
                for field in ("quantity", "fee", "notional", "cash_delta"):
                    check(key + "." + field, f[field], ZERO, ZERO, "execution")
                if not f["rejection"]:
                    findings.append({"layer": "execution", "path": key, "message": "rejection needs reason"})
                symbol, budget, amount = intent["symbol"], intent["budget"], D(intent["amount"])
                if symbol in bar.open and budget in available and amount > 0 and intent["reason"] and intent["rule_id"]:
                    price = bar.open[symbol]
                    can_buy = (intent["side"] == "buy" and
                               (case.data["execution"]["insufficient_cash"] == "clip" or amount <= available[budget]) and
                               min(amount, available[budget]) >= LOT * price + case.fees.calculate(LOT, price))
                    can_sell = (intent["side"] == "sell" and LOT <= amount <= sellable[symbol] and
                                amount * price >= case.fees.calculate(amount, price))
                    if can_buy or can_sell:
                        findings.append({"layer": "execution", "path": key, "message": "rejected an executable intent"})
                continue
            price = bar.open[f["symbol"]]
            check(key + ".price", f["price"], price, ZERO, "execution")
            check(key + ".notional", notional, quantity * price, layer="execution")
            check(key + ".fee", fee, case.fees.calculate(quantity, price), ZERO, "execution")
            if quantity <= 0 or quantity % LOT != 0:
                findings.append({"layer": "execution", "path": key, "message": "invalid fill lot"})
            if f["side"] == "buy":
                limit = min(D(intent["amount"]), available[f["budget"]])
                if notional + fee > limit + TOLERANCE:
                    findings.append({"layer": "execution", "path": key, "message": "fill exceeds unreserved budget"})
                if (quantity + LOT) * price + case.fees.calculate(quantity + LOT, price) <= limit:
                    findings.append({"layer": "execution", "path": key, "message": "buy did not execute maximum affordable lot"})
                expected_status = "partial" if D(intent["amount"]) > available[f["budget"]] else "filled"
                available[f["budget"]] -= notional + fee
                residual = D(intent["amount"]) - notional - fee
            else:
                expected_quantity = (D(intent["amount"]) / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
                check(key + ".quantity", quantity, expected_quantity, ZERO, "execution")
                if quantity > sellable[f["symbol"]] + TOLERANCE:
                    findings.append({"layer": "execution", "path": key, "message": "fill exceeds unreserved holdings"})
                sellable[f["symbol"]] -= quantity
                expected_status = "filled"
                residual = D(intent["amount"]) - quantity
            if f["status"] != expected_status:
                findings.append({"layer": "execution", "path": key + ".status", "actual": f["status"], "expected": expected_status})
            check(key + ".residual", f["residual"], residual)
            signed = quantity if f["side"] == "buy" else -quantity
            delta = -signed * price - fee
            check(key + ".cash_delta", f["cash_delta"], delta)
            if f["side"] == "buy" and -delta > D(intent["amount"]) + TOLERANCE:
                findings.append({"layer": "execution", "path": key, "message": "buy exceeded intent budget"})
            holdings[f["symbol"]] += signed
            cash += delta
            budgets[f["budget"]] += delta
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
                if amount < -TOLERANCE:
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
