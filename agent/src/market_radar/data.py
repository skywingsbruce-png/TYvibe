"""Public daily-price adapter for the read-only market radar.

Yahoo Finance data is convenient and delayed/non-execution-grade. The caller
must expose failures and timestamps instead of substituting fabricated prices.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def fetch_daily_history(symbols: list[str]) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Fetch up to six months of daily bars, returning per-symbol failures."""
    try:
        import yfinance as yf
    except ImportError:
        return {}, {symbol: "yfinance is not installed" for symbol in symbols}

    histories: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    for symbol in symbols:
        try:
            frame = yf.Ticker(symbol).history(period="6mo", interval="1d", auto_adjust=False)
            if frame.empty or not {"Close", "Volume"}.issubset(frame.columns):
                failures[symbol] = "no usable daily OHLCV returned"
                continue
            histories[symbol] = frame.copy()
        except Exception as exc:  # noqa: BLE001 - a public data failure is not fatal to the radar
            failures[symbol] = f"{type(exc).__name__}: public data unavailable"
    return histories, failures


def source_metadata() -> dict[str, Any]:
    return {
        "name": "Yahoo Finance via yfinance",
        "grade": "public_delayed",
        "execution_safe": False,
        "message": "Public daily data is for research only, not execution or option pricing.",
    }
