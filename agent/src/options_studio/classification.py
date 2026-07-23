"""Deterministic strategy classifier.

Groups a portfolio's raw option/stock lines into recognized structures. The
guiding rule from the spec: **never invent a strategy name.** Single legs are
always identifiable and are named by their unambiguous single-leg type. Combined
structures (verticals, covered calls, cash-secured puts, PMCC) are formed *only*
on an exact, quantity-balanced match. Anything the matcher cannot place into a
recognized bucket is emitted as :attr:`StrategyType.UNCLASSIFIED` with a note.

The classifier is pure: it takes a :class:`PortfolioSnapshot` and returns a new
snapshot with :attr:`PortfolioSnapshot.strategies` populated. It reads marks
(premiums) when present to distinguish debit vs credit verticals, and falls back
to the structural strike/right rule when premiums are missing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.options_studio.models import (
    OptionContract,
    OptionLeg,
    OptionRight,
    PortfolioSnapshot,
    Strategy,
    StrategyType,
    UnderlyingPosition,
)

# A long option with at least this many calendar days to expiry is treated as a
# LEAPS when it stands alone.
_LEAPS_DTE = 365

# Minimum expiry gap (days) between a long call and a short call for the
# structure to be read as a diagonal PMCC rather than a same-cycle spread.
_PMCC_MIN_GAP = 150

# A PMCC long leg must itself be reasonably long-dated.
_PMCC_MIN_LONG_DTE = 270


@dataclass
class _Unit:
    """One contract's-worth of exposure during matching (mutable bookkeeping)."""

    contract: OptionContract
    remaining: int  # absolute contracts still unallocated


def classify(snapshot: PortfolioSnapshot) -> PortfolioSnapshot:
    """Return a copy of ``snapshot`` with strategies classified."""
    strategies: list[Strategy] = []
    counter = _IdCounter()
    by_underlying: dict[str, list[OptionContract]] = {}
    for opt in snapshot.options:
        by_underlying.setdefault(opt.underlying, []).append(opt)

    shares_by_underlying: dict[str, list[UnderlyingPosition]] = {}
    for pos in snapshot.underlyings:
        shares_by_underlying.setdefault(pos.symbol, []).append(pos)

    for underlying in sorted(set(by_underlying) | set(shares_by_underlying)):
        strategies.extend(
            _classify_underlying(
                underlying,
                by_underlying.get(underlying, []),
                shares_by_underlying.get(underlying, []),
                snapshot.as_of,
                snapshot.cash,
                counter,
            )
        )
    return replace(snapshot, strategies=tuple(strategies))


class _IdCounter:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"S{self._n}"


