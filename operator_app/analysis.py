"""Deterministic variance, reconciliation, alert, and capacity calculations."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .company import DEPARTMENTS

RULES = {
    "CASH_RUNWAY_LT_3": {"threshold": 3.0, "operator": "<", "unit": "months", "owner": "Finance lead"},
    "REV_PLAN_SHORTFALL_GT_12": {"threshold": -0.12, "operator": "<", "unit": "fraction", "owner": "Sales lead"},
    "SUPPORT_BACKLOG_GROWTH_GT_25": {"threshold": 0.25, "operator": ">", "unit": "fraction", "owner": "Support lead"},
    "HEADCOUNT_PLAN_SHORTFALL_GT_8": {"threshold": -0.08, "operator": "<", "unit": "fraction", "owner": "COO"},
    "EXPENSE_OVERRUN_GT_10": {"threshold": 0.10, "operator": ">", "unit": "fraction", "owner": "Finance lead"},
    "CAPACITY_UTILIZATION_GT_90": {"threshold": 0.90, "operator": ">", "unit": "fraction", "owner": "Delivery lead"},
}


def _sum_by_department(rows: list[dict[str, Any]], field: str = "amount") -> dict[str, int]:
    result = {department: 0 for department in DEPARTMENTS}
    for row in rows:
        result[row["department"]] += row[field]
    return result


def _refs(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(row["id"] for row in rows)


def _cash_reconciliation(month_data: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int, int]:
    finance = month_data["finance"]
    bank_open = finance["bank_opening"]
    book_open = finance["book_opening"]
    # Multiple records for one reference stay distinct and are treated as a sum.
    bank_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    book_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in finance["bank_movements"]:
        bank_grouped[row["reference"]].append(row)
    for row in finance["book_movements"]:
        book_grouped[row["reference"]].append(row)
    breaks = []
    for reference in sorted(set(bank_grouped) | set(book_grouped)):
        bank_rows = bank_grouped.get(reference, [])
        book_rows = book_grouped.get(reference, [])
        bank_amount = sum(row["amount"] for row in bank_rows)
        book_amount = sum(row["amount"] for row in book_rows)
        difference = bank_amount - book_amount
        if difference:
            breaks.append({
                "break_id": f"REC-{month_data['month']}-CASH-{reference}",
                "kind": "cash_reconciliation",
                "month": month_data["month"],
                "reference": reference,
                "bank_amount": bank_amount,
                "book_amount": book_amount,
                "difference": difference,
                "amount": difference,
                "record_ids": _refs(bank_rows) + _refs(book_rows),
            })
    bank_close = sum(row["amount"] for row in bank_open) + sum(row["amount"] for row in finance["bank_movements"])
    book_close = sum(row["amount"] for row in book_open) + sum(row["amount"] for row in finance["book_movements"])
    opening_evidence = _refs(bank_open) + _refs(book_open)
    if bank_open[0]["amount"] != book_open[0]["amount"]:
        difference = bank_open[0]["amount"] - book_open[0]["amount"]
        breaks.append({
            "break_id": f"REC-{month_data['month']}-CASH-opening-balance",
            "kind": "cash_reconciliation", "month": month_data["month"],
            "reference": "opening-balance", "bank_amount": bank_open[0]["amount"],
            "book_amount": book_open[0]["amount"], "difference": difference,
            "amount": difference, "record_ids": opening_evidence,
        })
    return breaks, bank_close, book_close, bank_close - book_close


def _revenue_reconciliation(month_data: dict[str, Any]) -> list[dict[str, Any]]:
    finance = month_data["finance"]
    finance_total = sum(row["amount"] for row in finance["revenue_actual"])
    sales_rows = [row for row in month_data["sales"] if row["recognized_revenue"] != 0]
    sales_total = sum(row["recognized_revenue"] for row in sales_rows)
    difference = sales_total - finance_total
    duplicates = [key for key, count in Counter(row["source_ref"] for row in sales_rows).items() if count > 1]
    if difference == 0 and not duplicates:
        return []
    if duplicates:
        sales_evidence = [row["id"] for row in sales_rows if row["source_ref"] in duplicates]
    else:
        sales_evidence = _refs(sales_rows)
    return [{
        "break_id": f"REC-{month_data['month']}-REVENUE-sales-to-finance",
        "kind": "revenue_reconciliation",
        "month": month_data["month"],
        "sales_recognized": sales_total,
        "finance_recognized": finance_total,
        "difference": difference,
        "amount": difference,
        "duplicate_source_refs": sorted(duplicates),
        "record_ids": sorted(set(sales_evidence + _refs(finance["revenue_actual"]))),
    }]


def analyze_month(month_data: dict[str, Any]) -> dict[str, Any]:
    """Compute all management metrics and rules from validated source records."""
    month = month_data["month"]
    finance = month_data["finance"]
    revenue_plan = _sum_by_department(finance["revenue_plan"])
    revenue_actual = _sum_by_department(finance["revenue_actual"])
    expense_plan = _sum_by_department(finance["expense_plan"])
    expense_actual = _sum_by_department(finance["expense_actual"])
    total_revenue_plan = sum(revenue_plan.values())
    total_revenue_actual = sum(revenue_actual.values())
    total_expense_plan = sum(expense_plan.values())
    total_expense_actual = sum(expense_actual.values())
    revenue_variance = total_revenue_actual - total_revenue_plan
    expense_variance = total_expense_actual - total_expense_plan
    revenue_variance_pct = revenue_variance / total_revenue_plan if total_revenue_plan else 0.0
    expense_variance_pct = expense_variance / total_expense_plan if total_expense_plan else 0.0

    cash_breaks, bank_close, book_close, cash_difference = _cash_reconciliation(month_data)
    revenue_breaks = _revenue_reconciliation(month_data)
    cash_runway = bank_close / total_expense_actual if total_expense_actual else 0.0
    support_record = month_data["support"][0]
    support_end = support_record["opening_backlog"] + support_record["new_tickets"] - support_record["closed_tickets"]
    backlog_growth = (support_end - support_record["opening_backlog"]) / support_record["opening_backlog"] if support_record["opening_backlog"] else 0.0
    planned_heads = sum(row["plan"] for row in month_data["people"])
    actual_heads = sum(row["actual"] for row in month_data["people"])
    headcount_drift = (actual_heads - planned_heads) / planned_heads if planned_heads else 0.0
    capacity_rows = []
    for row in month_data["delivery"]:
        capacity_rows.append({**row, "utilization": row["throughput_units"] / row["capacity_units"]})
    capacity_rows.sort(key=lambda row: (-row["utilization"], row["department"]))
    binding = capacity_rows[0]

    department_summaries = []
    for department in DEPARTMENTS:
        r_plan = revenue_plan[department]
        r_actual = revenue_actual[department]
        e_plan = expense_plan[department]
        e_actual = expense_actual[department]
        hc = next(row for row in month_data["people"] if row["department"] == department)
        cap = next(row for row in capacity_rows if row["department"] == department)
        department_summaries.append({
            "department": department,
            "revenue_plan": r_plan,
            "revenue_actual": r_actual,
            "revenue_variance": r_actual - r_plan,
            "revenue_variance_pct": (r_actual - r_plan) / r_plan if r_plan else None,
            "revenue_record_ids": [f"REV-PLAN-{month}-{department}", f"REV-ACT-{month}-{department}"],
            "expense_plan": e_plan,
            "expense_actual": e_actual,
            "expense_variance": e_actual - e_plan,
            "expense_variance_pct": (e_actual - e_plan) / e_plan if e_plan else None,
            "expense_record_ids": [f"EXP-PLAN-{month}-{department}", f"EXP-ACT-{month}-{department}"],
            "headcount_plan": hc["plan"],
            "headcount_actual": hc["actual"],
            "headcount_record_ids": [hc["id"]],
            "capacity_units": cap["capacity_units"],
            "throughput_units": cap["throughput_units"],
            "utilization": cap["utilization"],
            "capacity_record_ids": [cap["id"]],
        })

    alerts = []

    def fire(rule_id: str, observed: float, title: str, evidence_ids: list[str], action: str, decision: str | None = None, context: str = "", options: list[str] | None = None, department: str | None = None) -> None:
        rule = RULES[rule_id]
        alerts.append({
            "alert_id": f"ALT-{month}-{rule_id}",
            "rule_id": rule_id,
            "title": title,
            "month": month,
            "threshold": rule["threshold"],
            "operator": rule["operator"],
            "unit": rule["unit"],
            "observed_value": round(observed, 6),
            "severity": "high" if rule_id in {"CASH_RUNWAY_LT_3", "REV_PLAN_SHORTFALL_GT_12"} else "medium",
            "owner": rule["owner"],
            "department": department,
            "evidence_record_ids": sorted(set(evidence_ids)),
            "action": action,
            "decision": decision,
            "context": context,
            "options": options or [],
        })

    if cash_runway < RULES["CASH_RUNWAY_LT_3"]["threshold"]:
        fire("CASH_RUNWAY_LT_3", cash_runway, "Cash covers fewer than three months of current operating expenses", _refs(finance["bank_opening"] + finance["bank_movements"] + finance["expense_actual"]), "Prepare and review a 13-week cash forecast", "Approve a cash protection plan", f"Cash coverage is {cash_runway:.2f} months at the current expense run rate.", ["Freeze discretionary spend", "Secure short-term financing", "Accept the current cash plan"])
    if revenue_variance_pct < RULES["REV_PLAN_SHORTFALL_GT_12"]["threshold"]:
        fire("REV_PLAN_SHORTFALL_GT_12", revenue_variance_pct, "Revenue is more than 12 percent below plan", _refs(finance["revenue_plan"] + finance["revenue_actual"]), "Review the sales forecast and recovery pipeline", "Reforecast the quarter", f"Revenue is {revenue_variance_pct:.1%} against plan.", ["Keep the current plan", "Reset the forecast", "Add a recovery target"], "sales")
    if backlog_growth > RULES["SUPPORT_BACKLOG_GROWTH_GT_25"]["threshold"]:
        fire("SUPPORT_BACKLOG_GROWTH_GT_25", backlog_growth, "Support backlog grew by more than 25 percent", [support_record["id"]], "Review ticket triage and temporary support capacity", None, f"Ticket backlog grew by {backlog_growth:.1%} during the month.", department="support")
    if headcount_drift < RULES["HEADCOUNT_PLAN_SHORTFALL_GT_8"]["threshold"]:
        fire("HEADCOUNT_PLAN_SHORTFALL_GT_8", headcount_drift, "Actual headcount is more than 8 percent below plan", _refs(month_data["people"]), "Review delivery staffing and hiring priorities", "Approve a revised hiring plan", f"Headcount is {headcount_drift:.1%} below plan.", ["Backfill open roles", "Use contractors", "Reduce planned delivery capacity"], "delivery")
    if expense_variance_pct > RULES["EXPENSE_OVERRUN_GT_10"]["threshold"]:
        fire("EXPENSE_OVERRUN_GT_10", expense_variance_pct, "Operating expenses are more than 10 percent over plan", _refs(finance["expense_plan"] + finance["expense_actual"]), "Identify and control the expense overrun")
    if binding["utilization"] > RULES["CAPACITY_UTILIZATION_GT_90"]["threshold"]:
        fire("CAPACITY_UTILIZATION_GT_90", binding["utilization"], f"{binding['department'].title()} capacity is above 90 percent utilization", [binding["id"]], f"Relieve the {binding['department']} capacity constraint", "Approve a capacity response", f"Throughput is {binding['throughput_units']} units against capacity of {binding['capacity_units']}.", ["Add temporary capacity", "Resequence work", "Accept longer lead times"], binding["department"])

    all_breaks = cash_breaks + revenue_breaks
    metrics = {
        "revenue_plan": {"value": total_revenue_plan, "unit": "GBP", "evidence_record_ids": _refs(finance["revenue_plan"])},
        "revenue_actual": {"value": total_revenue_actual, "unit": "GBP", "evidence_record_ids": _refs(finance["revenue_actual"])},
        "revenue_variance": {"value": revenue_variance, "unit": "GBP", "evidence_record_ids": _refs(finance["revenue_plan"] + finance["revenue_actual"])},
        "revenue_variance_pct": {"value": round(revenue_variance_pct, 6), "unit": "fraction", "evidence_record_ids": _refs(finance["revenue_plan"] + finance["revenue_actual"])},
        "expense_plan": {"value": total_expense_plan, "unit": "GBP", "evidence_record_ids": _refs(finance["expense_plan"])},
        "expense_actual": {"value": total_expense_actual, "unit": "GBP", "evidence_record_ids": _refs(finance["expense_actual"])},
        "bank_closing_cash": {"value": bank_close, "unit": "GBP", "evidence_record_ids": _refs(finance["bank_opening"] + finance["bank_movements"])},
        "book_closing_cash": {"value": book_close, "unit": "GBP", "evidence_record_ids": _refs(finance["book_opening"] + finance["book_movements"])},
        "cash_runway_months": {"value": round(cash_runway, 6), "unit": "months of current operating expenses", "evidence_record_ids": _refs(finance["bank_opening"] + finance["bank_movements"] + finance["expense_actual"])},
        "headcount_plan": {"value": planned_heads, "unit": "people", "evidence_record_ids": _refs(month_data["people"])},
        "headcount_actual": {"value": actual_heads, "unit": "people", "evidence_record_ids": _refs(month_data["people"])},
        "support_backlog_open": {"value": support_record["opening_backlog"], "unit": "tickets", "evidence_record_ids": [support_record["id"]]},
        "support_backlog_close": {"value": support_end, "unit": "tickets", "evidence_record_ids": [support_record["id"]]},
    }
    return {
        "month": month,
        "synthetic": True,
        "metrics": metrics,
        "cash_reconciliation": {"bank_closing_cash": bank_close, "book_closing_cash": book_close, "difference": cash_difference, "breaks": cash_breaks},
        "cross_source_breaks": revenue_breaks,
        "reconciliation_breaks": all_breaks,
        "department_summaries": department_summaries,
        "capacity": {"departments": capacity_rows, "binding_constraint": binding, "threshold": RULES["CAPACITY_UTILIZATION_GT_90"]["threshold"]},
        "support": {"opening_backlog": support_record["opening_backlog"], "new_tickets": support_record["new_tickets"], "closed_tickets": support_record["closed_tickets"], "closing_backlog": support_end, "growth_pct": round(backlog_growth, 6), "record_ids": [support_record["id"]]},
        "headcount": {"plan": planned_heads, "actual": actual_heads, "drift_pct": round(headcount_drift, 6), "record_ids": _refs(month_data["people"])},
        "alerts": alerts,
    }


def trace_alert(month_data: dict[str, Any], alert_id: str) -> dict[str, Any]:
    report = analyze_month(month_data)
    for alert in report["alerts"]:
        if alert["alert_id"] == alert_id:
            records = {row["id"]: row for source_rows in (
                month_data["finance"][key]
                for key in ("bank_opening", "book_opening", "bank_movements", "book_movements", "revenue_plan", "revenue_actual", "expense_plan", "expense_actual")
            ) for row in source_rows}
            records.update({row["id"]: row for key in ("people", "sales", "delivery", "support") for row in month_data[key]})
            return {"alert": alert, "evidence": [records[key] for key in alert["evidence_record_ids"] if key in records]}
    raise KeyError(f"Alert {alert_id} did not fire for {month_data['month']}")
