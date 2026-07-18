"""Options Risk Studio data contracts (frozen dataclasses).

These types are the stable boundary between the broker parser, the strategy
classifier, the deterministic risk engine, the hard-rules engine, and the API /
frontend. They deliberately mirror the style of
``src/shadow_account/models.py``: frozen dataclasses, explicit types, JSON-safe
``to_dict`` via :func:`dataclasses.asdict`.

Sign conventions
----------------
* ``quantity`` is **signed**: positive = long, negative = short. This holds for
  both :class:`UnderlyingPosition` shares and :class:`OptionContract` contracts.
* Monetary values are in the account's base currency (USD for this project).
* ``market_value`` is the current mark of the position (already multiplied by
  contract multiplier and signed quantity) when known, else ``None``.

Nothing here computes risk. Greeks, max loss, breakevens, and scenarios are
produced by :mod:`src.options_studio.risk_engine` from these contracts plus real
market data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional


class OptionRight(str, Enum):
    """Option right."""

    CALL = "call"
    PUT = "put"


class StrategyType(str, Enum):
    """Recognized option/stock strategy structures.

    ``UNCLASSIFIED`` is a first-class value, not a fallback to be guessed
    around: any combination the classifier cannot identify with confidence is
    labeled ``UNCLASSIFIED`` and must not be given a strategy name downstream.
    """

    LONG_STOCK = "long_stock"
    SHORT_STOCK = "short_stock"
    LONG_CALL = "long_call"
    SHORT_CALL = "short_call"
    LONG_PUT = "long_put"
    SHORT_PUT = "short_put"
    CASH_SECURED_PUT = "cash_secured_put"
    COVERED_CALL = "covered_call"
    VERTICAL_DEBIT_SPREAD = "vertical_debit_spread"
    VERTICAL_CREDIT_SPREAD = "vertical_credit_spread"
    PMCC = "poor_mans_covered_call"
    LEAPS = "leaps"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class UnderlyingPosition:
    """A cash-equity (share) position in one underlying.

    Attributes:
        symbol: Underlying ticker, upper-cased (e.g. ``"AAPL"``).
        quantity: Signed share count (positive long, negative short).
        average_cost: Average cost per share in account currency, or ``None``.
        market_value: Current mark of the whole position, or ``None`` if the
            live price is unavailable.
    """

    symbol: str
    quantity: float
    average_cost: Optional[float] = None
    market_value: Optional[float] = None


@dataclass(frozen=True)
class OptionContract:
    """A single option line item as imported from a broker statement.

    Every field required by the spec is mandatory except the marks
    (``average_cost`` / ``market_value``), which are ``None`` when the broker
    export or live data does not supply them.

    Attributes:
        underlying: Underlying ticker, upper-cased.
        right: :class:`OptionRight` — call or put.
        strike: Strike price in account currency.
        expiry: Expiration date.
        multiplier: Contract multiplier (US equity options are 100).
        quantity: Signed contract count (positive long, negative short).
        average_cost: Average premium per contract-share, or ``None``.
        market_value: Current mark of the whole line, or ``None`` if
            unavailable.
        occ_symbol: OCC option symbol when the broker provides it, else
            ``None``. Never fabricated.
    """

    underlying: str
    right: OptionRight
    strike: float
    expiry: date
    multiplier: int
    quantity: float
    average_cost: Optional[float] = None
    market_value: Optional[float] = None
    occ_symbol: Optional[str] = None

    def is_long(self) -> bool:
        """Return whether this is a long (positive-quantity) contract."""
        return self.quantity > 0

    def dte(self, as_of: date) -> int:
        """Return calendar days to expiry from ``as_of`` (may be negative)."""
        return (self.expiry - as_of).days


@dataclass(frozen=True)
class OptionLeg:
    """One leg of a classified strategy.

    A leg wraps an :class:`OptionContract` with the quantity actually consumed
    by its parent :class:`Strategy`. This lets a larger raw position be split
    across strategies (e.g. 10 short puts where 4 are cash-secured and 6 are the
    short leg of verticals) without mutating the underlying contract.

    Attributes:
        contract: The option contract this leg draws from.
        quantity: Signed contract count allocated to the strategy. Its absolute
            value is ``<=`` the contract's absolute quantity.
    """

    contract: OptionContract
    quantity: float


@dataclass(frozen=True)
class Strategy:
    """A classified (or explicitly unclassified) grouping of legs.

    Attributes:
        strategy_id: Stable ID like ``"S1"`` within a snapshot.
        strategy_type: Recognized :class:`StrategyType`, or
            :attr:`StrategyType.UNCLASSIFIED` when identification is not certain.
        underlying: Underlying ticker the strategy is on.
        option_legs: Tuple of option legs (may be empty for pure-stock).
        stock_legs: Tuple of share positions participating (e.g. the 100 shares
            behind a covered call). May be empty.
        note: Optional short human note (e.g. why it is unclassified). Never a
            fabricated strategy name.
    """

    strategy_id: str
    strategy_type: StrategyType
    underlying: str
    option_legs: tuple[OptionLeg, ...] = field(default_factory=tuple)
    stock_legs: tuple[UnderlyingPosition, ...] = field(default_factory=tuple)
    note: Optional[str] = None


@dataclass(frozen=True)
class TradeLot:
    """A single executed fill from the broker's trade blotter.

    Attributes:
        symbol: Underlying ticker.
        asset_kind: ``"stock"`` or ``"option"``.
        trade_date: Execution date.
        quantity: Signed quantity (positive buy, negative sell).
        price: Fill price per share / per contract-share.
        fees: Commissions + fees for this fill (non-negative).
        right: Option right when ``asset_kind == "option"``, else ``None``.
        strike: Option strike when applicable, else ``None``.
        expiry: Option expiry when applicable, else ``None``.
        action_type: Optional broker action tag (e.g. ``"exercise"``,
            ``"assignment"``, ``"corporate_action"``) preserved verbatim.
    """

    symbol: str
    asset_kind: str
    trade_date: date
    quantity: float
    price: float
    fees: float = 0.0
    right: Optional[OptionRight] = None
    strike: Optional[float] = None
    expiry: Optional[date] = None
    action_type: Optional[str] = None


@dataclass(frozen=True)
class PortfolioSnapshot:
    """A parsed, de-identified point-in-time portfolio.

    This is the top-level contract the risk engine and rules engine consume. It
    carries only market/position structure — never account numbers, names, or
    the account's absolute net-liquidation value in a form tied to identity. The
    anonymized ``account_label`` is the only account handle exposed downstream.

    Attributes:
        account_label: Opaque anonymized label (e.g. ``"acct-1"``). Never the
            real broker account number.
        as_of: Snapshot date the positions are valued as of.
        base_currency: Account base currency (e.g. ``"USD"``).
        cash: Settled cash balance in base currency.
        buying_power: Available buying power, or ``None`` when not exported.
        underlyings: Share positions.
        options: Raw option line items.
        strategies: Classified strategies (populated by the classifier).
        trade_lots: Executed fills from the blotter (may be empty).
        source_label: Short label of the source format (e.g.
            ``"ibkr_activity_csv"``). Never a file path.
    """

    account_label: str
    as_of: date
    base_currency: str
    cash: float
    buying_power: Optional[float]
    underlyings: tuple[UnderlyingPosition, ...] = field(default_factory=tuple)
    options: tuple[OptionContract, ...] = field(default_factory=tuple)
    strategies: tuple[Strategy, ...] = field(default_factory=tuple)
    trade_lots: tuple[TradeLot, ...] = field(default_factory=tuple)
    source_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict (dates become ISO strings)."""
        return _json_safe(asdict(self))


def _json_safe(obj: Any) -> Any:
    """Recursively convert dataclass-asdict output into JSON-safe values."""
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    return obj
