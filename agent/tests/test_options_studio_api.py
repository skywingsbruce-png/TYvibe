"""Tests for the read-only Options Studio API route + data-safety semantics."""

from __future__ import annotations

from pathlib import Path

import src.api.options_studio_routes as routes
from src.api.options_studio_routes import (
    _SAMPLE_STATEMENT,
    build_review_payload,
)
from src.options_studio.reconciliation import run_reconciliation

_SAMPLE_TEXT = _SAMPLE_STATEMENT.read_text(encoding="utf-8")


def _point_private_dir(monkeypatch, tmp_path: Path) -> None:
    import src.options_studio.storage as storage

    monkeypatch.setattr(storage, "get_private_dir", lambda: tmp_path)


def test_bundled_sample_exists_and_is_clean():
    assert _SAMPLE_STATEMENT.exists()
    result = run_reconciliation(_SAMPLE_TEXT)
    # The bundled sample must parse cleanly so the dashboard demo is trustworthy.
    assert len(result.warnings) == 0
    assert result.portfolio_risk.unclassified_strategies == 0


def test_sample_returned_only_when_no_real_file(monkeypatch, tmp_path):
    _point_private_dir(monkeypatch, tmp_path)  # empty dir => no real statement
    payload = build_review_payload()
    assert payload["data_source"] == "sample"
    assert payload["risk_usable"] is True
    assert payload["blocking_issues"] == []
    # de-identified
    blob = str(payload)
    assert "U0000000-FICTIONAL" not in blob and "SAMPLE ONLY" not in blob


def test_real_clean_file_is_usable(monkeypatch, tmp_path):
    _point_private_dir(monkeypatch, tmp_path)
    # A clean real file: 100 AAPL shares, no options, no unresolved lines,
    # ending cash present so cash_not_found does not fire.
    (tmp_path / "ibkr_statement.csv").write_text(
        "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Value\n"
        "Open Positions,Data,Summary,Stocks,USD,AAPL,100,1,150,15000\n"
        "Cash Report,Header,Currency Summary,Currency,Total\n"
        "Cash Report,Data,Ending Cash,USD,20000\n",
        encoding="utf-8",
    )
    payload = build_review_payload()
    assert payload["data_source"] == "real"
    assert payload["risk_usable"] is True
    assert payload["blocking_issues"] == []


def test_real_with_unresolved_option_is_not_usable_and_no_sample_fallback(monkeypatch, tmp_path):
    _point_private_dir(monkeypatch, tmp_path)
    # A real file with an unparseable option line => unresolved_option warning.
    (tmp_path / "ibkr_statement.csv").write_text(
        "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Value\n"
        "Open Positions,Data,Summary,Stocks,USD,AAPL,100,1,150,15000\n"
        "Open Positions,Data,Summary,Equity and Index Options,USD,MYSTERY9,1,100,1.00,100\n"
        "Cash Report,Header,Currency Summary,Currency,Total\n"
        "Cash Report,Data,Ending Cash,USD,20000\n",
        encoding="utf-8",
    )
    payload = build_review_payload()
    assert payload["data_source"] == "real"  # never falls back to sample
    assert payload["risk_usable"] is False
    codes = {b["code"] for b in payload["blocking_issues"]}
    assert "unresolved_option" in codes


def test_real_parse_failure_returns_real_error(monkeypatch, tmp_path):
    _point_private_dir(monkeypatch, tmp_path)
    (tmp_path / "ibkr_statement.csv").write_text("not,a,valid,ibkr,statement\n1,2,3,4,5\n", encoding="utf-8")
    payload = build_review_payload()
    assert payload["data_source"] == "real"
    assert payload["risk_usable"] is False
    assert payload["error"] is not None
    assert payload["snapshot"] is None


def test_cash_not_found_blocks_risk(monkeypatch, tmp_path):
    _point_private_dir(monkeypatch, tmp_path)
    # A short put with NO Cash Report => cash_not_found => not usable.
    (tmp_path / "ibkr_statement.csv").write_text(
        "Financial Instrument Information,Header,Asset Category,Symbol,Description,Underlying,Multiplier,Expiry,Type,Strike\n"
        "Financial Instrument Information,Data,Equity and Index Options,MU 260220P00090000,MU 20FEB26 90 P,MU,100,2026-02-20,P,90\n"
        "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Value\n"
        "Open Positions,Data,Summary,Equity and Index Options,USD,MU 260220P00090000,-1,100,3.00,-300\n",
        encoding="utf-8",
    )
    payload = build_review_payload()
    assert payload["data_source"] == "real"
    assert payload["risk_usable"] is False
    assert "cash_not_found" in {b["code"] for b in payload["blocking_issues"]}


def test_sample_derived_from_csv_no_duplicate_json():
    # The single source of truth is the sample CSV; there must be no committed
    # duplicate JSON artifact in the frontend.
    repo_root = Path(routes.__file__).resolve().parents[3]
    assert not (repo_root / "frontend" / "public" / "options-studio-sample.json").exists()
