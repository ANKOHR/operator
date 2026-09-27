"""Command-line interface for Operator."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .analysis import analyze_month, trace_alert
from .company import generate_company
from .ingest import (
    available_months,
    close_action,
    connect,
    create_decision,
    get_month_data,
    hydrate_through,
    ingest_month,
    list_actions,
    list_decisions,
    list_quarantine,
    list_risks,
    load_month_file,
    resolve_decision,
)
from .reports import write_brief, write_reports


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _add_storage_args(parser: argparse.ArgumentParser, with_data: bool = True) -> None:
    if with_data:
        parser.add_argument("--data", default="data", help="synthetic company data directory")
    parser.add_argument("--db", default="state/operator.sqlite", help="SQLite state database path")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="operator", description="Monthly operating system for a synthetic company")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate-company", help="generate reproducible synthetic monthly source data")
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--out", default="data")

    ingest = sub.add_parser("ingest-month", help="validate and load one synthetic month into SQLite")
    ingest.add_argument("--month", required=True)
    _add_storage_args(ingest)

    pack = sub.add_parser("pack", help="write the monthly pack and spoken briefing")
    pack.add_argument("--month", required=True)
    _add_storage_args(pack)
    pack.add_argument("--out", default="reports")

    alert = sub.add_parser("alerts", help="list the deterministic rule alerts for a month")
    alert.add_argument("--month", required=True)
    _add_storage_args(alert)

    trace = sub.add_parser("trace", help="show the rule and source records behind an alert")
    trace.add_argument("alert_id")
    _add_storage_args(trace)

    decision = sub.add_parser("decisions", help="list, create, or resolve stateful decisions")
    decision_sub = decision.add_subparsers(dest="operation")
    decision_list = decision_sub.add_parser("list")
    _add_storage_args(decision_list, with_data=False)
    decision_list.add_argument("--all", action="store_true")
    decision_create = decision_sub.add_parser("create")
    _add_storage_args(decision_create, with_data=False)
    decision_create.add_argument("--month", required=True)
    decision_create.add_argument("--title", required=True)
    decision_create.add_argument("--context", required=True)
    decision_create.add_argument("--owner", required=True)
    decision_create.add_argument("--option", action="append", default=[])
    decision_resolve = decision_sub.add_parser("resolve")
    _add_storage_args(decision_resolve, with_data=False)
    decision_resolve.add_argument("--id", required=True)

    action = sub.add_parser("actions", help="list or close stateful actions")
    action_sub = action.add_subparsers(dest="operation")
    action_list = action_sub.add_parser("list")
    _add_storage_args(action_list, with_data=False)
    action_list.add_argument("--all", action="store_true")
    action_close = action_sub.add_parser("close")
    _add_storage_args(action_close, with_data=False)
    action_close.add_argument("--id", required=True)

    risks = sub.add_parser("risks", help="list the current risk register")
    risks.add_argument("--db", default="state/operator.sqlite")
    risks.add_argument("--all", action="store_true")

    brief = sub.add_parser("brief", help="write the one-page spoken management briefing")
    brief.add_argument("--month", required=True)
    _add_storage_args(brief)
    brief.add_argument("--out", default="reports")

    quarantine = sub.add_parser("quarantine", help="show invalid rows and their reasons")
    quarantine.add_argument("--month", required=True)
    quarantine.add_argument("--db", default="state/operator.sqlite")
    return parser


def _default_decisions(args: argparse.Namespace) -> None:
    args.operation = args.operation or "list"
    if args.operation == "list":
        args.db = getattr(args, "db", "state/operator.sqlite")
        args.all = getattr(args, "all", False)


def run(args: argparse.Namespace) -> int:
    if args.command == "generate-company":
        company = generate_company(args.seed, args.out)
        _json({"company": company["name"], "synthetic": company["synthetic"], "months": len(available_months(args.out)), "output": str(Path(args.out))})
        return 0
    if args.command == "ingest-month":
        result = ingest_month(args.db, load_month_file(args.data, args.month))
        _json(result)
        return 0
    if args.command in {"pack", "brief", "alerts"}:
        hydrate_through(args.db, args.data, args.month)
        if args.command == "pack":
            paths = write_reports(args.db, args.data, args.month, args.out)
            _json({"synthetic": True, **{key: str(value) for key, value in paths.items()}})
        elif args.command == "brief":
            _json({"synthetic": True, "brief": str(write_brief(args.db, args.data, args.month, args.out))})
        else:
            _json({"synthetic": True, "month": args.month, "alerts": analyze_month(get_month_data(args.db, args.month))["alerts"]})
        return 0
    if args.command == "trace":
        match = re.match(r"^ALT-(\d{4}-\d{2})-", args.alert_id)
        if not match:
            raise ValueError("alert ID must use the form ALT-YYYY-MM-RULE_ID")
        month = match.group(1)
        hydrate_through(args.db, args.data, month)
        _json({"synthetic": True, **trace_alert(get_month_data(args.db, month), args.alert_id)})
        return 0
    if args.command == "decisions":
        _default_decisions(args)
        if args.operation == "list":
            _json({"synthetic": True, "decisions": list_decisions(args.db, outstanding_only=not args.all)})
        elif args.operation == "create":
            decision_id = create_decision(args.db, args.month, args.title, args.context, args.option, args.owner)
            _json({"synthetic": True, "decision_id": decision_id, "status": "pending"})
        elif args.operation == "resolve":
            resolve_decision(args.db, args.id)
            _json({"synthetic": True, "decision_id": args.id, "status": "resolved"})
        return 0
    if args.command == "actions":
        args.operation = args.operation or "list"
        if args.operation == "list":
            _json({"synthetic": True, "actions": list_actions(args.db, outstanding_only=not args.all)})
        else:
            close_action(args.db, args.id)
            _json({"synthetic": True, "action_id": args.id, "status": "closed"})
        return 0
    if args.command == "risks":
        _json({"synthetic": True, "risks": list_risks(args.db, outstanding_only=not args.all)})
        return 0
    if args.command == "quarantine":
        _json({"synthetic": True, "month": args.month, "quarantine": list_quarantine(args.db, args.month)})
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"operator: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
