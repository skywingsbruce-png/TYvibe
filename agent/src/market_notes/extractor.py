"""Deterministic structuring of raw messages into :class:`MarketNote`.

Pure keyword/regex heuristics — no LLM, no network. Two hardening rules:

* **Symbols** are recognized only from an explicit ``$CASHTAG`` or from a known
  set (indices + held-book underlyings + the maintained whitelist). Bare
  all-caps chat noise (``FUD``, ``TACO``, ``AI``, ``LOL``) is left as plain text.
* **Direction** requires an explicit sentiment token with word boundaries
  (``bearish`` / ``bullish`` / ``看空`` / ``看多`` / ``跌破`` / ``突破`` …). Phrases
  like ``short term`` / ``long term`` / ``I have a put`` do NOT imply a
  direction. When nothing explicit is present the direction is ``UNKNOWN`` —
  never guessed.
"""

from __future__ import annotations

import hashlib
import re

from src.market_notes.models import Direction, Horizon, MarketNote, RawMessage

_EXCERPT_MAX = 600

# ---------------------------------------------------------------------------
# Symbols — explicit cashtag OR a member of a known set (never bare-token guess)
# ---------------------------------------------------------------------------

_CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z.\-]{0,5})\b")
_BARE_TOKEN_RE = re.compile(r"\b([A-Z]{1,6})\b")


def _extract_symbols(text: str, known: frozenset[str]) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()

    def add(sym: str) -> None:
        s = sym.upper()
        if s and s not in seen:
            seen.add(s)
            found.append(s)

    # Explicit cashtags are always accepted (deliberate user intent).
    for m in _CASHTAG_RE.finditer(text):
        add(m.group(1))
    # Bare uppercase tokens are accepted ONLY when known (index / held / whitelist).
    for m in _BARE_TOKEN_RE.finditer(text):
        tok = m.group(1).upper()
        if tok in known:
            add(tok)
    return tuple(found)


# ---------------------------------------------------------------------------
# Direction — explicit sentiment only
# ---------------------------------------------------------------------------

_BULLISH_RE = re.compile(r"\bbullish\b|看多|做多|突破|站上", re.IGNORECASE)
_BEARISH_RE = re.compile(r"\bbearish\b|看空|做空|跌破|破位", re.IGNORECASE)
_NEUTRAL_RE = re.compile(r"\bneutral\b|中性", re.IGNORECASE)


def _extract_direction(text: str) -> Direction:
    bull = bool(_BULLISH_RE.search(text))
    bear = bool(_BEARISH_RE.search(text))
    if bull and not bear:
        return Direction.BULLISH
    if bear and not bull:
        return Direction.BEARISH
    if bull and bear:
        return Direction.UNKNOWN  # mixed sentiment in one message -> not resolved
    if _NEUTRAL_RE.search(text):
        return Direction.NEUTRAL
    return Direction.UNKNOWN


# ---------------------------------------------------------------------------
# Key levels
# ---------------------------------------------------------------------------

_LEVEL_KEYWORDS = (
    "above", "below", "support", "resistance", "break", "breaks", "reclaim", "target", "hold", "over", "under",
    "上方", "下方", "支撑", "阻力", "关键位", "收复", "站上", "跌破", "突破", "目标",
)
_CASH_PRICE_RE = re.compile(r"\$\s?(\d{1,7}(?:\.\d{1,4})?)")
_NEAR_LEVEL_RE = re.compile(
    r"(?:%s)\s*\$?\s*(\d{1,7}(?:\.\d{1,4})?)" % "|".join(re.escape(k) for k in _LEVEL_KEYWORDS),
    re.IGNORECASE,
)
_LEVEL_BEFORE_RE = re.compile(
    r"\$?\s*(\d{1,7}(?:\.\d{1,4})?)\s*(?:%s)" % "|".join(re.escape(k) for k in _LEVEL_KEYWORDS),
    re.IGNORECASE,
)


def _extract_key_levels(text: str) -> tuple[str, ...]:
    levels: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        if v not in seen:
            seen.add(v)
            levels.append(v)

    for rx in (_CASH_PRICE_RE, _NEAR_LEVEL_RE, _LEVEL_BEFORE_RE):
        for m in rx.finditer(text):
            add(m.group(1))
    return tuple(levels)


# ---------------------------------------------------------------------------
# Horizon
# ---------------------------------------------------------------------------

_INTRADAY = ("intraday", "today", "0dte", "scalp", "day trade", "eod", "盘中", "日内", "今天", "尾盘", "盘后")
_DAYS = ("few days", "couple days", "this week", "swing", "next day", "数日", "几天", "本周", "近几日")
_WEEKS = ("weeks", "next week", "monthly", "long term", "数周", "几周", "下周", "月内", "一个月", "中长期")


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def _extract_horizon(text: str) -> Horizon:
    if _has_any(text, _INTRADAY):
        return Horizon.INTRADAY
    if _has_any(text, _WEEKS):
        return Horizon.WEEKS
    if _has_any(text, _DAYS):
        return Horizon.DAYS
    return Horizon.UNKNOWN


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

_SETUP_MARKERS = ("if ", "once ", "when ", "as long as", "如果", "一旦", "当", "只要")
_INVALIDATION_MARKERS = ("unless", "until", "invalidated", "invalid if", "stop ", "stopped", "除非", "失效", "跌破", "收复")


def _extract_clause(text: str, markers: tuple[str, ...]) -> str | None:
    low = text.lower()
    best: tuple[int, str] | None = None
    for marker in markers:
        idx = low.find(marker.lower())
        if idx != -1 and (best is None or idx < best[0]):
            tail = text[idx:]
            clause = re.split(r"[.!?。！？\n]", tail, maxsplit=1)[0].strip()
            if clause:
                best = (idx, clause)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _note_id(source_ref: str, content_hash: str) -> str:
    digest = hashlib.sha256(f"{source_ref}|{content_hash}".encode("utf-8")).hexdigest()
    return f"n{digest[:8]}"


def structure_message(msg: RawMessage, known_symbols: frozenset[str] = frozenset()) -> MarketNote:
    """Turn a raw message into a structured :class:`MarketNote` (no guessing).

    Args:
        msg: The raw message.
        known_symbols: Bare tokens accepted as symbols (indices + held-book
            underlyings + whitelist). Explicit ``$cashtags`` are always accepted.
    """
    content = _normalize(msg.text)
    excerpt = msg.text.strip()[:_EXCERPT_MAX]
    content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    return MarketNote(
        note_id=_note_id(msg.source_ref, content_hash),
        author=msg.author,
        timestamp=msg.timestamp,
        symbols=_extract_symbols(content, known_symbols),
        direction=_extract_direction(content),
        content=content,
        key_levels=_extract_key_levels(content),
        horizon=_extract_horizon(content),
        setup_condition=_extract_clause(content, _SETUP_MARKERS),
        invalidation_condition=_extract_clause(content, _INVALIDATION_MARKERS),
        excerpt=excerpt,
        source_file=msg.source_file,
        source_ref=msg.source_ref,
        content_hash=content_hash,
    )
