"""Tests for the three-tier portfolio risk aggregation (all fictional data).

The point of these tests is that PMCC / calendar / diagonal time spreads are
never silently dropped from portfolio risk: their net debit becomes a
conservative capital-at-risk proxy that feeds the combined risk proxy and
concentration, and time spreads with a net credit / missing cost basis are
surfaced (indeterminate) rather than counted as zero.
"""

from __future__ import annotations

from datetime import date

from src.options_studio.classification import classify
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
    StrategyType,
)
from src.options_studio.risk_engine import analyze_portfolio
from src.options_studio.rules_engine import RuleMode, Severity, evaluate, load_rules

AS_OF = date(2026, 7, 20)

LONG_EXPIRY = date(2026, 12, 18)
SHORT_EXPIRY = date(2026, 9, 18)


def _opt(symbol, right, strike, expiry, qty, cost, mult=100):
    return OptionContract(symbol, right, strike, expiry, mult, qty, average_cost=cost)


def _snap(options, cash=0.0):
    return PortfolioSnapshot(
        account_label="acct-test",
        as_of=AS_OF,
        base_currency="USD",
        cash=cash,
        buying_power=None,
        options=tuple(options),
    )


def _vertical(symbol="AAA"):
    # Same-expiry call debit spread: strictly defined max loss.
    return [
        _opt(symbol, OptionRight.CALL, 100, SHORT_EXPIRY, 1, 8.0),
        _opt(symbol, OptionRight.CALL, 105, SHORT_EXPIRY, -1, 3.0),
    ]


def _pmcc_net_debit(symbol="BBB"):
    # Long deep LEAPS call (premium 40) + short near call (premium 5).
    # net_cash = -(1*40*100) - (-1*5*100) = -3500  => net debit 3500.
    return [
        _opt(symbol, OptionRight.CALL, 100, date(2027, 6, 17), 1, 40.0),
        _opt(symbol, OptionRight.CALL, 130, SHORT_EXPIRY, -1, 5.0),
    ]


def test_time_spread_net_debit_feeds_combined_proxy():
    # A vertical alone: deterministic == combined, no time-spread proxy.
    base = analyze_portfolio(classify(_snap(_vertical())))
    assert base.time_spread_net_debit_proxy == 0.0
    assert base.combined_risk_proxy == base.deterministic_defined_max_loss
    assert base.combined_risk_proxy > 0

    # Add a PMCC with a 3500 net debit: combined proxy must rise by exactly 3500.
    withpmcc = analyze_portfolio(classify(_snap(_vertical() + _pmcc_net_debit())))
    assert withpmcc.strategies  # sanity
    assert any(s.strategy_type == StrategyType.PMCC.value for s in withpmcc.strategies)
    assert withpmcc.deterministic_defined_max_loss == base.deterministic_defined_max_loss
    assert round(withpmcc.time_spread_net_debit_proxy, 2) == 3500.0
    assert round(withpmcc.combined_risk_proxy - base.combined_risk_proxy, 2) == 3500.0


def test_time_spread_is_not_zero_risk():
    # A calendar/PMCC on its own must not read as zero portfolio risk.
    risk = analyze_portfolio(classify(_snap(_pmcc_net_debit())))
    assert risk.deterministic_defined_max_loss == 0.0
    assert round(risk.time_spread_net_debit_proxy, 2) == 3500.0
    assert round(risk.combined_risk_proxy, 2) == 3500.0
    assert risk.approximate_risk_strategies == 1
    assert risk.indeterminate_time_spread_count == 0


def test_net_credit_time_spread_is_indeterminate_not_zero():
    # Calendar where the short leg is richer than the long leg => net CREDIT.
    # net_cash = -(1*2*100) - (-1*8*100) = +600  => credit, no debit proxy.
    snap = classify(
        _snap([
            _opt("CCC", OptionRight.CALL, 110, LONG_EXPIRY, 1, 2.0),
            _opt("CCC", OptionRight.CALL, 110, SHORT_EXPIRY, -1, 8.0),
        ])
    )
    assert snap.strategies[0].strategy_type == StrategyType.CALENDAR_CALL_SPREAD
    risk = analyze_portfolio(snap)
    assert risk.time_spread_net_debit_proxy == 0.0
    assert risk.combined_risk_proxy == 0.0  # cannot proxy; NOT counted as risk
    assert risk.indeterminate_time_spread_count == 1  # surfaced, not silently zero
    assert risk.approximate_risk_strategies == 1

    report = evaluate(risk, load_rules(), mode=RuleMode.REVIEW)
    verdict = next(v for v in report.verdicts if v.rule_id == "indeterminate_time_spread")
    assert verdict.severity == Severity.WATCH
    assert report.overall != Severity.BLOCK  # at least WATCH, not a false safe/ALLOW


def test_missing_cost_basis_time_spread_is_indeterminate():
    snap = classify(
        _snap([
            _opt("DDD", OptionRight.CALL, 110, LONG_EXPIRY, 1, None),  # missing premium
            _opt("DDD", OptionRight.CALL, 110, SHORT_EXPIRY, -1, 6.0),
        ])
    )
    assert snap.strategies[0].strategy_type == StrategyType.CALENDAR_CALL_SPREAD
    risk = analyze_portfolio(snap)
    assert risk.time_spread_net_debit_proxy == 0.0
    assert risk.indeterminate_time_spread_count == 1


