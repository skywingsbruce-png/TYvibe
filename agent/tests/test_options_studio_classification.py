"""Tests for deterministic strategy classification."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.options_studio.broker import parse_statement
from src.options_studio.classification import classify
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
    StrategyType,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "options_studio"
AS_OF = date(2026, 1, 15)


def _opt(underlying, right, strike, expiry, qty, cost=1.0, mult=100):
    return OptionContract(
        underlying=underlying,
        right=right,
        strike=strike,
        expiry=expiry,
        multiplier=mult,
        quantity=qty,
        average_cost=cost,
    )


def _snapshot(options=(), underlyings=(), cash=0.0):
    return PortfolioSnapshot(
        account_label="acct-test",
        as_of=AS_OF,
        base_currency="USD",
        cash=cash,
        buying_power=None,
        underlyings=tuple(underlyings),
        options=tuple(options),
    )


def _types(snapshot):
    return sorted(s.strategy_type for s in snapshot.strategies)


def test_fixture_classification_covers_all_recognized_types():
    result = parse_statement((FIXTURES / "ibkr_activity_sample.csv").read_text(encoding="utf-8"))
    classified = classify(result.snapshot)
    types = {s.strategy_type for s in classified.strategies}
    assert StrategyType.VERTICAL_DEBIT_SPREAD in types   # MSFT
    assert StrategyType.VERTICAL_CREDIT_SPREAD in types   # SNDK
    assert StrategyType.CASH_SECURED_PUT in types         # MU (cash covers)
    assert StrategyType.COVERED_CALL in types             # AAPL short call + shares
    assert StrategyType.LEAPS in types                    # GOOGL long-dated call
    assert StrategyType.LONG_STOCK in types               # residual AAPL shares
    # No strategy is ever left without a name unless truly unclassifiable.
    assert StrategyType.UNCLASSIFIED not in types


def test_cash_secured_put_requires_cash_else_short_put():
    put = _opt("MU", OptionRight.PUT, 90, date(2026, 2, 20), -1, cost=3.0)
    secured = classify(_snapshot(options=[put], cash=9000))
    assert secured.strategies[0].strategy_type == StrategyType.CASH_SECURED_PUT
    naked = classify(_snapshot(options=[put], cash=100))
    assert naked.strategies[0].strategy_type == StrategyType.SHORT_PUT


def test_vertical_debit_vs_credit_by_premium():
    long_call = _opt("MSFT", OptionRight.CALL, 400, date(2026, 2, 20), 1, cost=15.0)
    short_call = _opt("MSFT", OptionRight.CALL, 420, date(2026, 2, 20), -1, cost=6.0)
    debit = classify(_snapshot(options=[long_call, short_call]))
    assert debit.strategies[0].strategy_type == StrategyType.VERTICAL_DEBIT_SPREAD

    short_put = _opt("SNDK", OptionRight.PUT, 80, date(2026, 1, 23), -1, cost=4.5)
    long_put = _opt("SNDK", OptionRight.PUT, 75, date(2026, 1, 23), 1, cost=2.5)
    credit = classify(_snapshot(options=[short_put, long_put]))
    assert credit.strategies[0].strategy_type == StrategyType.VERTICAL_CREDIT_SPREAD


def test_pmcc_detected_from_diagonal_calls():
    leaps = _opt("NVDA", OptionRight.CALL, 100, date(2027, 6, 18), 1, cost=60.0)
    short = _opt("NVDA", OptionRight.CALL, 160, date(2026, 2, 20), -1, cost=4.0)
    result = classify(_snapshot(options=[leaps, short]))
    assert result.strategies[0].strategy_type == StrategyType.PMCC


def test_leaps_single_long_call():
    leaps = _opt("GOOGL", OptionRight.CALL, 150, date(2027, 6, 18), 1, cost=80.0)
    result = classify(_snapshot(options=[leaps]))
    assert result.strategies[0].strategy_type == StrategyType.LEAPS


def test_naked_short_call_is_short_call():
    sc = _opt("TSLA", OptionRight.CALL, 300, date(2026, 2, 20), -1, cost=5.0)
    result = classify(_snapshot(options=[sc]))
    assert result.strategies[0].strategy_type == StrategyType.SHORT_CALL