def _classify_underlying(
    underlying: str,
    options: list[OptionContract],
    shares: list[UnderlyingPosition],
    as_of,
    account_cash: float,
    counter: _IdCounter,
) -> list[Strategy]:
    strategies: list[Strategy] = []

    long_calls = [_Unit(o, int(round(abs(o.quantity)))) for o in options if o.right == OptionRight.CALL and o.quantity > 0]
    short_calls = [_Unit(o, int(round(abs(o.quantity)))) for o in options if o.right == OptionRight.CALL and o.quantity < 0]
    long_puts = [_Unit(o, int(round(abs(o.quantity)))) for o in options if o.right == OptionRight.PUT and o.quantity > 0]
    short_puts = [_Unit(o, int(round(abs(o.quantity)))) for o in options if o.right == OptionRight.PUT and o.quantity < 0]

    net_shares = sum(s.quantity for s in shares)
    free_share_lots = int(net_shares // 100) if net_shares > 0 else 0
    free_cash = max(account_cash, 0.0)
    share_avg_cost = _avg_cost(shares)

    # 1) PMCC — long-dated long call diagonalized by a near-dated short call.
    for lc in long_calls:
        if lc.remaining <= 0 or lc.contract.dte(as_of) < _PMCC_MIN_LONG_DTE:
            continue
        for sc in short_calls:
            while lc.remaining > 0 and sc.remaining > 0 and (lc.contract.dte(as_of) - sc.contract.dte(as_of)) >= _PMCC_MIN_GAP:
                strategies.append(
                    Strategy(
                        strategy_id=counter.next(),
                        strategy_type=StrategyType.PMCC,
                        underlying=underlying,
                        option_legs=(OptionLeg(lc.contract, 1), OptionLeg(sc.contract, -1)),
                        note="Poor man's covered call (diagonal long-dated call + short near call).",
                    )
                )
                lc.remaining -= 1
                sc.remaining -= 1

    # 2) Covered calls — short call fully backed by >=100 shares each.
    for sc in short_calls:
        while sc.remaining > 0 and free_share_lots > 0:
            strategies.append(
                Strategy(
                    strategy_id=counter.next(),
                    strategy_type=StrategyType.COVERED_CALL,
                    underlying=underlying,
                    option_legs=(OptionLeg(sc.contract, -1),),
                    stock_legs=(UnderlyingPosition(symbol=underlying, quantity=100, average_cost=share_avg_cost),),
                    note="Short call backed by 100 shares.",
                )
            )
            sc.remaining -= 1
            free_share_lots -= 1

    # 3) Vertical spreads (same expiry, same right).
    strategies.extend(_match_verticals(underlying, long_calls, short_calls, OptionRight.CALL, as_of, counter))
    strategies.extend(_match_verticals(underlying, long_puts, short_puts, OptionRight.PUT, as_of, counter))

    # 3.5) Calendar / diagonal call spreads — a short call whose expiry is
    # EARLIER than a same-underlying long call. Paired here, BEFORE the naked
    # short-call fallback, so a short call covered by a longer-dated long call is
    # never mislabeled as an unbounded naked short. This is intentionally NOT
    # gated on the strict PMCC "long leg >= 270 DTE" rule.
    strategies.extend(_match_calendar_diagonal(underlying, long_calls, short_calls, counter))

    # 4) Remaining short puts — cash-secured when free cash covers assignment.
    for sp in short_puts:
        while sp.remaining > 0:
            required = sp.contract.strike * sp.contract.multiplier
            if free_cash >= required:
                free_cash -= required
                stype = StrategyType.CASH_SECURED_PUT
                note = "Short put fully secured by available cash."
            else:
                stype = StrategyType.SHORT_PUT
                note = "Short put NOT fully cash-secured by portfolio cash."
            strategies.append(
                Strategy(
                    strategy_id=counter.next(),
                    strategy_type=stype,
                    underlying=underlying,
                    option_legs=(OptionLeg(sp.contract, -1),),
                    note=note,
                )
            )
            sp.remaining -= 1

    # 5) Remaining single legs.
    strategies.extend(_emit_singles(underlying, long_calls, StrategyType.LONG_CALL, as_of, counter))
    strategies.extend(_emit_singles(underlying, short_calls, StrategyType.SHORT_CALL, as_of, counter))
    strategies.extend(_emit_singles(underlying, long_puts, StrategyType.LONG_PUT, as_of, counter))

    # 6) Uncovered residual shares as a plain stock position.
    residual_shares = net_shares - (
        sum(100 for s in strategies if s.strategy_type == StrategyType.COVERED_CALL)
    )
    if abs(residual_shares) >= 1e-9 and net_shares != 0:
        avg = _avg_cost(shares)
        strategies.append(
            Strategy(
                strategy_id=counter.next(),
                strategy_type=StrategyType.LONG_STOCK if residual_shares > 0 else StrategyType.SHORT_STOCK,
                underlying=underlying,
                stock_legs=(UnderlyingPosition(symbol=underlying, quantity=residual_shares, average_cost=avg),),
                note=None,
            )
        )

    return strategies


def _match_verticals(
    underlying: str,
    longs: list[_Unit],
    shorts: list[_Unit],
    right: OptionRight,
    as_of,
    counter: _IdCounter,
) -> list[Strategy]:
    out: list[Strategy] = []
    for lu in longs:
        for su in shorts:
            while (
                lu.remaining > 0
                and su.remaining > 0
                and lu.contract.expiry == su.contract.expiry
                and lu.contract.strike != su.contract.strike
            ):
                debit = _is_debit(lu.contract, su.contract, right)
                out.append(
                    Strategy(
                        strategy_id=counter.next(),
                        strategy_type=StrategyType.VERTICAL_DEBIT_SPREAD if debit else StrategyType.VERTICAL_CREDIT_SPREAD,
                        underlying=underlying,
                        option_legs=(OptionLeg(lu.contract, 1), OptionLeg(su.contract, -1)),
                        note=f"Vertical {right.value} spread ({'debit' if debit else 'credit'}).",
                    )
                )
                lu.remaining -= 1
                su.remaining -= 1
    return out


def _match_calendar_diagonal(
    underlying: str,
    long_calls: list[_Unit],
    short_calls: list[_Unit],
    counter: _IdCounter,
) -> list[Strategy]:
    """Pair a short call with a longer-dated long call (calendar/diagonal).

    Requirements: 1:1 pairing, same underlying, long-call expiry strictly AFTER
    the short-call expiry, and equal multipliers. Same strike => calendar; a
    different strike => diagonal. These are NOT unbounded-loss positions: the
    short call is covered by the longer-dated long call; they carry
    assignment / margin risk instead.
    """
    out: list[Strategy] = []
    for lu in long_calls:
        for su in short_calls:
            while (
                lu.remaining > 0
                and su.remaining > 0
                and lu.contract.expiry > su.contract.expiry
                and lu.contract.multiplier == su.contract.multiplier
            ):
                same_strike = lu.contract.strike == su.contract.strike
                stype = StrategyType.CALENDAR_CALL_SPREAD if same_strike else StrategyType.DIAGONAL_CALL_SPREAD
                kind = "Calendar" if same_strike else "Diagonal"
                out.append(
                    Strategy(
                        strategy_id=counter.next(),
                        strategy_type=stype,
                        underlying=underlying,
                        option_legs=(OptionLeg(lu.contract, 1), OptionLeg(su.contract, -1)),
                        note=(
                            f"{kind} call spread: the short call expires before the longer-dated long "
                            "call, which covers it. Not an unbounded naked short — carries "
                            "assignment / margin risk; static payoff is approximate without live pricing."
                        ),
                    )
                )
                lu.remaining -= 1
                su.remaining -= 1
    return out


def _is_debit(long_c: OptionContract, short_c: OptionContract, right: OptionRight) -> bool:
    """Decide debit vs credit, preferring real premiums, else strike structure."""
    lp, sp = long_c.average_cost, short_c.average_cost
    if lp is not None and sp is not None:
        return lp >= sp
    # Structural fallback: for calls, buying the lower strike is a debit;
    # for puts, buying the higher strike is a debit.
    if right == OptionRight.CALL:
        return long_c.strike < short_c.strike
    return long_c.strike > short_c.strike


def _emit_singles(
    underlying: str,
    units: list[_Unit],
    base_type: StrategyType,
    as_of,
    counter: _IdCounter,
) -> list[Strategy]:
    out: list[Strategy] = []
    for u in units:
        while u.remaining > 0:
            is_long = u.contract.quantity > 0
            stype = base_type
            note = None
            if is_long and u.contract.dte(as_of) >= _LEAPS_DTE:
                stype = StrategyType.LEAPS
                note = f"Long-dated {u.contract.right.value} (DTE >= {_LEAPS_DTE}); treated as LEAPS."
            out.append(
                Strategy(
                    strategy_id=counter.next(),
                    strategy_type=stype,
                    underlying=underlying,
                    option_legs=(OptionLeg(u.contract, 1 if is_long else -1),),
                    note=note,
                )
            )
            u.remaining -= 1
    return out


def _avg_cost(shares: list[UnderlyingPosition]) -> float | None:
    known = [s for s in shares if s.average_cost is not None]
    if not known:
        return None
    total_qty = sum(abs(s.quantity) for s in known) or 1.0
    return sum((s.average_cost or 0.0) * abs(s.quantity) for s in known) / total_qty
