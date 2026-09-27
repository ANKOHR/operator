from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from operator_app.analysis import analyze_month, trace_alert
from operator_app.company import generate_company
from operator_app.ingest import (
    close_action,
    create_action,
    get_month_data,
    ingest_month,
    list_actions,
    load_month_file,
    validate_month,
)
from operator_app.reports import write_reports

ROOT = Path(__file__).resolve().parents[1]


def _generate(path: Path, seed: int = 42) -> None:
    generate_company(seed, path)


def _run_cli(*args: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "operator_app.cli", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_company_generator_is_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _generate(first, 42)
    _generate(second, 42)
    first_files = {path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    second_files = {path.relative_to(second).as_posix(): path.read_bytes() for path in second.rglob("*") if path.is_file()}
    assert first_files == second_files
    company = json.loads((first / "company.json").read_text(encoding="utf-8"))
    assert company["synthetic"] is True
    assert len(list((first / "months").glob("*.json"))) >= 12


def test_reconciliation_catches_planted_breaks_with_registered_amounts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _generate(data_dir)
    register = json.loads((data_dir / "test-fixtures" / "planted_issues.json").read_text(encoding="utf-8"))
    expected = [issue for issue in register["issues"] if issue["kind"] in {"cash_reconciliation", "revenue_reconciliation"}]
    actual = []
    for month_path in sorted((data_dir / "months").glob("*.json")):
        report = analyze_month(json.loads(month_path.read_text(encoding="utf-8")))
        actual.extend(report["reconciliation_breaks"])
    for issue in expected:
        matches = [row for row in actual if row["month"] == issue["month"] and row["kind"] == issue["kind"] and row["amount"] == issue["amount"]]
        assert len(matches) == 1, issue
        assert set(issue["record_ids"]).issubset(matches[0]["record_ids"])
    assert len(actual) == len(expected)


def test_alerts_fire_on_seeded_thresholds_and_stay_silent_on_control_month(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _generate(data_dir)
    control = analyze_month(load_month_file(data_dir, "2026-05"))
    assert control["alerts"] == []

    register = json.loads((data_dir / "test-fixtures" / "planted_issues.json").read_text(encoding="utf-8"))
    planted = [issue for issue in register["issues"] if issue["kind"] == "alert_threshold"]
    for issue in planted:
        report = analyze_month(load_month_file(data_dir, issue["month"]))
        alert = next(row for row in report["alerts"] if row["rule_id"] == issue["rule_id"])
        assert alert["threshold"] == issue["threshold"]
        assert alert["observed_value"] == pytest.approx(issue["observed_value"], abs=1e-6)
        assert set(issue["record_ids"]).issubset(alert["evidence_record_ids"])


def test_month_continuity_escalates_overdue_and_keeps_closed_actions_closed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db = tmp_path / "state.sqlite"
    _generate(data_dir)
    ingest_month(db, load_month_file(data_dir, "2026-06"))
    create_action(db, "ACT-TEST-CARRY", "2026-06", "2026-06", "Operations lead", "Carry this action")
    create_action(db, "ACT-TEST-CLOSED", "2026-06", "2026-06", "Operations lead", "Keep this closed")
    close_action(db, "ACT-TEST-CLOSED")

    ingest_month(db, load_month_file(data_dir, "2026-07"))
    rows = {row["action_id"]: row for row in list_actions(db, outstanding_only=False)}
    assert rows["ACT-TEST-CARRY"]["status"] == "open"
    assert rows["ACT-TEST-CARRY"]["escalated"] == 1
    assert rows["ACT-TEST-CARRY"]["last_seen_month"] == "2026-07"
    assert rows["ACT-TEST-CLOSED"]["status"] == "closed"

    ingest_month(db, load_month_file(data_dir, "2026-08"))
    rows = {row["action_id"]: row for row in list_actions(db, outstanding_only=False)}
    assert rows["ACT-TEST-CARRY"]["status"] == "open"
    assert rows["ACT-TEST-CARRY"]["escalated"] == 1
    assert rows["ACT-TEST-CLOSED"]["status"] == "closed"


def test_pack_outputs_are_deterministic_for_the_same_inputs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _generate(data_dir)
    first_db = tmp_path / "first.sqlite"
    second_db = tmp_path / "second.sqlite"
    # Hydrate through the requested month before writing reports.
    from operator_app.ingest import hydrate_through

    hydrate_through(first_db, data_dir, "2026-08")
    hydrate_through(second_db, data_dir, "2026-08")
    first_paths = write_reports(first_db, data_dir, "2026-08", tmp_path / "reports-one")
    second_paths = write_reports(second_db, data_dir, "2026-08", tmp_path / "reports-two")
    for key in ("markdown", "html", "brief"):
        assert first_paths[key].read_bytes() == second_paths[key].read_bytes()


def test_each_sample_pack_alert_resolves_to_source_records(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db = tmp_path / "state.sqlite"
    _generate(data_dir)
    from operator_app.ingest import hydrate_through

    hydrate_through(db, data_dir, "2026-08")
    report = analyze_month(get_month_data(db, "2026-08"))
    assert report["alerts"]
    for alert in report["alerts"]:
        traced = trace_alert(get_month_data(db, "2026-08"), alert["alert_id"])
        assert traced["alert"]["rule_id"] == alert["rule_id"]
        assert traced["alert"]["threshold"] == alert["threshold"]
        assert traced["alert"]["observed_value"] == alert["observed_value"]
        assert {row["id"] for row in traced["evidence"]} == set(alert["evidence_record_ids"])


def test_ingestion_quarantines_bad_rows_with_reason(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _generate(data_dir)
    raw = load_month_file(data_dir, "2026-05")
    raw["people"].append({"id": "HC-2026-05-invalid", "department": "marketing", "plan": 4, "actual": 3})
    clean, quarantined = validate_month(raw)
    assert len(clean["people"]) == len(raw["people"]) - 1
    assert quarantined[0]["record_id"] == "HC-2026-05-invalid"
    assert quarantined[0]["reason"] == "unknown department"

    result = ingest_month(tmp_path / "state.sqlite", raw)
    assert result["quarantined"][0]["reason"] == "unknown department"


def test_cli_smoke_for_all_subcommands(tmp_path: Path) -> None:
    data_dir = tmp_path / "company"
    db = tmp_path / "state.sqlite"
    reports = tmp_path / "reports"
    generated = json.loads(_run_cli("generate-company", "--seed", "42", "--out", str(data_dir)))
    assert generated["synthetic"] is True

    assert json.loads(_run_cli("ingest-month", "--month", "2026-06", "--data", str(data_dir), "--db", str(db)))["synthetic"] is True
    assert json.loads(_run_cli("alerts", "--month", "2026-05", "--data", str(data_dir), "--db", str(db)))["synthetic"] is True
    assert json.loads(_run_cli("pack", "--month", "2026-08", "--data", str(data_dir), "--db", str(db), "--out", str(reports)))["synthetic"] is True
    assert (reports / "management-pack-2026-08.md").is_file()
    assert (reports / "management-pack-2026-08.html").is_file()
    assert (reports / "management-brief-2026-08.md").is_file()
    assert json.loads(_run_cli("brief", "--month", "2026-08", "--data", str(data_dir), "--db", str(db), "--out", str(reports)))["synthetic"] is True
    assert json.loads(_run_cli("trace", "ALT-2026-08-CASH_RUNWAY_LT_3", "--data", str(data_dir), "--db", str(db)))["synthetic"] is True
    assert json.loads(_run_cli("decisions", "list", "--db", str(db)))["synthetic"] is True
    created = json.loads(_run_cli("decisions", "create", "--month", "2026-08", "--title", "Smoke decision", "--context", "CLI test", "--owner", "COO", "--option", "Proceed", "--db", str(db)))
    _run_cli("decisions", "resolve", "--id", created["decision_id"], "--db", str(db))
    action_output = json.loads(_run_cli("actions", "list", "--db", str(db)))
    assert action_output["synthetic"] is True
    action_rows = action_output["actions"]
    assert action_rows
    _run_cli("actions", "close", "--id", action_rows[0]["action_id"], "--db", str(db))
    assert json.loads(_run_cli("risks", "--db", str(db)))["synthetic"] is True
    assert json.loads(_run_cli("quarantine", "--month", "2026-06", "--db", str(db)))["synthetic"] is True
