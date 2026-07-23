"""Configuration for the personal, read-only market radar."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _REPO_ROOT / "config" / "market_radar.yaml"


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    etfs: tuple[str, ...]
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class EventGate:
    name: str
    date: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class RadarConfig:
    broad_market: str
    growth_market: str
    themes: tuple[Theme, ...]
    candidates: tuple[str, ...]
    events: tuple[EventGate, ...]
    event_window_days: int
    event_calendar_complete: bool


def _symbols(values: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip().upper() for value in (values or []) if str(value).strip()))


def load_config() -> RadarConfig:
    """Load the repository-owned, human-auditable radar configuration."""
    raw: dict[str, Any] = {}
    if _CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            raw = {}

    benchmarks = raw.get("benchmarks") or {}
    themes: list[Theme] = []
    for key, value in (raw.get("themes") or {}).items():
        item = value or {}
        themes.append(
            Theme(
                key=str(key),
                label=str(item.get("label") or key),
                etfs=_symbols(item.get("etfs")),
                symbols=_symbols(item.get("symbols")),
            )
        )
    events = tuple(
        EventGate(
            name=str(item.get("name") or "Unnamed event"),
            date=str(item.get("date") or ""),
            symbols=_symbols(item.get("symbols")),
        )
        for item in (raw.get("events") or [])
        if isinstance(item, dict) and item.get("date")
    )
    return RadarConfig(
        broad_market=str(benchmarks.get("broad_market") or "SPY").upper(),
        growth_market=str(benchmarks.get("growth_market") or "QQQ").upper(),
        themes=tuple(themes),
        candidates=_symbols(raw.get("candidates")),
        events=events,
        event_window_days=max(0, int(raw.get("event_window_days") or 7)),
        event_calendar_complete=bool(raw.get("event_calendar_complete", False)),
    )
