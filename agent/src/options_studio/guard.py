"""Structural no-real-trading guard for the Options Risk Studio.

This module is the single, explicit enforcement point for the project's
hardest invariant: **the Options Risk Studio must never place, modify, or
cancel a real broker order, and must never enable any trading permission.**

The guard is deliberately paranoid and fail-closed:

* Import of a raw broker statement is *read-only*; nothing in this package
  constructs a broker order object or calls a broker write endpoint.
* Any attempt to switch on real trading — via the ``ENABLE_REAL_TRADING``
  environment variable, or by passing an explicit flag through the Studio's
  own surfaces — raises :class:`RealTradingForbiddenError`.
* The check is intentionally decoupled from Vibe-Trading's core live-trading
  mandate machinery (``src.live``). Even if that machinery were mis-configured
  upstream, the Studio's own entry points refuse to proceed.

There is no code path in this package that turns the guard off.
"""

from __future__ import annotations

import os

#: Environment variables that, if set to a truthy value, indicate someone is
#: trying to arm real trading. The Studio refuses to run when any is truthy.
_REAL_TRADING_ENV_VARS = (
    "ENABLE_REAL_TRADING",
    "OPTIONS_STUDIO_ENABLE_REAL_TRADING",
    "VIBE_TRADING_ENABLE_REAL_TRADING",
)

#: Values that count as "on". Everything else (including unset) counts as off.
_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}


class RealTradingForbiddenError(RuntimeError):
    """Raised whenever any real-trading capability is requested.

    The Options Risk Studio is a read-only analysis workspace. This error is
    the intended, non-recoverable outcome of asking it to trade.
    """


def _is_truthy(value: str | None) -> bool:
    """Return whether an env-style string value should be treated as ``True``."""
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def real_trading_requested() -> bool:
    """Return whether any real-trading toggle is currently switched on.

    Checks every known enable-flag environment variable. Used by tests and by
    :func:`assert_no_real_trading` so the detection logic has one home.
    """
    return any(_is_truthy(os.getenv(name)) for name in _REAL_TRADING_ENV_VARS)


def assert_no_real_trading(*, requested: bool = False) -> None:
    """Fail closed unless real trading is provably *off*.

    Args:
        requested: An explicit in-process request to arm real trading (e.g. a
            flag threaded through a Studio API call). Any truthy value here is
            itself a violation — the Studio has no legitimate reason to receive
            one.

    Raises:
        RealTradingForbiddenError: If ``requested`` is truthy, or if any
            enable-flag environment variable is set to a truthy value.
    """
    if requested:
        raise RealTradingForbiddenError(
            "Options Risk Studio is read-only: an explicit real-trading request "
            "was rejected. This workspace never places, modifies, or cancels "
            "broker orders."
        )
    for name in _REAL_TRADING_ENV_VARS:
        if _is_truthy(os.getenv(name)):
            raise RealTradingForbiddenError(
                f"{name} is set to a truthy value, but the Options Risk Studio "
                "refuses to enable real trading. Unset it to continue in "
                "read-only mode."
            )
