"""Schema validation, quarantine, and SQLite-backed monthly state."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

from .company import DEPARTMENTS

COLLECTIONS = {
    "finance.bank_opening": ("finance", "bank_opening", {"id", "amount"}, {"amount"}),
    "finance.book_opening": ("finance", "book_opening", {"id", "amount"}, {"amount"}),
    "finance.bank_movements": ("finance", "bank_movements", {"id", "reference", "amount"}, {"amount"}),
    "finance.book_movements": ("finance", "book_movements", {"id", "reference", "amount"}, {"amount"}),
    "finance.revenue_plan": ("finance", "revenue_plan", {"id", "department", "amount"}, {"amount"}),
    "finance.revenue_actual": ("finance", "revenue_actual", {"id", "department", "amount"}, {"amount"}),
    "finance.expense_plan": ("finance", "expense_plan", {"id", "department", "amount"}, {"amount"}),
    "finance.expense_actual": ("finance", "expense_actual", {"id", "department", "amount"}, {"amount"}),
    "people": (None, "people", {"id", "department", "plan", "actual"}, {"plan", "actual"}),
    "sales": (None, "sales", {"id", "department", "recognized_revenue", "stage", "source_ref"}, {"recognized_revenue"}),
    "delivery": (None, "delivery", {"id", "department", "capacity_units", "throughput_units"}, {"capacity_units", "throughput_units"}),
    "support": (None, "support", {"id", "opening_backlog", "new_tickets", "closed_tickets"}, {"opening_backlog", "new_tickets", "closed_tickets"}),
}


def validate_month(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return valid rows and quarantined rows with explicit reasons."""
    if not isinstance(raw, dict):
        raise ValueError("monthly source must be a JSON object")
    month = raw.get("month")
    if not isinstance(month, str) or len(month) != 7 or month[4:5] != "-":
        raise ValueError("month must use YYYY-MM format")
    if raw.get("synthetic") is not True:
        raise ValueError("source month must be explicitly labelled synthetic")
    if not isinstance(raw.get("finance"), dict):
        raise ValueError("finance source is required")

    clean = deepcopy(raw)
    quarantined: list[dict[str, Any]] = []
    for path, (parent, key, required, numeric_fields) in COLLECTIONS.items():
        container = clean if parent is None else clean[parent]
        source_rows = container.get(key)
        if not isinstance(source_rows, list):
            raise ValueError(f"{path} must be a list")
        accepted = []
        seen_ids: set[str] = set()
        for index, row in enumerate(source_rows):
            reason = None
            if not isinstance(row, dict):
                reason = "row must be a JSON object"
            elif not required.issubset(row.keys()):
                reason = "missing required field: " + ", ".join(sorted(required - row.keys()))
            elif not isinstance(row.get("id"), str) or not row["id"].strip():
                reason = "id must be a non-empty string"
            elif row["id"] in seen_ids:
                reason = "duplicate id within source collection"
            elif any(isinstance(row.get(field), bool) or not isinstance(row.get(field), int) for field in numeric_fields):
                reason = "numeric fields must be integers"
            elif any(row.get(field, 0) < 0 for field in numeric_fields if not (field == "amount" and ".bank_movements" in path or field == "amount" and ".book_movements" in path)):
                reason = "count and amount fields must be non-negative"
            elif "department" in required and row.get("department") not in DEPARTMENTS:
                reason = "unknown department"
            elif "capacity_units" in required and row.get("capacity_units", 0) == 0:
                reason = "capacity_units must be greater than zero"
            elif "probability_pct" in row and not 0 <= row["probability_pct"] <= 100:
                reason = "probability_pct must be between 0 and 100"
            elif "reference" in required and not isinstance(row.get("reference"), str):
                reason = "reference must be a string"
            elif "source_ref" in required and not isinstance(row.get("source_ref"), str):
                reason = "source_ref must be a string"
            if reason:
                quarantined.append({"source": path, "index": index, "record_id": row.get("id") if isinstance(row, dict) else None, "reason": reason, "raw": row})
                continue
            seen_ids.add(row["id"])
            accepted.append(row)
        container[key] = accepted
    if len(clean["finance"]["bank_opening"]) != 1 or len(clean["finance"]["book_opening"]) != 1:
        raise ValueError("exactly one bank opening and one book opening balance are required")
    return clean, quarantined


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS months (
            month TEXT PRIMARY KEY,
            synthetic INTEGER NOT NULL CHECK (synthetic = 1),
            data_json TEXT NOT NULL,
            quarantined_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS records (
            month TEXT NOT NULL,
            source TEXT NOT NULL,
            record_id TEXT NOT NULL,
            department TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (month, source, record_id),
            FOREIGN KEY (month) REFERENCES months(month) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS quarantine (
            quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            source TEXT NOT NULL,
            record_id TEXT,
            reason TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            FOREIGN KEY (month) REFERENCES months(month) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS actions (
            action_id TEXT PRIMARY KEY,
            created_month TEXT NOT NULL,
            due_month TEXT NOT NULL,
            owner TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            escalated INTEGER NOT NULL DEFAULT 0,
            last_seen_month TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY,
            created_month TEXT NOT NULL,
            title TEXT NOT NULL,
            context TEXT NOT NULL,
            options_json TEXT NOT NULL,
            owner TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS risks (
            risk_id TEXT PRIMARY KEY,
            first_month TEXT NOT NULL,
            last_month TEXT NOT NULL,
            title TEXT NOT NULL,
            likelihood INTEGER NOT NULL,
            impact INTEGER NOT NULL,
            score INTEGER NOT NULL,
            owner TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
    )
    return connection


def _flatten_records(clean: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for path, (parent, key, _, _) in COLLECTIONS.items():
        container = clean if parent is None else clean[parent]
        rows.extend((path, row) for row in container[key])
    return rows


def load_month_file(data_dir: str | Path, month: str) -> dict[str, Any]:
    path = Path(data_dir) / "months" / f"{month}.json"
    if not path.is_file():
        raise FileNotFoundError(f"No source file found for {month}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def available_months(data_dir: str | Path) -> list[str]:
    root = Path(data_dir) / "months"
    return sorted(path.stem for path in root.glob("????-??.json")) if root.exists() else []


def _next_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    if number == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{number + 1:02d}"


def _state_from_month(connection: sqlite3.Connection, month: str) -> None:
    """Refresh deterministic risks, decisions, and actions from current signals."""
    from .analysis import analyze_month

    data_row = connection.execute("SELECT data_json FROM months WHERE month = ?", (month,)).fetchone()
    if data_row is None:
        return
    report = analyze_month(json.loads(data_row["data_json"]))
    # An open item becomes escalated only after its due month has passed.
    connection.execute(
        "UPDATE actions SET escalated = CASE WHEN due_month < ? THEN 1 ELSE escalated END, last_seen_month = ? WHERE status = 'open'",
        (month, month),
    )
    for alert in report["alerts"]:
        rule_id = alert["rule_id"]
        risk_id = f"RISK-{rule_id}"
        owner = alert["owner"]
        likelihood = 4 if alert["severity"] == "high" else 3
        impact = 5 if rule_id in {"CASH_RUNWAY_LT_3", "REV_PLAN_SHORTFALL_GT_12"} else 4
        title = alert["title"]
        existing = connection.execute("SELECT first_month FROM risks WHERE risk_id = ?", (risk_id,)).fetchone()
        first_month = existing["first_month"] if existing else month
        connection.execute(
            "INSERT INTO risks VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open') "
            "ON CONFLICT(risk_id) DO UPDATE SET last_month=excluded.last_month, title=excluded.title, "
            "likelihood=excluded.likelihood, impact=excluded.impact, score=excluded.score, owner=excluded.owner, status='open'",
            (risk_id, first_month, month, title, likelihood, impact, likelihood * impact, owner),
        )
        action_id = f"ACT-{month}-{rule_id}"
        connection.execute(
            "INSERT OR IGNORE INTO actions VALUES (?, ?, ?, ?, ?, 'open', 0, ?)",
            (action_id, month, _next_month(month), owner, alert["action"], month),
        )
        if alert.get("decision"):
            decision_id = f"DEC-{month}-{rule_id}"
            connection.execute(
                "INSERT OR IGNORE INTO decisions VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (decision_id, month, alert["decision"], alert["context"], json.dumps(alert["options"]), owner),
            )
    active_risks = {f"RISK-{alert['rule_id']}" for alert in report["alerts"]}
    for row in connection.execute("SELECT risk_id FROM risks WHERE status = 'open'").fetchall():
        if row["risk_id"] not in active_risks:
            connection.execute("UPDATE risks SET status = 'closed' WHERE risk_id = ?", (row["risk_id"],))


def ingest_month(db_path: str | Path, raw: dict[str, Any]) -> dict[str, Any]:
    clean, quarantined = validate_month(raw)
    month = clean["month"]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute("DELETE FROM months WHERE month = ?", (month,))
            connection.execute(
                "INSERT INTO months(month, synthetic, data_json, quarantined_count) VALUES (?, 1, ?, ?)",
                (month, json.dumps(clean, sort_keys=True), len(quarantined)),
            )
            for source, record in _flatten_records(clean):
                connection.execute(
                    "INSERT INTO records(month, source, record_id, department, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (month, source, record["id"], record.get("department"), json.dumps(record, sort_keys=True)),
                )
            for row in quarantined:
                connection.execute(
                    "INSERT INTO quarantine(month, source, record_id, reason, raw_json) VALUES (?, ?, ?, ?, ?)",
                    (month, row["source"], row["record_id"], row["reason"], json.dumps(row["raw"], sort_keys=True)),
                )
            _state_from_month(connection, month)
        return {"synthetic": True, "month": month, "records_loaded": len(_flatten_records(clean)), "quarantined": quarantined}
    finally:
        connection.close()


def hydrate_through(db_path: str | Path, data_dir: str | Path, target_month: str) -> None:
    months = available_months(data_dir)
    if target_month not in months:
        raise FileNotFoundError(f"Month {target_month} is not in {data_dir}")
    connection = connect(db_path)
    try:
        existing = {row[0] for row in connection.execute("SELECT month FROM months")}
    finally:
        connection.close()
    for month in months:
        if month > target_month:
            break
        if month not in existing:
            ingest_month(db_path, load_month_file(data_dir, month))


def get_month_data(db_path: str | Path, month: str) -> dict[str, Any]:
    connection = connect(db_path)
    try:
        row = connection.execute("SELECT data_json FROM months WHERE month = ?", (month,)).fetchone()
        if row is None:
            raise KeyError(f"Month {month} has not been ingested")
        return json.loads(row["data_json"])
    finally:
        connection.close()


def list_quarantine(db_path: str | Path, month: str) -> list[dict[str, Any]]:
    connection = connect(db_path)
    try:
        return [dict(row) for row in connection.execute("SELECT source, record_id, reason, raw_json FROM quarantine WHERE month = ? ORDER BY quarantine_id", (month,))]
    finally:
        connection.close()


def list_decisions(db_path: str | Path, outstanding_only: bool = True) -> list[dict[str, Any]]:
    connection = connect(db_path)
    try:
        query = "SELECT * FROM decisions" + (" WHERE status = 'pending'" if outstanding_only else "") + " ORDER BY created_month, decision_id"
        rows = [dict(row) for row in connection.execute(query)]
        for row in rows:
            row["options"] = json.loads(row.pop("options_json"))
        return rows
    finally:
        connection.close()


def list_actions(db_path: str | Path, outstanding_only: bool = True) -> list[dict[str, Any]]:
    connection = connect(db_path)
    try:
        query = "SELECT * FROM actions" + (" WHERE status = 'open'" if outstanding_only else "") + " ORDER BY created_month, action_id"
        return [dict(row) for row in connection.execute(query)]
    finally:
        connection.close()


def list_risks(db_path: str | Path, outstanding_only: bool = True) -> list[dict[str, Any]]:
    connection = connect(db_path)
    try:
        query = "SELECT * FROM risks" + (" WHERE status = 'open'" if outstanding_only else "") + " ORDER BY score DESC, risk_id"
        return [dict(row) for row in connection.execute(query)]
    finally:
        connection.close()


def close_action(db_path: str | Path, action_id: str) -> None:
    connection = connect(db_path)
    try:
        with connection:
            cursor = connection.execute("UPDATE actions SET status = 'closed' WHERE action_id = ?", (action_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown action: {action_id}")
    finally:
        connection.close()


def create_decision(db_path: str | Path, month: str, title: str, context: str, options: list[str], owner: str) -> str:
    digest = hashlib.sha256((title + "\0" + context + "\0" + owner).encode("utf-8")).hexdigest()[:10]
    decision_id = f"DEC-{month}-MANUAL-{digest}"
    connection = connect(db_path)
    try:
        with connection:
            connection.execute("INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, 'pending')", (decision_id, month, title, context, json.dumps(options), owner))
    finally:
        connection.close()
    return decision_id


def create_action(db_path: str | Path, action_id: str, created_month: str, due_month: str, owner: str, title: str) -> None:
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                "INSERT INTO actions VALUES (?, ?, ?, ?, ?, 'open', 0, ?)",
                (action_id, created_month, due_month, owner, title, created_month),
            )
    finally:
        connection.close()


def resolve_decision(db_path: str | Path, decision_id: str) -> None:
    connection = connect(db_path)
    try:
        with connection:
            cursor = connection.execute("UPDATE decisions SET status = 'resolved' WHERE decision_id = ?", (decision_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown decision: {decision_id}")
    finally:
        connection.close()
