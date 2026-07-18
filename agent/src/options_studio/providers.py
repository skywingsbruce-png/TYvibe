"""Market-data provider interface for the risk engine.

The risk engine needs two kinds of *real* market data:

* a spot price for each underlying (for scenario P/L and moneyness), and
* an implied volatility for each option contract (to compute Greeks via
  Black-Scholes).

This module defines the provider contract and the honest failure semantics that
the whole project hangs on: **when data is unavailable or stale, the provider
says so — it never fabricates a price, IV, OI, volume, or Greek.** A
:class:`DataResult` with ``available=False`` (or ``stale=True``) propagates all
the way to the UI, where the affected metric is shown as unavailable/stale and
strategy conclusions are withheld.

Two providers ship here:

* :class:`NullMarketDataProvider` — always unavailable. The safe default: with
  no configured data source, the Studio shows "data unavailable" rather than
  inventing numbers.
* :class:`StaticMarketDataProvider` — returns caller-injected values. Used by
  tests and by callers that already hold real quotes; it does not itself invent
  anything.

A real network-backed provider (bridging Vibe-Trading's existing option-chain
loaders) is intentionally left as a follow-up so this module has zero hidden
fabrication surface. See ``README`` "Known limitations".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Generic, Optional, TypeVar

from src.options_studio.models import OptionContract

T = TypeVar("T")

# A quote older than this many days is flagged stale (weekend-tolerant).
_STALE_AFTER_DAYS = 4


@dataclass(frozen=True)
class DataResult(Generic[T]):
    """A data lookup outcome that is explicit about availability and freshness.

    Attributes:
        available: Whether a real value was obtained.
        value: The value when available, else ``None``.
        stale: Whether the value is present but older than the freshness bound.
        reason: Human-readable explanation when unavailable or stale.
        as_of: Timestamp the value is valid as of, when known.
    """

    available: bool
    value: Optional[T] = None
    stale: bool = False
    reason: str = ""
    as_of: Optional[datetime] = None

    def usable(self) -> bool:
        """Return whether the value may drive a conclusion (present, not stale)."""
        return self.available and self.value is not None and not self.stale


@dataclass(frozen=True)
class Spot:
    """A real spot price for an underlying."""

    symbol: str
    price: float


@dataclass(frozen=True)
class OptionMarket:
    """Real option-market data for one contract.

    Only fields sourced from a real chain are populated; anything absent stays
    ``None`` and is never back-filled with a model or a guess.
    """

    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None


class MarketDataProvider(ABC):
    """Abstract source of real spot prices and option-market data."""

    @abstractmethod
    def get_spot(self, symbol: str) -> DataResult[Spot]:
        """Return a real spot price for ``symbol`` or an unavailable result."""

    @abstractmethod
    def get_option_market(self, contract: OptionContract) -> DataResult[OptionMarket]:
        """Return real option-market data for ``contract`` or unavailable."""


class NullMarketDataProvider(MarketDataProvider):
    """A provider that is always honestly unavailable.

    This is the default. With no configured data source, every risk metric that
    depends on live data is reported unavailable rather than invented.
    """

    _REASON = "No market-data provider configured; live data unavailable."

    def get_spot(self, symbol: str) -> DataResult[Spot]:
        return DataResult(available=False, reason=self._REASON)

    def get_option_market(self, contract: OptionContract) -> DataResult[OptionMarket]:
        return DataResult(available=False, reason=self._REASON)


class StaticMarketDataProvider(MarketDataProvider):
    """A provider returning caller-injected real values (no fabrication).

    Args:
        spots: Map of upper-cased symbol to ``(price, as_of)``.
        options: Map of an option key (see :meth:`option_key`) to
            ``(OptionMarket, as_of)``.
        now: Reference time for staleness. Defaults to the epoch-free
            caller-supplied value; when ``None``, staleness is not evaluated
            (results are treated as fresh) so tests are deterministic.
    """

    def __init__(
        self,
        spots: Optional[dict[str, tuple[float, Optional[datetime]]]] = None,
        options: Optional[dict[str, tuple[OptionMarket, Optional[datetime]]]] = None,
        now: Optional[datetime] = None,
    ) -> None:
        self._spots = {k.upper(): v for k, v in (spots or {}).items()}
        self._options = options or {}
        self._now = now

    @staticmethod
    def option_key(contract: OptionContract) -> str:
        """Return a stable lookup key for a contract."""
        return f"{contract.underlying}|{contract.right.value}|{contract.strike}|{contract.expiry.isoformat()}"

    def _staleness(self, as_of: Optional[datetime]) -> tuple[bool, str]:
        if self._now is None or as_of is None:
            return False, ""
        age = (self._now - as_of).days
        if age > _STALE_AFTER_DAYS:
            return True, f"Quote is {age} days old (> {_STALE_AFTER_DAYS})."
        return False, ""

    def get_spot(self, symbol: str) -> DataResult[Spot]:
        entry = self._spots.get(symbol.upper())
        if entry is None:
            return DataResult(available=False, reason=f"No spot for {symbol}.")
        price, as_of = entry
        stale, reason = self._staleness(as_of)
        return DataResult(available=True, value=Spot(symbol.upper(), price), stale=stale, reason=reason, as_of=as_of)

    def get_option_market(self, contract: OptionContract) -> DataResult[OptionMarket]:
        entry = self._options.get(self.option_key(contract))
        if entry is None:
            return DataResult(available=False, reason="No option-market data for this contract.")
        market, as_of = entry
        stale, reason = self._staleness(as_of)
        return DataResult(available=True, value=market, stale=stale, reason=reason, as_of=as_of)


def utc_now() -> datetime:
    """Return an aware UTC timestamp (helper for callers building providers)."""
    return datetime.now(timezone.utc)


def to_datetime(day: date) -> datetime:
    """Promote a date to an aware UTC datetime at midnight."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
