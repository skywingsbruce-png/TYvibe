"""Tests for the read-only Options Studio API route + bundled sample."""

from __future__ import annotations

from src.api.options_studio_routes import _SAMPLE_STATEMENT, _load_statement
from src.options_studio.reconciliation import run_reconciliation


def test_bundled_sample_exists_and_is_clean():
    assert _SAMPLE_STATEMENT.exists()
    result = run_reconciliation(_SAMPLE_STATEMENT.read_text(encoding="utf-8"))
    # The bundled sample must parse cleanly so the dashboard demo is trustworthy.
    assert len(result.warnings) == 0
    assert result.portfolio_risk.unclassified_strategies == 0
    payload = result.to_payload()
    assert set(payload) >= {"snapshot", "portfolio_risk", "rules", "warnings"}
    assert payload["rules"]["overall"] in {"ALLOW", "WATCH", "BLOCK"}


def test_load_statement_falls_back_to_sample_when_no_real_file(tmp_path, monkeypatch):
    # Point the private dir at an empty tmp so no real statement is found.
    import src.options_studio.storage as storage

    monkeypatch.setattr(storage, "get_private_dir", lambda: tmp_path)
    text, source = _load_statement()
    assert source == "sample"
    assert "FICTIONAL SAMPLE" in text


def test_review_payload_is_deidentified():
    result = run_reconciliation(_SAMPLE_STATEMENT.read_text(encoding="utf-8"))
    blob = str(result.to_payload())
    assert "U0000000-FICTIONAL" not in blob
    assert "SAMPLE ONLY" not in blob
