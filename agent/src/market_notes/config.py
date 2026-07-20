"""Market Notes configuration: symbol recognition + conclusion time windows.

Loads ``config/market_notes_symbols.yaml`` with conservative embedded defaults
so the feature works even if the file is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _REPO_ROOT / "config" / "market_notes_symbols.yaml"

_DEFAULT_INDICES = {"SPX", "SPY", "QQQ", "NDX", "VIX", "ES", "NQ", "IWM", "DIA", "RUT"}
_DEFAULT_WHITELIST: set[str] = set()
_DEFAULT_WINDOWS = {"intraday_hours": 24, "days_days": 7, "weeks_days": 30, "unknown_days": 7}


@dataclass(frozen=True)
class NotesConfig:
    """Resolved Market Notes config."""

    indices: frozenset[str]
    whitelist: frozenset[str]
    windows: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "indices": sorted(self.indices),
            "whitelist_size": len(self.whitelist),
            "windows": dict(self.windows),
        }


def _load_raw() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def load_config() -> NotesConfig:
    """Return the resolved config (file merged over defaults)."""
    raw = _load_raw()
    indices = {str(s).strip().upper() for s in (raw.get("indices") or [])} or set(_DEFAULT_INDICES)
    indices |= _DEFAULT_INDICES  # indices are additive; never lose the core set
    whitelist = {str(s).strip().upper() for s in (raw.get("whitelist") or [])} | _DEFAULT_WHITELIST
    windows = dict(_DEFAULT_WINDOWS)
    for key, val in (raw.get("windows") or {}).items():
        try:
            windows[key] = int(val)
        except (TypeError, ValueError):
            continue
    return NotesConfig(indices=frozenset(indices), whitelist=frozenset(whitelist), windows=windows)
