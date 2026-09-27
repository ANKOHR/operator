"""Deterministic synthetic company data generation."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

DEPARTMENTS = ("sales", "delivery", "finance", "support")
START_MONTH = (2025, 6)
MONTH_COUNT = 15
CONTROL_MONTH = "2026-05"


def month_sequence(start: tuple[int, int] = START_MONTH, count: int = MONTH_COUNT) -> list[str]:
    year, month = start
    result = []
    for _ in range(count):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def _rounded(value: float) -> int:
    return int(round(value))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate_company(seed: int, out: str | Path) -> dict[str, Any]:
    """Write a reproducible fictional company and its monthly source records."""
    rng = random.Random(seed)
    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    company = {
        "company_id": "SYNTH-NORTHSTAR-001",
        "name": "Northstar Fieldworks Ltd",
        "synthetic": True,
        "currency": "GBP",
        "employee_range": [30, 100],
        "generated_seed": seed,
        "first_month": month_sequence()[0],
        "last_month": month_sequence()[-1],
    }
    _write_json(output / "company.json", company)

    revenue_plan = {"sales": 118_000, "delivery": 252_000, "finance": 0, "support": 38_000}
    expense_plan = {"sales": 82_000, "delivery": 214_000, "finance": 48_000, "support": 56_000}
    headcount_plan = {"sales": 8, "delivery": 32, "finance": 5, "support": 10}
    cash_balance = 1_650_000
    issue_register: dict[str, Any] = {
        "synthetic_test_fixture": True,
        "not_for_management_output": True,
        "seed": seed,
        "issues": [],
    }

    for month in month_sequence():
        rev_factor = rng.uniform(0.975, 1.035)
        if month == "2026-07":
            rev_factor = 0.79
        rev_actual = {d: _rounded(value * rev_factor * rng.uniform(0.99, 1.01)) for d, value in revenue_plan.items()}
        exp_actual = {d: _rounded(value * rng.uniform(0.985, 1.025)) for d, value in expense_plan.items()}
        if month == "2026-05":
            rev_actual = {d: _rounded(value * 1.01) for d, value in revenue_plan.items()}

        revenue_plan_rows = []
        revenue_actual_rows = []
        expense_plan_rows = []
        expense_actual_rows = []
        for department in DEPARTMENTS:
            revenue_plan_rows.append({"id": f"REV-PLAN-{month}-{department}", "department": department, "amount": revenue_plan[department]})
            revenue_actual_rows.append({"id": f"REV-ACT-{month}-{department}", "department": department, "amount": rev_actual[department]})
            expense_plan_rows.append({"id": f"EXP-PLAN-{month}-{department}", "department": department, "amount": expense_plan[department]})
            expense_actual_rows.append({"id": f"EXP-ACT-{month}-{department}", "department": department, "amount": exp_actual[department]})

        bank_opening = {"id": f"BANK-OPEN-{month}", "amount": cash_balance}
        book_opening = {"id": f"BOOK-OPEN-{month}", "amount": cash_balance}
        movement_specs = [
            ("customer-receipts", 515_000),
            ("payroll", -256_000),
            ("supplier-payments", -193_000),
            ("tax-and-overheads", -44_000),
        ]
        bank_movements = []
        book_movements = []
        for reference, amount in movement_specs:
            varied = amount + rng.randint(-4_000, 4_000)
            bank_movements.append({"id": f"BANK-{month}-{reference}", "reference": reference, "amount": varied})
            book_movements.append({"id": f"BOOK-{month}-{reference}", "reference": reference, "amount": varied})

        if month == "2026-06":
            bank_movements.append({"id": f"BANK-{month}-unmatched-credit", "reference": "unmatched-credit-0626", "amount": 27_500})
            issue_register["issues"].append({
                "kind": "cash_reconciliation", "month": month, "amount": 27_500,
                "record_ids": [f"BANK-{month}-unmatched-credit"],
            })
        if month == "2026-07":
            book_movements.append({"id": f"BOOK-{month}-duplicate-payment", "reference": "duplicate-payment-0726", "amount": -18_250})
            issue_register["issues"].append({
                "kind": "cash_reconciliation", "month": month, "amount": 18_250,
                "record_amount": -18_250,
                "record_ids": [f"BOOK-{month}-duplicate-payment"],
            })

        if month == "2026-07":
            bank_movements.append({"id": f"BANK-{month}-cash-dip", "reference": "exceptional-cash-dip", "amount": -1_050_000})
            book_movements.append({"id": f"BOOK-{month}-cash-dip", "reference": "exceptional-cash-dip", "amount": -1_050_000})
            issue_register["issues"].append({
                "kind": "cash_dip", "month": month, "amount": -1_050_000,
                "record_ids": [f"BANK-{month}-cash-dip", f"BOOK-{month}-cash-dip"],
            })

        delivery_capacity = 480 + rng.randint(-8, 8)
        delivery_throughput = _rounded(delivery_capacity * rng.uniform(0.77, 0.85))
        if month == "2026-08":
            delivery_capacity = 400
            delivery_throughput = 438

        if month == "2026-08":
            actual_heads = {"sales": 8, "delivery": 27, "finance": 5, "support": 10}
        else:
            actual_heads = dict(headcount_plan)

        if month == "2026-08":
            support = [{"id": f"SUP-{month}-rollup", "opening_backlog": 96, "new_tickets": 174, "closed_tickets": 116}]
        else:
            opening = 92 if month == CONTROL_MONTH else 96
            arrivals = 122 if month == CONTROL_MONTH else 124 + rng.randint(-4, 4)
            closed = 127 if month == CONTROL_MONTH else arrivals + rng.randint(-5, 5)
            support = [{"id": f"SUP-{month}-rollup", "opening_backlog": opening, "new_tickets": arrivals, "closed_tickets": closed}]

        people = [{
            "id": f"HC-{month}-{department}", "department": department,
            "plan": headcount_plan[department], "actual": actual_heads[department],
        } for department in DEPARTMENTS]
        delivery = [
            {"id": f"CAP-{month}-sales", "department": "sales", "capacity_units": 220, "throughput_units": 176},
            {"id": f"CAP-{month}-delivery", "department": "delivery", "capacity_units": delivery_capacity, "throughput_units": delivery_throughput},
            {"id": f"CAP-{month}-finance", "department": "finance", "capacity_units": 120, "throughput_units": 84},
            {"id": f"CAP-{month}-support", "department": "support", "capacity_units": 300, "throughput_units": 210},
        ]
        sales = []
        for row in revenue_actual_rows:
            sales.append({
                "id": f"SALE-{month}-{row['department']}", "department": row["department"],
                "recognized_revenue": row["amount"], "stage": "recognized", "source_ref": row["id"],
            })
        if month == "2026-07":
            sales.append({
                "id": f"SALE-{month}-overlap", "department": "delivery",
                "recognized_revenue": 42_000, "stage": "recognized", "source_ref": f"SALE-{month}-delivery",
            })
            issue_register["issues"].append({
                "kind": "revenue_reconciliation", "month": month, "amount": 42_000,
                "record_ids": [f"SALE-{month}-delivery", f"SALE-{month}-overlap"],
            })
        for i in range(6):
            sales.append({
                "id": f"PIPE-{month}-{i + 1:02d}", "department": "sales",
                "recognized_revenue": 0, "stage": ("qualified", "proposal", "discovery")[i % 3],
                "source_ref": f"OPP-{month}-{i + 1:02d}",
                "pipeline_amount": rng.randint(15_000, 90_000),
                "probability_pct": (20, 40, 60, 70, 30, 50)[i],
            })

        month_data = {
            "month": month,
            "synthetic": True,
            "finance": {
                "bank_opening": [bank_opening], "book_opening": [book_opening],
                "bank_movements": bank_movements, "book_movements": book_movements,
                "revenue_plan": revenue_plan_rows, "revenue_actual": revenue_actual_rows,
                "expense_plan": expense_plan_rows, "expense_actual": expense_actual_rows,
            },
            "people": people,
            "sales": sales,
            "delivery": delivery,
            "support": support,
        }
        if month == "2026-07":
            closing_cash = cash_balance + sum(row["amount"] for row in bank_movements)
            expense_total = sum(exp_actual.values())
            issue_register["issues"].append({
                "kind": "alert_threshold", "month": month, "rule_id": "CASH_RUNWAY_LT_3",
                "observed_value": round(closing_cash / expense_total, 6), "threshold": 3.0,
                "record_ids": [row["id"] for row in [bank_opening] + bank_movements] + [row["id"] for row in expense_actual_rows],
            })
            issue_register["issues"].append({
                "kind": "alert_threshold", "month": month,
                "rule_id": "REV_PLAN_SHORTFALL_GT_12",
                "observed_value": round((sum(rev_actual.values()) - sum(revenue_plan.values())) / sum(revenue_plan.values()), 6),
                "threshold": -0.12,
                "record_ids": [row["id"] for row in revenue_plan_rows + revenue_actual_rows],
            })
        if month == "2026-08":
            expense_total = sum(exp_actual.values())
            closing_cash = cash_balance + sum(row["amount"] for row in bank_movements)
            issue_register["issues"].extend([
                {"kind": "alert_threshold", "month": month, "rule_id": "CASH_RUNWAY_LT_3", "observed_value": round(closing_cash / expense_total, 6), "threshold": 3.0, "record_ids": [row["id"] for row in [bank_opening] + bank_movements] + [row["id"] for row in expense_actual_rows]},
                {"kind": "alert_threshold", "month": month, "rule_id": "SUPPORT_BACKLOG_GROWTH_GT_25", "observed_value": round((support[0]["opening_backlog"] + support[0]["new_tickets"] - support[0]["closed_tickets"] - support[0]["opening_backlog"]) / support[0]["opening_backlog"], 6), "threshold": 0.25, "record_ids": [support[0]["id"]]},
                {"kind": "alert_threshold", "month": month, "rule_id": "HEADCOUNT_PLAN_SHORTFALL_GT_8", "observed_value": round((sum(actual_heads.values()) - sum(headcount_plan.values())) / sum(headcount_plan.values()), 6), "threshold": -0.08, "record_ids": [row["id"] for row in people]},
                {"kind": "alert_threshold", "month": month, "rule_id": "CAPACITY_UTILIZATION_GT_90", "observed_value": round(delivery_throughput / delivery_capacity, 6), "threshold": 0.90, "record_ids": [f"CAP-{month}-delivery"]},
            ])
        month_path = output / "months" / f"{month}.json"
        _write_json(month_path, month_data)
        cash_balance = bank_opening["amount"] + sum(r["amount"] for r in bank_movements)

    _write_json(output / "test-fixtures" / "planted_issues.json", issue_register)
    return company
