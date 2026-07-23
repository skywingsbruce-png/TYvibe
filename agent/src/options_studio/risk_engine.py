"""Deterministic options risk engine.

Everything here is deterministic and honest about data availability:

* **Expiry-payoff metrics** (max profit, max loss, breakevens) are computed from
  the position's own strikes and *real* premiums (the cost basis imported from
  the broker statement). They need **no** live market data, per the spec's
  requirement for vertical spreads. The same piecewise-linear payoff method also
  yields correct "unbounded" answers for undefined-risk structures (naked short
  call, short stock) instead of a fake number.
* **Greeks** are computed via Black-Scholes from a *real* spot and a *real*
  implied volatility supplied by a :class:`~src.options_studio.providers.MarketDataProvider`.
  If the provider reports the spot or IV unavailable/stale, the Greeks are
  returned unavailable — never modeled from a guessed IV.
* **Scenario P/L** at underlying moves of -20/-10/-5/0/+5/+10/+20% needs a real
  spot; with the default :class:`~src.options_studio.providers.NullMarketDataProvider`
  it is reported unavailable.

No value here is ever simulated. When an input is missing, the corresponding
output carries ``available=False`` and a reason string.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from scipy.stats import norm

from src.options_studio.models import (
    OptionRight,
    PortfolioSnapshot,
    Strategy,
    StrategyType,
)
from src.options_studio.providers import (
    MarketDataProvider,
    NullMarketDataProvider,
)

#: Underlying moves (as fractions) for the expiry P/L scenario grid.
SCENARIO_MOVES: tuple[float, ...] = (-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20)

#: Strategies with dte <= this are flagged in the near-term risk bucket.
NEAR_DTE_DAYS = 14

#: Default annual risk-free rate used only for Black-Scholes Greeks. It never
#: affects the (data-free) expiry-payoff metrics.
DEFAULT_RISK_FREE_RATE = 0.04


# ---------------------------------------------------------------------------
# Result contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayoffMetrics:
    """Expiry payoff metrics for one strategy (data-free).

    ``max_profit`` / ``max_loss`` are signed P/L in account currency:
    ``max_loss`` is negative. ``None`` means *unbounded* in that direction
    (e.g. a naked short call has ``max_loss=None``). ``available=False`` means
    the metric could not be computed because a required premium / cost basis was
    missing — never because it was guessed.
    """

    available: bool
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    breakevens: tuple[float, ...] = field(default_factory=tuple)
    net_cash: Optional[float] = None  # + = net credit received, - = net debit paid
    unbounded_profit: bool = False
    unbounded_loss: bool = False
    approximate: bool = False  # payoff cannot be modeled reliably (e.g. diagonals)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "max_profit": self.max_profit,
            "max_loss": self.max_loss,
            "breakevens": list(self.breakevens),
            "net_cash": self.net_cash,
            "unbounded_profit": self.unbounded_profit,
            "unbounded_loss": self.unbounded_loss,
            "approximate": self.approximate,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GreeksResult:
    """Position Greeks for one strategy, or an unavailable result."""

    available: bool
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ScenarioResult:
    """Expiry P/L across the underlying-move grid, or unavailable."""

    available: bool
    spot: Optional[float] = None
    points: tuple[tuple[float, float], ...] = field(default_factory=tuple)  # (move, pnl)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "spot": self.spot,
            "points": [{"move": m, "pnl": p} for m, p in self.points],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StrategyRisk:
    """All computed risk for a single strategy."""

    strategy_id: str
    strategy_type: str
    underlying: str
    dte: Optional[int]
    multiplier: int
    payoff: PayoffMetrics
    greeks: GreeksResult
    scenarios: ScenarioResult
    assignment_or_margin_risk: bool = False

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "strategy_type": self.strategy_type,
            "underlying": self.underlying,
            "dte": self.dte,
            "multiplier": self.multiplier,
            "payoff": self.payoff.to_dict(),
            "greeks": self.greeks.to_dict(),
            "scenarios": self.scenarios.to_dict(),
            "assignment_or_margin_risk": self.assignment_or_margin_risk,
        }


@dataclass(frozen=True)
class PortfolioRisk:
    """Portfolio-level aggregation.

    Three distinct amounts are kept apart and must NOT be conflated into one
    "total risk":

    * ``deterministic_defined_max_loss`` — sum of the strictly computable static
      max losses (verticals, long calls/puts, short puts, covered calls, …).
      This is an exact, data-free number.
    * ``time_spread_net_debit_proxy`` — sum of the net debit paid for PMCC /
      calendar / diagonal call spreads. It is a **conservative capital-at-risk
      proxy**, NOT a precise max loss, and is always flagged approximate. Time
      spreads with a net credit or a missing cost basis are *not* counted as
      zero: they are surfaced via ``indeterminate_time_spread_count``.
    * ``combined_risk_proxy`` — the two above added together. Used only to rank
      portfolio exposure / concentration; it is explicitly *not* a precise
      maximum loss.

    ``total_defined_max_loss`` is retained as an alias of
    ``deterministic_defined_max_loss`` for backward compatibility, and must never
    be presented as the portfolio's complete risk.
    """

    as_of: str
    base_currency: str
    strategies: tuple[StrategyRisk, ...]
    deterministic_defined_max_loss: float
    time_spread_net_debit_proxy: float
    combined_risk_proxy: float
    unbounded_loss_strategies: int
    indeterminate_risk_strategies: int
    indeterminate_time_spread_count: int
    approximate_risk_strategies: int
    unclassified_strategies: int
    concentration_by_underlying: tuple[tuple[str, float], ...]  # (symbol, fraction of combined proxy)
    concentration_basis: str
    concentration_includes_time_spread: bool
    near_dte_risk: float
    near_dte_strategy_ids: tuple[str, ...]

    @property
    def total_defined_max_loss(self) -> float:
        """Backward-compatible alias: the *deterministic* defined max loss only.

        Never treat this as the complete portfolio risk — see
        ``combined_risk_proxy``.
        """
        return self.deterministic_defined_max_loss

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "base_currency": self.base_currency,
            "strategies": [s.to_dict() for s in self.strategies],
            "deterministic_defined_max_loss": self.deterministic_defined_max_loss,
            "time_spread_net_debit_proxy": self.time_spread_net_debit_proxy,
            "combined_risk_proxy": self.combined_risk_proxy,
            # Backward-compatible alias (== deterministic_defined_max_loss). Not the full risk.
            "total_defined_max_loss": self.deterministic_defined_max_loss,
            "unbounded_loss_strategies": self.unbounded_loss_strategies,
            "indeterminate_risk_strategies": self.indeterminate_risk_strategies,
            "indeterminate_time_spread_count": self.indeterminate_time_spread_count,
            "approximate_risk_strategies": self.approximate_risk_strategies,
            "unclassified_strategies": self.unclassified_strategies,
            "concentration_by_underlying": [
                {"underlying": u, "fraction": f} for u, f in self.concentration_by_underlying
            ],
            "concentration_basis": self.concentration_basis,
            "concentration_includes_time_spread": self.concentration_includes_time_spread,
            "near_dte_risk": self.near_dte_risk,
            "near_dte_strategy_ids": list(self.near_dte_strategy_ids),
        }


# ---------------------------------------------------------------------------
# Payoff (data-free)
# ---------------------------------------------------------------------------


def _leg_intrinsic(right: OptionRight, spot: float, strike: float) -> float:
    if right == OptionRight.CALL:
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def _strategy_payoff_at(strategy: Strategy, spot: float) -> Optional[float]:
    """Return the strategy's P/L at expiry for underlying price ``spot``.

    Uses real premiums (option ``average_cost``) and real stock cost basis.
    Returns ``None`` if any required premium / cost basis is missing (so the
    caller reports the metric unavailable rather than guessing).
    """
    total = 0.0
    for leg in strategy.option_legs:
        premium = leg.contract.average_cost
        if premium is None:
            return None
        intrinsic = _leg_intrinsic(leg.contract.right, spot, leg.contract.strike)
        total += leg.quantity * (intrinsic - premium) * leg.contract.multiplier
    for stock in strategy.stock_legs:
        if stock.average_cost is None:
            return None
        total += stock.quantity * (spot - stock.average_cost)
    return total


def _strategy_slopes(strategy: Strategy) -> tuple[float, float]:
    """Return (leftmost, rightmost) payoff slope in P/L per $1 of underlying."""
    left = 0.0
    right = 0.0
    for leg in strategy.option_legs:
        mult = leg.contract.multiplier
        q = leg.quantity
        if leg.contract.right == OptionRight.CALL:
            right += q * mult  # calls gain slope above their strike
        else:  # put: gains slope below strike (negative direction)
            left += -q * mult
    for stock in strategy.stock_legs:
        left += stock.quantity
        right += stock.quantity
    return left, right


# Strategies whose legs expire on DIFFERENT dates. A single-expiry intrinsic
# payoff is misleading for these because the longer-dated long call still holds
# time value at the near leg's expiry, which cannot be valued without live
# option pricing. We therefore refuse to emit deterministic max profit/loss.
_TIME_SPREAD_TYPES = frozenset(
    {StrategyType.PMCC, StrategyType.CALENDAR_CALL_SPREAD, StrategyType.DIAGONAL_CALL_SPREAD}
)


def compute_payoff(strategy: Strategy) -> PayoffMetrics:
    """Compute expiry payoff metrics for ``strategy`` from strikes + premiums."""
    if strategy.strategy_type in _TIME_SPREAD_TYPES:
        # Do NOT emit a single-expiry intrinsic payoff (it would ignore the long
        # leg's residual time value and can produce a false negative max-profit).
        # Report it as approximate/unavailable with the net debit as the only
        # data-free defined-risk indicator.
        return PayoffMetrics(
            available=False,
            approximate=True,
            net_cash=round(_net_cash(strategy), 4) if _net_cash(strategy) is not None else None,
            unbounded_profit=False,
            unbounded_loss=False,
            reason=(
                "Diagonal/calendar with different leg expiries: the longer-dated long call still "
                "holds time value at the near leg's expiry, which cannot be valued without live "
                "option pricing. Max profit/loss are unavailable (approximate); the short call is "
                "covered by the long call, not an unbounded naked short."
            ),
        )

    strikes = sorted({leg.contract.strike for leg in strategy.option_legs})
    has_options = bool(strategy.option_legs)
    has_stock = bool(strategy.stock_legs)
    if not has_options and not has_stock:
        return PayoffMetrics(available=False, reason="Strategy has no legs.")

    # Evaluation points: 0, every strike, and a point well above the top strike.
    top = strikes[-1] if strikes else max((s.average_cost or 1.0) for s in strategy.stock_legs)
    test_points = [0.0] + strikes + [top * 2 + 10.0]
    payoffs: list[tuple[float, float]] = []
    for s in test_points:
        value = _strategy_payoff_at(strategy, s)
        if value is None:
            return PayoffMetrics(
                available=False,
                reason="Missing premium / cost basis for one or more legs; payoff not computed (not guessed).",
            )
        payoffs.append((s, value))

    left_slope, right_slope = _strategy_slopes(strategy)
    unbounded_profit = right_slope > 1e-9
    unbounded_loss = right_slope < -1e-9

    breakpoint_pnls = [p for _, p in payoffs]
    max_profit: Optional[float] = None if unbounded_profit else max(breakpoint_pnls)
    max_loss: Optional[float] = None if unbounded_loss else min(breakpoint_pnls)

    breakevens = _find_breakevens(payoffs)
    net_cash = _net_cash(strategy)

    return PayoffMetrics(
        available=True,
        max_profit=round(max_profit, 4) if max_profit is not None else None,
        max_loss=round(max_loss, 4) if max_loss is not None else None,
        breakevens=tuple(round(b, 4) for b in breakevens),
        net_cash=round(net_cash, 4) if net_cash is not None else None,
        unbounded_profit=unbounded_profit,
        unbounded_loss=unbounded_loss,
    )


def _find_breakevens(payoffs: list[tuple[float, float]]) -> list[float]:
    """Linear-interpolate zero crossings of the piecewise-linear payoff."""
    out: list[float] = []
    for (s0, p0), (s1, p1) in zip(payoffs, payoffs[1:]):
        if p0 == 0.0:
            out.append(s0)
        if (p0 < 0 < p1) or (p1 < 0 < p0):
            # zero crossing on this segment
            frac = p0 / (p0 - p1)
            out.append(s0 + frac * (s1 - s0))
    # de-dup close values
    unique: list[float] = []
    for value in sorted(out):
        if not unique or abs(value - unique[-1]) > 1e-6:
            unique.append(value)
    return unique


def _net_cash(strategy: Strategy) -> Optional[float]:
    """Net premium cash flow: + credit received, - debit paid."""
    total = 0.0
    for leg in strategy.option_legs:
        premium = leg.contract.average_cost
        if premium is None:
            return None
        # Long leg pays (negative cash); short leg receives (positive cash).
        total += -leg.quantity * premium * leg.contract.multiplier
    return total


# ---------------------------------------------------------------------------
# Greeks (needs real spot + real IV)
# ---------------------------------------------------------------------------


def _bs_greeks(spot: float, strike: float, t_years: float, r: float, sigma: float, right: OptionRight) -> dict:
    """Per-share Black-Scholes Greeks. Requires positive T and sigma."""
    if t_years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + sigma**2 / 2) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf = float(norm.pdf(d1))
    if right == OptionRight.CALL:
        delta = float(norm.cdf(d1))
        theta = (-(spot * pdf * sigma) / (2 * sqrt_t) - r * strike * math.exp(-r * t_years) * float(norm.cdf(d2))) / 365.0
    else:
        delta = float(norm.cdf(d1) - 1.0)
        theta = (-(spot * pdf * sigma) / (2 * sqrt_t) + r * strike * math.exp(-r * t_years) * float(norm.cdf(-d2))) / 365.0
    gamma = pdf / (spot * sigma * sqrt_t)
    vega = spot * pdf * sqrt_t / 100.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def compute_greeks(
    strategy: Strategy,
    provider: MarketDataProvider,
    as_of: date,
    risk_free_rate: float,
) -> GreeksResult:
    """Aggregate position Greeks from real spot + real IV, or unavailable."""
    if not strategy.option_legs:
        return GreeksResult(available=False, reason="No option legs; Greeks not applicable.")

    spot_res = provider.get_spot(strategy.underlying)
    if not spot_res.usable():
        return GreeksResult(available=False, reason=spot_res.reason or "Spot unavailable.")
    spot = spot_res.value.price  # type: ignore[union-attr]

    total = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for leg in strategy.option_legs:
        market = provider.get_option_market(leg.contract)
        if not market.usable() or market.value is None:
            return GreeksResult(available=False, reason=market.reason or "Option-market data unavailable.")
        m = market.value
        dte = max(leg.contract.dte(as_of), 0)
        t_years = dte / 365.0
        # Prefer provider-supplied Greeks; else compute from real IV.
        if m.delta is not None and m.gamma is not None and m.theta is not None and m.vega is not None:
            per_share = {"delta": m.delta, "gamma": m.gamma, "theta": m.theta, "vega": m.vega}
        elif m.implied_volatility is not None:
            per_share = _bs_greeks(spot, leg.contract.strike, t_years, risk_free_rate, m.implied_volatility, leg.contract.right)
        else:
            return GreeksResult(
                available=False,
                reason="Neither provider Greeks nor implied volatility available; refusing to model from a guessed IV.",
            )
        scale = leg.quantity * leg.contract.multiplier
        for key in total:
            total[key] += per_share[key] * scale

    return GreeksResult(
        available=True,
        delta=round(total["delta"], 4),
        gamma=round(total["gamma"], 6),
        theta=round(total["theta"], 4),
        vega=round(total["vega"], 4),
    )


# ---------------------------------------------------------------------------
# Scenarios (needs real spot)
# ---------------------------------------------------------------------------


def compute_scenarios(strategy: Strategy, provider: MarketDataProvider) -> ScenarioResult:
    """Expiry P/L across the move grid, using a real spot, or unavailable."""
    spot_res = provider.get_spot(strategy.underlying)
    if not spot_res.usable() or spot_res.value is None:
        return ScenarioResult(available=False, reason=spot_res.reason or "Spot unavailable.")
    spot = spot_res.value.price
    points: list[tuple[float, float]] = []
    for move in SCENARIO_MOVES:
        pnl = _strategy_payoff_at(strategy, spot * (1 + move))
        if pnl is None:
            return ScenarioResult(
                available=False,
                spot=spot,
                reason="Missing premium / cost basis; scenario P/L not computed (not guessed).",
            )
        points.append((move, round(pnl, 4)))
    return ScenarioResult(available=True, spot=spot, points=tuple(points))


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def _strategy_dte(strategy: Strategy, as_of: date) -> Optional[int]:
    dtes = [leg.contract.dte(as_of) for leg in strategy.option_legs]
    return min(dtes) if dtes else None


def _strategy_multiplier(strategy: Strategy) -> int:
    for leg in strategy.option_legs:
        return leg.contract.multiplier
    return 100


def analyze_strategy(
    strategy: Strategy,
    provider: MarketDataProvider,
    as_of: date,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> StrategyRisk:
    """Compute payoff + Greeks + scenarios for one strategy."""
    return StrategyRisk(
        strategy_id=strategy.strategy_id,
        strategy_type=strategy.strategy_type.value,
        underlying=strategy.underlying,
        dte=_strategy_dte(strategy, as_of),
        multiplier=_strategy_multiplier(strategy),
        payoff=compute_payoff(strategy),
        greeks=compute_greeks(strategy, provider, as_of, risk_free_rate),
        scenarios=compute_scenarios(strategy, provider),
        assignment_or_margin_risk=strategy.strategy_type in _TIME_SPREAD_TYPES,
    )


def analyze_portfolio(
    snapshot: PortfolioSnapshot,
    provider: Optional[MarketDataProvider] = None,
    *,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> PortfolioRisk:
    """Analyze every classified strategy and aggregate portfolio risk.

    Args:
        snapshot: A snapshot whose ``strategies`` have been classified.
        provider: Market-data provider. Defaults to
            :class:`NullMarketDataProvider` (Greeks/scenarios unavailable).
        risk_free_rate: Annual rate for Black-Scholes Greeks only.
    """
    provider = provider or NullMarketDataProvider()
    risks = [analyze_strategy(s, provider, snapshot.as_of, risk_free_rate) for s in snapshot.strategies]

    deterministic_defined_max_loss = 0.0
    time_spread_net_debit_proxy = 0.0
    unbounded = 0
    indeterminate = 0
    indeterminate_time_spread = 0
    approximate = 0
    unclassified = 0
    # Per-underlying contribution to the COMBINED risk proxy (deterministic loss
    # + time-spread net debit), used for concentration ranking.
    combined_by_underlying: dict[str, float] = {}
    near_dte_risk = 0.0
    near_dte_ids: list[str] = []

    for risk in risks:
        if risk.strategy_type == StrategyType.UNCLASSIFIED.value:
            unclassified += 1
        payoff = risk.payoff
        # ``contribution`` is this strategy's share of the combined risk proxy
        # (None => it could not be quantified and is surfaced as indeterminate,
        # never silently treated as zero).
        contribution: Optional[float] = None

        if payoff.approximate:
            # PMCC / calendar / diagonal. Its true max loss needs live pricing;
            # use the net debit paid as a conservative capital-at-risk proxy.
            approximate += 1
            net_cash = payoff.net_cash
            if net_cash is not None and net_cash < 0:
                contribution = abs(net_cash)  # net debit paid
                time_spread_net_debit_proxy += contribution
            else:
                # Net credit or missing cost basis: cannot proxy capital at risk,
                # and it is NOT zero risk. Single it out for a mandatory WATCH.
                indeterminate_time_spread += 1
        elif not payoff.available:
            # Truly indeterminate (e.g. missing premium on a static structure).
            indeterminate += 1
        elif payoff.unbounded_loss or payoff.max_loss is None:
            unbounded += 1
        else:
            contribution = abs(min(payoff.max_loss, 0.0))
            deterministic_defined_max_loss += contribution

        if contribution is not None:
            combined_by_underlying[risk.underlying] = (
                combined_by_underlying.get(risk.underlying, 0.0) + contribution
            )
            if risk.dte is not None and 0 <= risk.dte <= NEAR_DTE_DAYS:
                near_dte_risk += contribution
                near_dte_ids.append(risk.strategy_id)

    combined_risk_proxy = deterministic_defined_max_loss + time_spread_net_debit_proxy

    if combined_risk_proxy > 0:
        concentration = tuple(
            sorted(
                ((u, round(v / combined_risk_proxy, 4)) for u, v in combined_by_underlying.items()),
                key=lambda kv: kv[1],
                reverse=True,
            )
        )
    else:
        concentration = ()

    return PortfolioRisk(
        as_of=snapshot.as_of.isoformat(),
        base_currency=snapshot.base_currency,
        strategies=tuple(risks),
        deterministic_defined_max_loss=round(deterministic_defined_max_loss, 4),
        time_spread_net_debit_proxy=round(time_spread_net_debit_proxy, 4),
        combined_risk_proxy=round(combined_risk_proxy, 4),
        unbounded_loss_strategies=unbounded,
        indeterminate_risk_strategies=indeterminate,
        indeterminate_time_spread_count=indeterminate_time_spread,
        approximate_risk_strategies=approximate,
        unclassified_strategies=unclassified,
        concentration_by_underlying=concentration,
        concentration_basis="combined_risk_proxy",
        concentration_includes_time_spread=time_spread_net_debit_proxy > 0,
        near_dte_risk=round(near_dte_risk, 4),
        near_dte_strategy_ids=tuple(near_dte_ids),
    )
