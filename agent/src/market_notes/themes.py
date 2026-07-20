"""Theme association for Market Notes.

Theme DEFINITIONS (label + member symbols/ETFs) are REUSED from
``config/market_radar.yaml`` — this module reads that file directly and does not
redefine themes. Keyword hints come from ``config/market_notes_symbols.yaml``
(``theme_keywords``). Two association types are produced and always kept
distinct:

* ``direct_symbol`` — a ticker the note explicitly mentions belongs to the
  theme's members.
* ``configured_keyword`` — a configured keyword (never LLM-inferred) appears in
  the note text.

Nothing here trades, ranks, or fetches live data. Theme→holdings linkage is
information only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.market_notes.config import load_config

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RADAR_CONFIG = _REPO_ROOT / "config" / "market_radar.yaml"


@dataclass(frozen=True)
class ThemeDef:
    """A theme reused from market_radar plus its Market-Notes keywords."""

    key: str
    label: str
    members: frozenset[str]  # symbols ∪ ETFs, upper-cased
    keywords: tuple[str, ...]


def _radar_themes() -> dict[str, dict]:
    if not _RADAR_CONFIG.exists():
        return {}
    try:
        raw = yaml.safe_load(_RADAR_CONFIG.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    themes = raw.get("themes")
    return themes if isinstance(themes, dict) else {}


def load_theme_defs() -> list[ThemeDef]:
    """Load theme definitions (market_radar) merged with configured keywords."""
    keyword_map = load_config().theme_keywords
    defs: list[ThemeDef] = []
    for key, value in _radar_themes().items():
        item = value or {}
        members = {
            str(s).strip().upper()
            for s in list(item.get("symbols") or []) + list(item.get("etfs") or [])
            if str(s).strip()
        }
        defs.append(
            ThemeDef(
                key=str(key),
                label=str(item.get("label") or key),
                members=frozenset(members),
                keywords=keyword_map.get(str(key), ()),
            )
        )
    return defs


def _keyword_hit(text_lower: str, keyword: str) -> bool:
    kw = keyword.strip().lower()
    if not kw:
        return False
    # ASCII alnum/space keywords match on word boundaries to avoid substring
    # noise; non-ASCII (e.g. CJK) keywords match as a plain substring.
    if kw.isascii() and all(c.isalnum() or c.isspace() for c in kw):
        return re.search(rf"\b{re.escape(kw)}\b", text_lower) is not None
    return kw in text_lower


def associate_note(symbols: list[str], content: str, defs: list[ThemeDef]) -> list[dict]:
    """Return this note's theme associations (deterministic, keyword-gated)."""
    upper_syms = [s.upper() for s in symbols]
    low = content.lower()
    out: list[dict] = []
    for d in defs:
        matched_symbols = sorted({s for s in upper_syms if s in d.members})
        matched_keywords = sorted({k for k in d.keywords if _keyword_hit(low, k)})
        if not matched_symbols and not matched_keywords:
            continue
        types: list[str] = []
        if matched_symbols:
            types.append("direct_symbol")
        if matched_keywords:
            types.append("configured_keyword")
        out.append(
            {
                "theme_key": d.key,
                "label": d.label,
                "association_types": types,
                "matched_symbols": matched_symbols,
                "matched_keywords": matched_keywords,
            }
        )
    return out


def themes_summary(notes: list[dict], holdings_index: dict, defs: list[ThemeDef]) -> dict:
    """Summarize themes referenced by any note, with read-only holdings linkage."""
    by_symbol = holdings_index.get("by_symbol", {})
    referenced: dict[str, dict] = {}
    for note in notes:
        for assoc in note.get("theme_associations", []):
            entry = referenced.setdefault(assoc["theme_key"], {"note_count": 0})
            entry["note_count"] += 1

    defs_by_key = {d.key: d for d in defs}
    summary: dict[str, dict] = {}
    for key, ref in referenced.items():
        d = defs_by_key.get(key)
        if d is None:
            continue
        held_symbols = sorted(s for s in d.members if s in by_symbol)
        strategy_count = sum(int(by_symbol[s].get("count", 0)) for s in held_symbols)
        expiries = [by_symbol[s].get("earliest_expiry") for s in held_symbols if by_symbol[s].get("earliest_expiry")]
        earliest = min(expiries) if expiries else None
        near = any(bool(by_symbol[s].get("has_near_dte")) for s in held_symbols)
        summary[key] = {
            "label": d.label,
            "members": sorted(d.members),
            "note_count": ref["note_count"],
            "held_symbols": held_symbols,
            "strategy_count": strategy_count,
            "earliest_expiry": earliest,
            "has_near_dte": near,
        }
    return summary
