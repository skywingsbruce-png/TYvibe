"""Tests for the hard risk-rules engine (ALLOW / WATCH / BLOCK)."""

from __future__ import annotations

from datetime import date

from src.options_studio.classification import classify
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
)
from src.options_studio.risk_engine import analyze_portfolio
from src.options_studio.rules_engine import Severity, evaluate, load_rules

AS_OF = date(2026, 1, 15)
FAR = date(2026, 6, 19)   # dte ~155
NEAR = date(2026, 1, 23)  # dte 8
TODAY = AS_OF             # dte 0


def _debit_spread(underlying, expiry, long_cost=3.0, short_cost=1.0):
    return [
        OptionContract(underlying, OptionRight.CALL, 100, expiry, 100, 1, average_cost=long_cost),
        OptionContract(underlying, OptionRight.CALL, 105, expiry, 100, -1, average_cost=short_cost),
    ]


def _portfolio(options):
    snap = PortfolioSnapshot(
        account_label="acct-test",
        as_of=AS_OF,
        base_currency="USD",
        cash=0.0,
        buying_power=None,
        options=tuple(options),
    )
    return analyze_portfolio(classify(snap))


def _by_id(report):
    return {v.rule_id: v for v in report.verdicts}


def test_clean_diversified_book_allows():
    # Three unrelated fictional tickers, small losses, far-dated => ALLOW.
    options = _debit_spread("ZAA", FAR) + _debit_spread("ZBB", FAR) + _debit_spread("ZCC", FAR)
    report = evaluate(_portfolio(options), load_rules())
    assert report.overall == Severity.ALLOW


def test_per_trade_loss_over_cap_blocks():
    # Debit 8 => max loss 800 > 500 cap.
    options = _debit_spread("ZAA", FAR, long_cost=9.0, short_cost=1.0)
    report = evaluate(_portfolio(options), load_rules())
    assert _by_id(report)["per_trade_max_risk"].severity == Severity.BLOCK
    assert report.overall == Severity.BLOCK


def test_naked_short_call_blocks_on_unbounded_loss():
    naked = [OptionContract("ZAA", OptionRight.CALL, 100, FAR, 100, -1, average_cost=5.0)]
    report = evaluate(_portfolio(naked), load_rules())
    assert _by_id(report)["per_trade_max_risk"].severity == Severity.BLOCK


def test_near_dte_triggers_watch():
    options = _debit_spread("ZAA", FAR) + _debit_spread("ZBB", NEAR)
    report = evaluate(_portfolio(options), load_rules())
    assert _by_id(report)["near_dte_warning"].severity == Severity.WATCH


def test_zero_dte_blocks():
    options = _debit_spread("ZAA", TODAY)
    report = evaluate(_portfolio(options), load_rules())
    assert _by_id(report)["zero_dte_limit"].severity == Severity.BLOCK
    assert report.overall == Severity.BLOCK


def test_theme_concentration_flags_correlated_names():
    # MU + SNDK are both in the memory_semiconductor theme => combined 100%.
    options = _debit_spread("MU", FAR) + _debit_spread("SNDK", FAR)
    report = evaluate(_portfolio(options), load_rules())
    assert _by_id(report)["theme_concentration"].severity == Severity.WATCH


def test_real_trading_request_fails_closed():
    options = _debit_spread("ZAA", FAR)
    report = evaluate(_portfolio(options), load_rules(), real_trading_requested=True)
    rt = _by_id(report)["real_trading_forbidden"]
    assert rt.severity == Severity.BLOCK
    assert report.overall == Severity.BLOCK
