"""Tests for the deterministic, read-only market radar."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import src.market_radar.service as service
from src.market_radar.config import RadarConfig, Theme


def _frame(start: float, end: float, *, final_volume: float = 100.0) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=70, freq="B", tz="UTC")
    closes = [start + ((end - start) * idx / 69) for idx in range(70)]
    volumes = [100.0] * 69 + [final_volume]
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=index)


def _config(*, complete_events: bool = False) -> RadarConfig:
    return RadarConfig(
        broad_market="SPY",
        growth_market="QQQ",
        themes=(Theme("semis", "Semiconductors", ("SMH",), ("AAA",)),),
        candidates=("AAA", "MISS"),
        events=(),
        event_window_days=7,
        event_calendar_complete=complete_events,
    )


def test_radar_ranks_candidates_but_never_labels_an_entry(monkeypatch):
    histories = {
        "SPY": _frame(100, 110),
        "QQQ": _frame(100, 110),
        "SMH": _frame(100, 120),
        "AAA": _frame(100, 130, final_volume=200),
    }
    monkeypatch.setattr(service, "load_config", lambda: _config())
    payload = service.build_market_radar(
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        history_fetcher=lambda _symbols: (histories, {"MISS": "not found"}),
    )

    candidate = next(item for item in payload["candidates"] if item["symbol"] == "AAA")
    assert candidate["status"] == "watch"
    assert candidate["score"] is not None
    assert candidate["score_max"] == 80  # no complete event calendar -> no invented 20 points
    assert "not an entry signal" in candidate["setup"]["observation"]
    assert candidate["components"][-1]["status"] == "unavailable"


def test_missing_public_data_is_visible_not_fabricated(monkeypatch):
    monkeypatch.setattr(service, "load_config", lambda: _config())
    payload = service.build_market_radar(
        now=datetime(2026, 7, 21, tzinfo=timezone.utc),
        history_fetcher=lambda _symbols: ({}, {"AAA": "not found", "MISS": "not found"}),
    )

    assert all(candidate["score"] is None for candidate in payload["candidates"])
    assert payload["failures"]["AAA"] == "not found"
    assert payload["themes"][0]["data_status"] == "unavailable"


def test_no_trading_path_in_market_radar_package():
    pkg_dir = Path(__file__).resolve().parent.parent / "src" / "market_radar"
    forbidden = ("place_order", "submit_order", "buy_to_open", "sell_to_open", "ENABLE_REAL_TRADING=")
    for py in pkg_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} found in {py.name}"
