"""Markdown, HTML, and spoken management briefing outputs."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from .analysis import analyze_month
from .ingest import get_month_data, list_actions, list_decisions, list_risks


def _money(amount: int) -> str:
    return f"£{amount:,.0f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def markdown_to_html(markdown: str, title: str) -> str:
    """Convert the small Markdown subset used by this report into standalone HTML."""
    lines = markdown.splitlines()
    body = []
    in_list = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("| "):
            if in_list:
                body.append("</ul>")
                in_list = False
            table_lines = []
            while i < len(lines) and lines[i].startswith("| "):
                table_lines.append(lines[i])
                i += 1
            rows = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in table_lines]
            rows = [row for row in rows if not all(set(cell) <= {"-", ":", " "} for cell in row)]
            if rows:
                body.append("<table><thead><tr>" + "".join(f"<th>{_esc(cell)}</th>" for cell in rows[0]) + "</tr></thead><tbody>")
                for row in rows[1:]:
                    body.append("<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>")
                body.append("</tbody></table>")
            continue
        i += 1
        if not line.strip():
            if in_list:
                body.append("</ul>")
                in_list = False
            continue
        if line.startswith("### "):
            tag, value = "h3", line[4:]
        elif line.startswith("## "):
            tag, value = "h2", line[3:]
        elif line.startswith("# "):
            tag, value = "h1", line[2:]
        elif line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline(line[2:])}</li>")
            continue
        else:
            tag, value = "p", line
        if in_list:
            body.append("</ul>")
            in_list = False
        body.append(f"<{tag}>{_inline(value)}</{tag}>")
    if in_list:
        body.append("</ul>")
    return "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>" + _esc(title) + "</title>" + "<style>body{font:16px/1.55 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#1e293b}h1,h2{color:#0f172a}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #cbd5e1;padding:.45rem;text-align:left;vertical-align:top}th{background:#f1f5f9}code{background:#f1f5f9;padding:.1rem .25rem} .label{font-weight:700;color:#9a3412}</style></head><body>" + "\n".join(body) + "</body></html>\n"


def _inline(value: str) -> str:
    escaped = _esc(value)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _previous_month_data(db_path: str | Path, month: str) -> dict[str, Any] | None:
    import sqlite3
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT month FROM months WHERE month < ? ORDER BY month DESC LIMIT 1", (month,)).fetchone()
    finally:
        connection.close()
    return get_month_data(db_path, row[0]) if row else None


def _evidence_index(report: dict[str, Any], previous_report: dict[str, Any] | None) -> list[str]:
    lines = ["## Evidence index", "", "Computed metrics list their ingested source record IDs. All currency values are GBP.", ""]
    for metric_id, metric in report["metrics"].items():
        lines.append(f"- `{metric_id}` = {metric['value']} {metric['unit']}; records: {', '.join(metric['evidence_record_ids'])}")
    if previous_report:
        for metric_id, metric in previous_report["metrics"].items():
            lines.append(f"- `previous.{metric_id}` = {metric['value']} {metric['unit']}; records: {', '.join(metric['evidence_record_ids'])}")
    lines.append("")
    return lines


def build_pack(company: dict[str, Any], report: dict[str, Any], previous: dict[str, Any] | None, risks: list[dict[str, Any]], decisions: list[dict[str, Any]], actions: list[dict[str, Any]]) -> str:
    month = report["month"]
    metrics = report["metrics"]
    bottleneck = report["capacity"]["binding_constraint"]
    lines = [
        f"# Monthly management pack: {month}",
        "",
        f"**SYNTHETIC DATA** | {company['name']} | Currency: GBP | Generated from ingested monthly records",
        "",
        "## Executive summary",
        "",
        f"For {month}, revenue was {_money(metrics['revenue_actual']['value'])} against a plan of {_money(metrics['revenue_plan']['value'])} ({_pct(metrics['revenue_variance_pct']['value'])}). Bank cash closed at {_money(metrics['bank_closing_cash']['value'])}, equal to {metrics['cash_runway_months']['value']:.2f} months of current operating expenses. The binding capacity constraint was {bottleneck['department']}, at {bottleneck['throughput_units']} units against capacity of {bottleneck['capacity_units']}. There are {len(report['alerts'])} active rule-based alerts and {len(report['reconciliation_breaks'])} reconciliation breaks.",
        "",
        "## Cash and revenue",
        "",
        "| Measure | Actual | Plan or comparison | Evidence reference |",
        "| --- | ---: | ---: | --- |",
        f"| Revenue | {_money(metrics['revenue_actual']['value'])} | Plan {_money(metrics['revenue_plan']['value'])}, variance {_pct(metrics['revenue_variance_pct']['value'])} | `revenue_actual`, `revenue_plan` |",
        f"| Operating expenses | {_money(metrics['expense_actual']['value'])} | Plan {_money(metrics['expense_plan']['value'])} | `expense_actual`, `expense_plan` |",
        f"| Bank closing cash | {_money(metrics['bank_closing_cash']['value'])} | Books {_money(metrics['book_closing_cash']['value'])} | `bank_closing_cash`, `book_closing_cash` |",
        f"| Cash coverage | {metrics['cash_runway_months']['value']:.2f} months | Alert threshold: below 3.00 months | `cash_runway_months` |",
        "",
        "Cash coverage is bank closing cash divided by the current month's actual operating expenses. This is a simple coverage measure, not a forecast of future receipts.",
        "",
    ]
    if previous:
        prev = previous["metrics"]
        cash_delta = metrics["bank_closing_cash"]["value"] - prev["bank_closing_cash"]["value"]
        revenue_delta = metrics["revenue_actual"]["value"] - prev["revenue_actual"]["value"]
        backlog_delta = metrics["support_backlog_close"]["value"] - prev["support_backlog_close"]["value"]
        lines += [
            "### Month over month",
            "",
            "| Measure | Previous month | Current month | Change | Evidence reference |",
            "| --- | ---: | ---: | ---: | --- |",
            f"| Bank closing cash | {_money(prev['bank_closing_cash']['value'])} | {_money(metrics['bank_closing_cash']['value'])} | {_money(cash_delta)} | `previous.bank_closing_cash`, `bank_closing_cash` |",
            f"| Revenue | {_money(prev['revenue_actual']['value'])} | {_money(metrics['revenue_actual']['value'])} | {_money(revenue_delta)} | `previous.revenue_actual`, `revenue_actual` |",
            f"| Support closing backlog | {prev['support_backlog_close']['value']} | {metrics['support_backlog_close']['value']} | {backlog_delta:+d} tickets | `previous.support_backlog_close`, `support_backlog_close` |",
            "",
        ]
    lines += ["## Reconciliation breaks", ""]
    if report["reconciliation_breaks"]:
        lines += [
            "| Break | Amount / difference | Records involved |",
            "| --- | ---: | --- |",
        ]
        for item in report["reconciliation_breaks"]:
            lines.append(f"| {item['kind']} `{item['break_id']}` | {_money(item['amount'])} | {', '.join(item['record_ids'])} |")
    else:
        lines.append("No cash or sales-to-finance reconciliation breaks were found in the ingested records.")
    lines += ["", "Plan to actual department variances are shown in Department summaries. Each row has plan and actual record IDs in the evidence index.", "", "## Alerts", ""]
    if report["alerts"]:
        lines += [
            "| Alert | Rule and threshold | Observed | Severity | Evidence records |",
            "| --- | --- | ---: | --- | --- |",
        ]
        for alert in report["alerts"]:
            if alert["unit"] == "fraction":
                threshold = f"{alert['operator']} {alert['threshold'] * 100:.0f}%"
                observed = f"{alert['observed_value'] * 100:.2f}%"
            else:
                threshold = f"{alert['operator']} {alert['threshold']:.2f} {alert['unit']}"
                observed = f"{alert['observed_value']:.2f} {alert['unit']}"
            lines.append(f"| `{alert['alert_id']}` {alert['title']} | `{alert['rule_id']}`, {threshold} | {observed} | {alert['severity']} | {', '.join(alert['evidence_record_ids'])} |")
            lines.append(f"  - Action: {alert['action']}")
    else:
        lines.append("No configured alert rules breached their thresholds this month.")
    lines += [
        "",
        "Every alert has a stable ID. Use `operator trace ALERT-ID` to inspect its threshold, observed value, and source records.",
        "",
        "## Capacity and bottleneck",
        "",
        f"The highest utilized department is **{bottleneck['department']}** at {bottleneck['utilization'] * 100:.1f}% utilization, with {bottleneck['throughput_units']} throughput units against {bottleneck['capacity_units']} capacity units. The capacity alert threshold is above {report['capacity']['threshold'] * 100:.0f}%. Source record: `{bottleneck['id']}`.",
        "",
        "| Department | Capacity | Throughput | Utilization | Source record |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in report["capacity"]["departments"]:
        lines.append(f"| {row['department']} | {row['capacity_units']} | {row['throughput_units']} | {row['utilization'] * 100:.1f}% | `{row['id']}` |")
    lines += ["", "## Department summaries", "", "| Department | Revenue actual / plan | Revenue variance | Expense actual / plan | Headcount actual / plan | Top risks |", "| --- | ---: | ---: | ---: | ---: | --- |"]
    for row in report["department_summaries"]:
        risks_for_dept = [alert["rule_id"] for alert in report["alerts"] if alert["department"] == row["department"]]
        lines.append(f"| {row['department']} | {_money(row['revenue_actual'])} / {_money(row['revenue_plan'])} | {_pct(row['revenue_variance_pct'])} | {_money(row['expense_actual'])} / {_money(row['expense_plan'])} | {row['headcount_actual']} / {row['headcount_plan']} | {', '.join(risks_for_dept) if risks_for_dept else 'No rule alert'} |")
    lines += ["", "Department revenue and expense values resolve to the paired plan and actual record IDs listed in the evidence index.", "", "## Risk register", ""]
    if risks:
        lines += ["Risk scores are likelihood multiplied by impact, each rated from 1 to 5.", "", "| Risk | Likelihood | Impact | Score | Owner | First seen | Last seen | Status |", "| --- | ---: | ---: | ---: | --- | --- | --- | --- |"]
        for risk in risks:
            lines.append(f"| {risk['title']} (`{risk['risk_id']}`) | {risk['likelihood']} | {risk['impact']} | {risk['score']} | {risk['owner']} | {risk['first_month']} | {risk['last_month']} | {risk['status']} |")
    else:
        lines.append("No open risks are registered.")
    lines += ["", "## Outstanding decisions", ""]
    if decisions:
        for decision in decisions:
            lines.append(f"- **{decision['title']}** (`{decision['decision_id']}`, owner: {decision['owner']}, opened: {decision['created_month']}). {decision['context']} Options: {'; '.join(decision['options'])}.")
    else:
        lines.append("No pending decisions.")
    lines += ["", "## Action list", ""]
    if actions:
        for action in actions:
            state = "overdue, escalated" if action["escalated"] else f"due {action['due_month']}"
            lines.append(f"- **{action['title']}** (`{action['action_id']}`, owner: {action['owner']}, {state}; opened {action['created_month']}).")
    else:
        lines.append("No open actions.")
    lines += ["", *_evidence_index(report, previous), "", "This management pack is generated entirely from **synthetic company data**. No company figures are real.", ""]
    return "\n".join(lines)


def build_brief(company: dict[str, Any], report: dict[str, Any], previous: dict[str, Any] | None, decisions: list[dict[str, Any]]) -> str:
    month = report["month"]
    metrics = report["metrics"]
    bottleneck = report["capacity"]["binding_constraint"]
    alerts_by_rule = {alert["rule_id"]: alert for alert in report["alerts"]}
    support = report["support"]
    lines = [
        f"# Spoken management briefing: {month}",
        "",
        f"**SYNTHETIC DATA** | {company['name']} | For the COO",
        "",
        "## The three things that matter",
        "",
        f"1. Cash closed at {_money(metrics['bank_closing_cash']['value'])}, covering {metrics['cash_runway_months']['value']:.2f} months of current operating expenses. The cash alert threshold is below 3.00 months. This measure divides closing cash by this month's actual operating expenses.",
        f"2. {bottleneck['department'].title()} is the most utilized department at {bottleneck['utilization'] * 100:.1f}%, with {bottleneck['throughput_units']} units completed against capacity of {bottleneck['capacity_units']}. The alert threshold is above 90%.",
        f"3. Support backlog ended at {support['closing_backlog']} tickets, compared with {support['opening_backlog']} at the start of the month. It changed by {support['growth_pct'] * 100:+.1f}% during the month.",
        "",
        "## What needs a decision",
        "",
    ]
    if decisions:
        for decision in decisions:
            lines.append(f"- **{decision['title']}**: {decision['context']} Options are {'; '.join(decision['options'])}. Owner: {decision['owner']}. Decision ID: `{decision['decision_id']}`.")
    else:
        lines.append("- No pending decisions are recorded.")
    lines += ["", "## What changed since last month", ""]
    if previous:
        prior = previous["metrics"]
        cash_delta = metrics["bank_closing_cash"]["value"] - prior["bank_closing_cash"]["value"]
        revenue_delta = metrics["revenue_actual"]["value"] - prior["revenue_actual"]["value"]
        backlog_delta = support["closing_backlog"] - prior["support_backlog_close"]["value"]
        lines += [
            f"- Bank closing cash changed by {_money(cash_delta)}, from {_money(prior['bank_closing_cash']['value'])} to {_money(metrics['bank_closing_cash']['value'])}.",
            f"- Revenue changed by {_money(revenue_delta)}, from {_money(prior['revenue_actual']['value'])} to {_money(metrics['revenue_actual']['value'])}.",
            f"- Support closing backlog changed by {backlog_delta:+d} tickets, from {prior['support_backlog_close']['value']} to {support['closing_backlog']}.",
        ]
    else:
        lines.append("- No earlier month is ingested, so there is no month-over-month comparison.")
    lines += ["", "## Pack references", "", f"Revenue was {_money(metrics['revenue_actual']['value'])} against plan of {_money(metrics['revenue_plan']['value'])}, variance {_pct(metrics['revenue_variance_pct']['value'])}. Alert rules fired: {', '.join(alert['rule_id'] for alert in report['alerts']) if report['alerts'] else 'none'}.", "", "This briefing is derived from the monthly management pack and uses **synthetic company data** only.", ""]
    return "\n".join(lines)


def write_reports(db_path: str | Path, data_dir: str | Path, month: str, out_dir: str | Path) -> dict[str, Path]:
    import json
    company = json.loads((Path(data_dir) / "company.json").read_text(encoding="utf-8"))
    current_data = get_month_data(db_path, month)
    report = analyze_month(current_data)
    prior_data = _previous_month_data(db_path, month)
    previous = analyze_month(prior_data) if prior_data else None
    risks = list_risks(db_path)
    decisions = list_decisions(db_path)
    actions = list_actions(db_path)
    markdown = build_pack(company, report, previous, risks, decisions, actions)
    brief = build_brief(company, report, previous, decisions)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    md_path = output / f"management-pack-{month}.md"
    html_path = output / f"management-pack-{month}.html"
    brief_path = output / f"management-brief-{month}.md"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown, f"Synthetic management pack {month}"), encoding="utf-8")
    brief_path.write_text(brief, encoding="utf-8")
    return {"markdown": md_path, "html": html_path, "brief": brief_path}


def write_brief(db_path: str | Path, data_dir: str | Path, month: str, out_dir: str | Path) -> Path:
    import json
    company = json.loads((Path(data_dir) / "company.json").read_text(encoding="utf-8"))
    report = analyze_month(get_month_data(db_path, month))
    prior_data = _previous_month_data(db_path, month)
    previous = analyze_month(prior_data) if prior_data else None
    brief = build_brief(company, report, previous, list_decisions(db_path))
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"management-brief-{month}.md"
    path.write_text(brief, encoding="utf-8")
    return path
