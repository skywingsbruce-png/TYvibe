"""Deterministic read-only market radar assembly.

The scanner ranks *watch candidates*. It deliberately separates a technical
observation from an entry decision, does not infer smart-money intent, and
does not contain any broker or order-routing code.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

import pandas as pd

from src.market_notes.holdings import build_holdings_index
from src.market_radar.config import EventGate, RadarConfig, Theme, load_config
from src.market_radar.data import fetch_daily_history, source_metadata

HistoryFetcher = Callable[[list[str]], tuple[dict[str, pd.DataFrame], dict[str, str]]]


def _number(value: float | int | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def _percent_change(series: pd.Series, periods: int) -> float | None:
    if len(series) <= periods:
        return None
    prior = float(series.iloc[-periods - 1])
    if prior == 0:
        return None
    return _number((float(series.iloc[-1]) / prior - 1) * 100)


def _metrics(frame: pd.DataFrame) -> dict | None:
    if len(frame) < 55:
        return None
    close = frame["Close"].astype(float).dropna()
    volume = frame["Volume"].astype(float).reindex(close.index).fillna(0)
    if len(close) < 55:
        return None
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    average_volume = volume.iloc[-21:-1].mean()
    rolling_peak = close.tail(60).cummax()
    drawdown = (close.tail(60) / rolling_peak - 1).min() * 100
    return {
        "close": _number(close.iloc[-1]),
        "return_1d": _percent_change(close, 1),
        "return_5d": _percent_change(close, 5),
        "return_20d": _percent_change(close, 20),
        "ma20": _number(ma20),
        "ma50": _number(ma50),
        "drawdown_60d": _number(drawdown),
        "volume_ratio_20d": _number(volume.iloc[-1] / average_volume) if average_volume > 0 else None,
        "support_20d": _number(close.iloc[-21:-1].min()),
        "breakout_20d": _number(close.iloc[-21:-1].max()),
        "as_of": str(close.index[-1].date()),
    }


def _trend_score(metrics: dict) -> int:
    close, ma20, ma50 = metrics["close"], metrics["ma20"], metrics["ma50"]
    return (15 if close > ma20 else 0) + (10 if ma20 > ma50 else 0) + (10 if close > ma50 else 0)


def _relative_strength_score(metrics: dict, qqq: dict | None) -> tuple[int | None, float | None]:
    if qqq is None or metrics["return_20d"] is None or qqq["return_20d"] is None:
        return None, None
    relative = metrics["return_20d"] - qqq["return_20d"]
    if relative >= 3:
        score = 25
    elif relative >= 0:
        score = 15
    else:
        score = 5
    return score, _number(relative)


def _volume_score(metrics: dict) -> int | None:
    ratio = metrics["volume_ratio_20d"]
    if ratio is None:
        return None
    if ratio >= 1.5 and (metrics["return_1d"] or 0) > 0:
        return 20
    if ratio >= 1.15:
        return 10
    return 0


def _event_gates(symbol: str, events: tuple[EventGate, ...], now: date, window_days: int) -> list[dict]:
    gates: list[dict] = []
    for event in events:
        try:
            event_date = date.fromisoformat(event.date)
        except ValueError:
            continue
        days_away = (event_date - now).days
        if event.symbols and symbol not in event.symbols:
            continue
        if 0 <= days_away <= window_days:
            gates.append({"name": event.name, "date": event.date, "days_away": days_away})
    return gates


def _candidate(symbol: str, frame: pd.DataFrame | None, qqq: dict | None, config: RadarConfig, today: date) -> dict:
    if frame is None:
        return {
            "symbol": symbol,
            "status": "data_unavailable",
            "score": None,
            "score_max": 100,
            "metrics": None,
            "components": [],
            "setup": None,
            "event_gates": [],
            "message": "No usable public daily data; not ranked.",
        }
    metrics = _metrics(frame)
    if metrics is None:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
            "score": None,
            "score_max": 100,
            "metrics": None,
            "components": [],
            "setup": None,
            "event_gates": [],
            "message": "Fewer than 55 daily bars; not ranked.",
        }
    trend = _trend_score(metrics)
    relative_score, relative_return = _relative_strength_score(metrics, qqq)
    volume = _volume_score(metrics)
    gates = _event_gates(symbol, config.events, today, config.event_window_days)
    event_score = 0 if gates else (20 if config.event_calendar_complete else None)
    components = [
        {"name": "trend", "score": trend, "max": 35, "status": "available"},
        {"name": "relative_strength_vs_qqq", "score": relative_score, "max": 25, "status": "available" if relative_score is not None else "unavailable"},
        {"name": "price_volume", "score": volume, "max": 20, "status": "available" if volume is not None else "unavailable"},
        {
            "name": "event_gate",
            "score": event_score,
            "max": 20,
            "status": "blocked" if gates else ("available" if config.event_calendar_complete else "unavailable"),
        },
    ]
    available_scores = [item["score"] for item in components if item["score"] is not None]
    return {
        "symbol": symbol,
        "status": "event_gate" if gates else "watch",
        "score": sum(available_scores),
        "score_max": sum(item["max"] for item in components if item["score"] is not None),
        "metrics": {**metrics, "relative_return_20d_vs_qqq": relative_return},
        "components": components,
        "setup": {
            "observation": "Watch candidate only. A score is not an entry signal.",
            "trigger": f"Daily close above {metrics['breakout_20d']:.2f} with confirmation, not an order.",
            "invalidation": f"Daily close below {metrics['support_20d']:.2f}; reassess the setup.",
            "instrument_note": "Use the separate Options Studio proposal check before any defined-risk option idea.",
        },
        "event_gates": gates,
        "message": "Ranked for observation from daily price, volume, and relative-strength inputs.",
    }


def _theme_payload(theme: Theme, histories: dict[str, pd.DataFrame], broad: dict | None, growth: dict | None) -> dict:
    etfs: list[dict] = []
    for symbol in theme.etfs:
        metrics = _metrics(histories[symbol]) if symbol in histories else None
        etfs.append({"symbol": symbol, "metrics": metrics})
    returns = [entry["metrics"]["return_20d"] for entry in etfs if entry["metrics"] and entry["metrics"]["return_20d"] is not None]
    average_return = _number(sum(returns) / len(returns)) if returns else None
    benchmark_return = growth["return_20d"] if growth else (broad["return_20d"] if broad else None)
    return {
        "key": theme.key,
        "label": theme.label,
        "etfs": etfs,
        "return_20d": average_return,
        "relative_return_20d": _number(average_return - benchmark_return) if average_return is not None and benchmark_return is not None else None,
        "data_status": "available" if returns else "unavailable",
    }


def build_market_radar(
    now: datetime | None = None,
    history_fetcher: HistoryFetcher = fetch_daily_history,
) -> dict:
    """Build the radar payload. It is local/read-only and never places orders."""
    now = now or datetime.now(timezone.utc)
    config = load_config()
    all_symbols = {config.broad_market, config.growth_market, *config.candidates}
    for theme in config.themes:
        all_symbols.update(theme.etfs)
    histories, failures = history_fetcher(sorted(all_symbols))
    broad = _metrics(histories[config.broad_market]) if config.broad_market in histories else None
    growth = _metrics(histories[config.growth_market]) if config.growth_market in histories else None
    themes = [_theme_payload(theme, histories, broad, growth) for theme in config.themes]
    themes.sort(key=lambda item: item["relative_return_20d"] if item["relative_return_20d"] is not None else -9999, reverse=True)
    candidates = [_candidate(symbol, histories.get(symbol), growth, config, now.date()) for symbol in config.candidates]
    candidates.sort(key=lambda item: item["score"] if item["score"] is not None else -1, reverse=True)

    holdings = build_holdings_index()
    by_symbol = holdings.get("by_symbol", {})
    related: list[dict] = []
    for theme in config.themes:
        matches = sorted(set(theme.symbols) & set(by_symbol))
        if matches:
            related.append(
                {
                    "theme": theme.label,
                    "symbols": [
                        {
                            "symbol": symbol,
                            "strategy_count": by_symbol[symbol]["count"],
                            "near_dte": by_symbol[symbol]["has_near_dte"],
                            "earliest_expiry": by_symbol[symbol]["earliest_expiry"],
                        }
                        for symbol in matches
                    ],
                }
            )
    return {
        "generated_at": now.isoformat(),
        "data_source": source_metadata(),
        "benchmarks": {config.broad_market: broad, config.growth_market: growth},
        "themes": themes,
        "candidates": candidates,
        "holdings": {"data_source": holdings.get("data_source"), "as_of": holdings.get("as_of"), "related_themes": related},
        "failures": failures,
        "limitations": [
            "Candidate scores rank observations only; they are not buy or sell signals.",
            "No institutional-flow claim is inferred from public volume or ETF data.",
            "Earnings and macro gates are only as complete as config/market_radar.yaml.",
            "No live option Greeks, IV, order routing, or broker integration is used.",
        ],
    }