def test_concentration_uses_combined_proxy_including_time_spread():
    # Small vertical on AAA, large PMCC net debit on BBB. BBB must dominate the
    # concentration ranking because time-spread proxy is included.
    risk = analyze_portfolio(classify(_snap(_vertical("AAA") + _pmcc_net_debit("BBB"))))
    assert risk.concentration_basis == "combined_risk_proxy"
    assert risk.concentration_includes_time_spread is True
    frac = {u: f for u, f in risk.concentration_by_underlying}
    assert "BBB" in frac and "AAA" in frac
    assert frac["BBB"] > frac["AAA"]  # the time spread is not invisible
    assert abs(sum(frac.values()) - 1.0) < 1e-6

    report = evaluate(risk, load_rules(), mode=RuleMode.REVIEW)
    conc = next(v for v in report.verdicts if v.rule_id == "single_underlying_concentration")
    assert "combined risk proxy" in conc.message


def _per_trade(report):
    return next(v for v in report.verdicts if v.rule_id == "per_trade_max_risk")


def test_large_time_spread_cannot_bypass_per_trade_cap_propose():
    # PMCC net debit 3500 >> default cap 500. It must NOT slip past the per-trade
    # rule just because its payoff is unavailable/approximate.
    risk = analyze_portfolio(classify(_snap(_pmcc_net_debit())))
    report = evaluate(risk, load_rules(), mode=RuleMode.PROPOSE, include_concentration=False)
    pt = _per_trade(report)
    assert pt.severity == Severity.BLOCK
    assert pt.details["time_spread_proxy_over_cap"]  # flagged via the proxy, not skipped
    assert report.overall == Severity.BLOCK


def test_large_time_spread_is_watch_exposure_in_review():
    risk = analyze_portfolio(classify(_snap(_pmcc_net_debit())))
    report = evaluate(risk, load_rules(), mode=RuleMode.REVIEW, include_concentration=False)
    pt = _per_trade(report)
    assert pt.severity == Severity.WATCH  # held exposure alert, not a block
    assert pt.details["time_spread_proxy_over_cap"]
    assert report.overall != Severity.BLOCK


def test_indeterminate_time_spread_fails_closed_on_propose():
    # Net-credit calendar: no net-debit proxy -> PROPOSE must fail closed (BLOCK).
    snap = classify(
        _snap([
            _opt("CCC", OptionRight.CALL, 110, LONG_EXPIRY, 1, 2.0),
            _opt("CCC", OptionRight.CALL, 110, SHORT_EXPIRY, -1, 8.0),
        ])
    )
    risk = analyze_portfolio(snap)
    propose = evaluate(risk, load_rules(), mode=RuleMode.PROPOSE, include_concentration=False)
    assert _per_trade(propose).severity == Severity.BLOCK
    assert _per_trade(propose).details["time_spread_no_proxy"]
    # REVIEW: at least WATCH, never silently ALLOW.
    review = evaluate(risk, load_rules(), mode=RuleMode.REVIEW, include_concentration=False)
    assert _per_trade(review).severity == Severity.WATCH


def test_missing_cost_basis_time_spread_fails_closed_on_propose():
    snap = classify(
        _snap([
            _opt("DDD", OptionRight.CALL, 110, LONG_EXPIRY, 1, None),
            _opt("DDD", OptionRight.CALL, 110, SHORT_EXPIRY, -1, 6.0),
        ])
    )
    risk = analyze_portfolio(snap)
    propose = evaluate(risk, load_rules(), mode=RuleMode.PROPOSE, include_concentration=False)
    assert _per_trade(propose).severity == Severity.BLOCK


def test_small_time_spread_within_cap_does_not_block_propose():
    # Long 6.0 / short 5.0 calendar => net debit 100 < cap 500: within limit.
    snap = classify(
        _snap([
            _opt("EEE", OptionRight.CALL, 110, LONG_EXPIRY, 1, 6.0),
            _opt("EEE", OptionRight.CALL, 110, SHORT_EXPIRY, -1, 5.0),
        ])
    )
    risk = analyze_portfolio(snap)
    assert round(risk.time_spread_net_debit_proxy, 2) == 100.0
    propose = evaluate(risk, load_rules(), mode=RuleMode.PROPOSE, include_concentration=False)
    assert _per_trade(propose).severity == Severity.ALLOW


def test_pdd_calendar_combined_proxy_equals_net_debit():
    # The real-world PDD pair: long 2026-12-18 C110 (15.0), short 2026-09-18 C110 (6.0).
    # net_cash = -(1*15*100) - (-1*6*100) = -900 => net debit 900.
    snap = classify(
        _snap([
            _opt("PDD", OptionRight.CALL, 110, LONG_EXPIRY, 1, 15.0),
            _opt("PDD", OptionRight.CALL, 110, SHORT_EXPIRY, -1, 6.0),
        ])
    )
    assert snap.strategies[0].strategy_type == StrategyType.CALENDAR_CALL_SPREAD
    risk = analyze_portfolio(snap)
    assert round(risk.time_spread_net_debit_proxy, 2) == 900.0
    assert round(risk.combined_risk_proxy, 2) == 900.0
    assert risk.unbounded_loss_strategies == 0  # never an unbounded naked short
