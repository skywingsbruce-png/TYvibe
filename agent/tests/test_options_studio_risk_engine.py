"""Tests for the deterministic risk engine.

Covers: data-free vertical max profit/loss/breakeven, unbounded-risk detection,
unavailable Greeks/scenarios under the Null provider, and computed scenarios /
Greeks under an injected Static provider (no fabrication anywhere).
"""

from __future__ import annotations

from datetime import date

import pytest

from src.options_studio.classification import classify
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
)
from src.options_studio.providers import (
    NullMarketDataProvider,
    OptionMarket,
    StaticMarketDataProvider,
)
from src.options_studio.risk_engine import analyze_portfolio, analyze_strategy

AS_OF = date(2026, 1, 15)


def _opt(underlying, right, strike, expiry, qty, cost):
    return OptionContract(underlying, right, strike, expiry, 100, qty, average_cost=cost)


def _snapshot(options, cash=0.0):
    return PortfolioSnapshot(
        account_label="acct-test",
        as_of=AS_OF,
        base_currency="USD",
        cash=cash,
        buying_power=None,
        options=tuple(options),
    )


def _first_strategy(options, cash=0.0):
    return classify(_snapshot(options, cash)).strategies[0]


def test_vertical_debit_spread_payoff_is_data_free():
    long_call = _opt("MSFT", OptionRight.CALL, 400, date(2026, 2, 20), 1, 15.0)
    short_call = _opt("MSFT", OptionRight.CALL, 420, date(2026, 2, 20), -1, 6.0)
    strat = _first_strategy([long_call, short_call])
    # Null provider => no live data; payoff must still compute.
    risk = analyze_strategy(strat, NullMarketDataProvider(), AS_OF)
    assert risk.payoff.available is True
    assert risk.payoff.max_profit == pytest.approx(1100.0)
    assert risk.payoff.max_loss == pytest.approx(-900.0)
    assert risk.payoff.breakevens == (pytest.approx(409.0),)
    assert risk.payoff.net_cash == pytest.approx(-900.0)  # net debit paid
    assert risk.dte == 36
    assert risk.multiplier == 100


def test_vertical_credit_spread_payoff():
    short_put = _opt("SNDK", OptionRight.PUT, 80, date(2026, 1, 23), -1, 4.5)
    long_put = _opt("SNDK", OptionRight.PUT, 75, date(2026, 1, 23), 1, 2.5)
    strat = _first_strategy([short_put, long_put])
    risk = analyze_strategy(strat, NullMarketDataProvider(), AS_OF)
    assert risk.payoff.max_profit == pytest.approx(200.0)
    assert risk.payoff.max_loss == pytest.approx(-300.0)
    assert risk.payoff.breakevens == (pytest.approx(78.0),)
    assert risk.payoff.net_cash == pytest.approx(200.0)  # net credit received


def test_naked_short_call_has_unbounded_loss():
    sc = _opt("TSLA", OptionRight.CALL, 300, date(2026, 2, 20), -1, 5.0)
    strat = _first_strategy([sc])
    risk = analyze_strategy(strat, NullMarketDataProvider(), AS_OF)
    assert risk.payoff.unbounded_loss is True
    assert risk.payoff.max_loss is None
    assert risk.payoff.max_profit == pytest.approx(500.0)  # keep the premium


def test_null_provider_reports_greeks_and_scenarios_unavailable():
    strat = _first_strategy(
        [
            _opt("MSFT", OptionRight.CALL, 400, date(2026, 2, 20), 1, 15.0),
            _opt("MSFT", OptionRight.CALL, 420, date(2026, 2, 20), -1, 6.0),
        ]
    )
    risk = analyze_strategy(strat, NullMarketDataProvider(), AS_OF)
    assert risk.greeks.available is False
    assert "unavailable" in risk.greeks.reason.lower()
    assert risk.scenarios.available is False


def test_scenarios_computed_from_real_spot():
    long_call = _opt("MSFT", OptionRight.CALL, 400, date(2026, 2, 20), 1, 15.0)
    short_call = _opt("MSFT", OptionRight.CALL, 420, date(2026, 2, 20), -1, 6.0)
    strat = _first_strategy([long_call, short_call])
    provider = StaticMarketDataProvider(spots={"MSFT": (410.0, None)})
    risk = analyze_strategy(strat, provider, AS_OF)
    assert risk.scenarios.available is True
    assert risk.scenarios.spot == 410.0
    points = dict(risk.scenarios.points)
    assert points[-0.20] == pytest.approx(-900.0)  # deep OTM => max loss
    assert points[0.20] == pytest.approx(1100.0)    # deep ITM => max profit


def test_greeks_computed_from_real_iv():
    long_call = _opt("MSFT", OptionRight.CALL, 400, date(2026, 2, 20), 1, 15.0)
    short_call = _opt("MSFT", OptionRight.CALL, 420, date(2026, 2, 20), -1, 6.0)
    strat = _first_strategy([long_call, short_call])
    key = StaticMarketDataProvider.option_key
    provider = StaticMarketDataProvider(
        spots={"MSFT": (410.0, None)},
        options={
            key(long_call): (OptionMarket(implied_volatility=0.25), None),
            key(short_call): (OptionMarket(implied_volatility=0.25), None),
        },
    )
    risk = analyze_strategy(strat, provider, AS_OF)
    assert risk.greeks.available is True
    assert isinstance(risk.greeks.delta, float)
    # Long lower-strike call minus short higher-strike call => net long delta.
    assert risk.greeks.delta > 0


def test_missing_iv_on_one_leg_blocks_greeks():
    long_call = _opt("MSFT", OptionRight.CALL, 400, date(2026, 2, 20), 1, 15.0)
    short_call = _opt("MSFT", OptionRight.CALL, 420, date(2026, 2, 20), -1, 6.0)
    strat = _first_strategy([long_call, short_call])
    provider = StaticMarketDataProvider(
        spots={"MSFT": (410.0, None)},
        options={StaticMarketDataProvider.option_key(long_call): (OptionMarket(implied_volatility=0.25), None)},
    )
    risk = analyze_strategy(strat, provider, AS_OF)
    assert risk.greeks.available is False


def test_portfolio_aggregation_from_fixture_like_book():
    # MSFT debit (max loss 900) + SNDK near-dte credit (max loss 300, dte 8).
    options = [
        _opt("MSFT", OptionRight.CALL, 400, date(2026, 2, 20), 1, 15.0),
        _opt("MSFT", OptionRight.CALL, 420, date(2026, 2, 20), -1, 6.0),
        _opt("SNDK", OptionRight.PUT, 80, date(2026, 1, 23), -1, 4.5),
        _opt("SNDK", OptionRight.PUT, 75, date(2026, 1, 23), 1, 2.5),
    ]
    snap = classify(_snapshot(options))
    portfolio = analyze_portfolio(snap)
    assert portfolio.total_defined_max_loss == pytest.approx(1200.0)
    assert portfolio.unbounded_loss_strategies == 0
    # SNDK expires 2026-01-23 => dte 8 <= 14 => near-term bucket = its 300 loss.
    assert portfolio.near_dte_risk == pytest.approx(300.0)
    conc = dict(portfolio.concentration_by_underlying)
    assert conc["MSFT"] == pytest.approx(0.75)
    assert conc["SNDK"] == pytest.approx(0.25)
