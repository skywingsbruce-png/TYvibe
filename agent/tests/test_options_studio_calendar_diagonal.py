"""Tests for calendar/diagonal call classification, approximate diagonal payoff,
assignment/margin rule, and currency labels (all fictional data)."""

from __future__ import annotations

from datetime import date

from src.options_studio.classification import classify
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
    StrategyType,
)
from src.options_studio.reconciliation import run_reconciliation
from src.options_studio.risk_engine import analyze_portfolio
from src.options_studio.rules_engine import RuleMode, Severity, evaluate, load_rules

AS_OF = date(2026, 7, 20)


def _opt(right, strike, expiry, qty, cost, mult=100):
    return OptionContract("PDD", right, strike, expiry, mult, qty, average_cost=cost)


def _snap(options, cash=0.0):
    return PortfolioSnapshot(
        account_label="acct-test",
        as_of=AS_OF,
        base_currency="USD",
        cash=cash,
        buying_power=None,
        options=tuple(options),
    )


def test_pdd_same_strike_calendar_is_not_naked_short():
    # PDD: long 2026-12-18 C110, short 2026-09-18 C110 (short expires first).
    snap = classify(
        _snap([
            _opt(OptionRight.CALL, 110, date(2026, 12, 18), 1, 15.0),
            _opt(OptionRight.CALL, 110, date(2026, 9, 18), -1, 6.0),
        ])
    )
    assert len(snap.strategies) == 1
    assert snap.strategies[0].strategy_type == StrategyType.CALENDAR_CALL_SPREAD

    risk = analyze_portfolio(snap)
    s = risk.strategies[0]
    assert s.payoff.approximate is True
    assert s.payoff.available is False
    assert s.payoff.unbounded_loss is False  # covered, NOT an unbounded naked short
    assert s.assignment_or_margin_risk is True
    assert risk.unbounded_loss_strategies == 0
    assert risk.approximate_risk_strategies == 1

    report = evaluate(risk, load_rules(), mode=RuleMode.REVIEW)
    per_trade = next(v for v in report.verdicts if v.rule_id == "per_trade_max_risk")
    assign = next(v for v in report.verdicts if v.rule_id == "assignment_or_margin_risk")
    assert per_trade.severity != Severity.BLOCK
    assert assign.severity == Severity.WATCH
    assert report.overall != Severity.BLOCK  # calendar must not force BLOCK


def test_diagonal_different_strike_recognized():
    snap = classify(
        _snap([
            _opt(OptionRight.CALL, 100, date(2026, 12, 18), 1, 18.0),
            _opt(OptionRight.CALL, 120, date(2026, 9, 18), -1, 4.0),
        ])
    )
    assert snap.strategies[0].strategy_type == StrategyType.DIAGONAL_CALL_SPREAD


def test_genuine_naked_short_call_still_blocks():
    snap = classify(_snap([_opt(OptionRight.CALL, 110, date(2026, 9, 18), -1, 6.0)]))
    assert snap.strategies[0].strategy_type == StrategyType.SHORT_CALL
    risk = analyze_portfolio(snap)
    assert risk.strategies[0].payoff.unbounded_loss is True
    report = evaluate(risk, load_rules(), mode=RuleMode.REVIEW)
    assert next(v for v in report.verdicts if v.rule_id == "per_trade_max_risk").severity == Severity.BLOCK
    assert report.overall == Severity.BLOCK


def test_pmcc_has_no_negative_max_profit():
    # Long deep LEAPS call + short near call => PMCC (strict). No pseudo-precise numbers.
    snap = classify(
        _snap([
            _opt(OptionRight.CALL, 100, date(2027, 6, 17), 1, 40.0),  # DTE ~332
            _opt(OptionRight.CALL, 130, date(2026, 9, 18), -1, 5.0),  # gap ~272
        ])
    )
    assert snap.strategies[0].strategy_type == StrategyType.PMCC
    p = analyze_portfolio(snap).strategies[0].payoff
    assert p.approximate is True
    assert p.available is False
    assert p.max_profit is None  # never a negative pseudo-precise max profit
    assert p.max_loss is None


def test_vertical_debit_spread_payoff_unaffected():
    snap = classify(
        _snap([
            _opt(OptionRight.CALL, 100, date(2026, 9, 18), 1, 8.0),
            _opt(OptionRight.CALL, 105, date(2026, 9, 18), -1, 3.0),
        ])
    )
    assert snap.strategies[0].strategy_type == StrategyType.VERTICAL_DEBIT_SPREAD
    p = analyze_portfolio(snap).strategies[0].payoff
    assert p.available is True
    assert p.approximate is False
    assert p.max_loss is not None and p.max_profit is not None


def test_eur_report_uses_eur_not_dollar():
    csv = (
        "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Value\n"
        "Open Positions,Data,Summary,Stocks,EUR,IBKR,10,1,77,770\n"
        "Cash Report,Header,Currency Summary,Currency,Total\n"
        "Cash Report,Data,Ending Cash,EUR,9876.54\n"
    )
    result = run_reconciliation(csv)
    assert result.snapshot.base_currency == "EUR"
    md = result.report_markdown
    assert "$" not in md  # no hardcoded dollar sign
    assert "EUR 9,876.54" in md
