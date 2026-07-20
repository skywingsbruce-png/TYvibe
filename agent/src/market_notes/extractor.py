"""Deterministic structuring of raw messages into :class:`MarketNote`.

Pure keyword/regex heuristics — no LLM, no network. The guiding rule: a field is
populated only when the source **explicitly** supports it; otherwise it stays
``None`` / ``UNKNOWN``. This module never invents an author, time, price,
horizon, or condition.
"""

from __future__ import annotations

import hashlib
import re

from src.market_notes.models import Direction, Horizon, MarketNote, RawMessage

_EXCERPT_MAX = 600

# ---------------------------------------------------------------------------
# Symbols / indices
# ---------------------------------------------------------------------------

_KNOWN_INDICES = {"SPX", "SPY", "QQQ", "NDX", "DJI", "IWM", "VIX", "RUT", "ES", "NQ"}
# Common all-caps words that are NOT tickers (avoid false positives).
_TICKER_STOPWORDS = {
    "I", "A", "AN", "THE", "OK", "AM", "PM", "ET", "EST", "PST", "UTC", "USD", "US",
    "CEO", "CFO", "IPO", "ATH", "ATL", "DD", "IMO", "IMHO", "TL", "DR", "FYI", "LOL",
    "EOD", "EOW", "YOLO", "FOMO", "PT", "SL", "TP", "OTM", "ITM", "ATM", "IV", "OI",
    "CPI", "PCE", "PPI", "GDP", "FOMC", "PMI", "EPS", "YOY", "QOQ", "AH", "PM",
    "IF", "OR", "AND", "NOT", "BUY", "SELL", "LONG", "SHORT", "CALL", "PUT", "CALLS", "PUTS",
}
# $TICKER (explicit) or bare 2-5 letter uppercase token.
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,6})\b")
_BARE_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")


def _extract_symbols(text: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()

    def add(sym: str) -> None:
        s = sym.upper()
        if s and s not in seen:
            seen.add(s)
            found.append(s)

    for m in _CASHTAG_RE.finditer(text):
        add(m.group(1))
    for m in _BARE_TICKER_RE.finditer(text):
        tok = m.group(1)
        if tok in _KNOWN_INDICES or (tok not in _TICKER_STOPWORDS):
            # A bare uppercase token is only accepted when it is a known index
            # or a plausible ticker not in the stopword list.
            add(tok)
    return tuple(found)


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------

_BEARISH = ("bear", "short", "downside", "breakdown", "put", "puts", "sell", "weak", "fade", "看空", "做空", "空头", "跌", "空")
_BULLISH = ("bull", "long", "upside", "breakout", "call", "calls", "buy", "strong", "rip", "看多", "做多", "多头", "涨", "多")
_NEUTRAL = ("neutral", "chop", "range", "sideways", "flat", "横盘", "震荡", "中性")
_CONDITIONAL = ("if ", "unless", "until", "once ", "as long as", "provided", "如果", "除非", "一旦", "只要", "收复", "站上", "跌破前", "之前")


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def _count_any(text: str, needles: tuple[str, ...]) -> int:
    low = text.lower()
    return sum(low.count(n.lower()) for n in needles)


def _extract_direction(text: str) -> Direction:
    # A note's DIRECTION is its net lean; a conditional caveat ("... until X is
    # reclaimed") is a separate dimension captured in setup/invalidation, so a
    # clear bull/bear lean wins over the conditional marker. CONDITIONAL is used
    # only when the note is contingent with no directional lean.
    bear = _count_any(text, _BEARISH)
    bull = _count_any(text, _BULLISH)
    if bear > bull:
        return Direction.BEARISH
    if bull > bear:
        return Direction.BULLISH
    if _has_any(text, _CONDITIONAL):
        return Direction.CONDITIONAL
    if _has_any(text, _NEUTRAL):
        return Direction.NEUTRAL
    return Direction.UNKNOWN


# ---------------------------------------------------------------------------
# Key levels
# ---------------------------------------------------------------------------

_LEVEL_KEYWORDS = (
    "above", "below", "support", "resistance", "break", "breaks", "reclaim", "target",
    "hold", "over", "under", "上方", "下方", "支撑", "阻力", "关键位", "收复", "站上", "跌破", "目标",
)
_CASH_PRICE_RE = re.compile(r"\$\s?(\d{1,6}(?:\.\d{1,4})?)")
_NEAR_LEVEL_RE = re.compile(
    r"(?:%s)\s*\$?\s*(\d{1,6}(?:\.\d{1,4})?)" % "|".join(re.escape(k) for k in _LEVEL_KEYWORDS),
    re.IGNORECASE,
)
_LEVEL_BEFORE_RE = re.compile(
    r"\$?\s*(\d{1,6}(?:\.\d{1,4})?)\s*(?:%s)" % "|".join(re.escape(k) for k in _LEVEL_KEYWORDS),
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
_WEEKS = ("weeks", "next week", "monthly", "month", "数周", "几周", "下周", "月内", "一个月")


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
    for marker in markers:
        idx = low.find(marker.lower())
        if idx != -1:
            tail = text[idx:]
            # Trim to the end of the sentence / clause.
            clause = re.split(r"[.!?。！？\n]", tail, maxsplit=1)[0].strip()
            return clause or None
    return None


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _note_id(source_ref: str, content_hash: str) -> str:
    digest = hashlib.sha256(f"{source_ref}|{content_hash}".encode("utf-8")).hexdigest()
    return f"n{digest[:8]}"


def structure_message(msg: RawMessage) -> MarketNote:
    """Turn a raw message into a structured :class:`MarketNote` (no guessing)."""
    content = _normalize(msg.text)
    excerpt = msg.text.strip()[:_EXCERPT_MAX]
    content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
    return MarketNote(
        note_id=_note_id(msg.source_ref, content_hash),
        author=msg.author,
        timestamp=msg.timestamp,
        symbols=_extract_symbols(content),
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
