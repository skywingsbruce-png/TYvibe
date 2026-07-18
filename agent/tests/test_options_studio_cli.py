"""Integration tests for the `options-studio reconcile` CLI.

All output is written to pytest's ``tmp_path`` — never into the repo. Fixtures
are entirely fictional.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli import _legacy
from src.options_studio.reconciliation import run_reconciliation

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "options_studio"
ACTIVITY = FIXTURES / "ibkr_activity_sample.csv"


def test_reconcile_cli_writes_two_local_files(tmp_path, capsys):
    rc = _legacy.main(
        [
            "options-studio",
            "reconcile",
            "--file",
            str(ACTIVITY),
            "--out-dir",
            str(tmp_path),
            "--account-label",
            "acct-test",
        ]
    )
    assert rc == 0
    snapshot = tmp_path / "portfolio_snapshot.json"
    report = tmp_path / "reconciliation_report.md"
    assert snapshot.exists()
    assert report.exists()

    # Snapshot JSON is well-formed and de-identified.
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["snapshot"]["source_label"] == "ibkr_activity_csv"
    assert payload["snapshot"]["account_label"] == "acct-test"
    blob = snapshot.read_text(encoding="utf-8") + report.read_text(encoding="utf-8")
    assert "U0000000-FICTIONAL" not in blob
    assert "SAMPLE ONLY" not in blob


def test_reconcile_cli_no_write_writes_nothing(tmp_path):
    rc = _legacy.main(
        ["options-studio", "reconcile", "--file", str(ACTIVITY), "--out-dir", str(tmp_path), "--no-write"]
    )
    assert rc == 0
    assert not (tmp_path / "portfolio_snapshot.json").exists()
    assert not (tmp_path / "reconciliation_report.md").exists()


def test_reconcile_cli_missing_file_returns_error(tmp_path):
    rc = _legacy.main(
        ["options-studio", "reconcile", "--file", str(tmp_path / "nope.csv"), "--out-dir", str(tmp_path)]
    )
    assert rc == 2


def test_report_has_required_sections_and_block_verdict():
    result = run_reconciliation(ACTIVITY.read_text(encoding="utf-8"), account_label="acct-test")
    md = result.report_markdown
    for heading in (
        "## 1. Parse summary",
        "## 2. Underlyings & expiries",
        "## 3. Recognized strategies",
        "## 4. Warnings",
        "## 5. Risk rules",
        "## 7. Cannot be verified from this statement",
    ):
        assert heading in md
    # Unresolved option surfaced as a warning, not guessed.
    assert "unresolved_option" in md
    # Conservative per-trade cap => this book blocks.
    assert result.rules_report.overall.label == "BLOCK"
    # Greeks unavailable under the default Null provider (no fabrication).
    assert all(not s.greeks.available for s in result.portfolio_risk.strategies)


def test_duplicate_trade_detected():
    # Two identical trade fills => duplicate_trade warning.
    csv = (
        "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,Comm/Fee\n"
        'Trades,Data,Order,Stocks,USD,AAPL,"2026-01-05, 10:31:00",100,150.00,-1.00\n'
        'Trades,Data,Order,Stocks,USD,AAPL,"2026-01-05, 10:31:00",100,150.00,-1.00\n'
        "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Value\n"
        "Open Positions,Data,Summary,Stocks,USD,AAPL,100,1,150,15000\n"
    )
    result = run_reconciliation(csv)
    codes = {w.code for w in result.warnings}
    assert "duplicate_trade" in codes
