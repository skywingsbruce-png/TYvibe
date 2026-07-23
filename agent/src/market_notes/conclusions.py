"""Time-windowed current-conclusion + conflict building.

The "current conclusions" card must not mix stale or unknown-time opinions with
fresh ones. An opinion enters the card only when its timestamp parses AND its
age is within the window for its horizon (intraday / days / weeks / unknown).
Every conclusion cites its source note(s); contradictory opinions **within the
same symbol and horizon bucket** surface as a conflict, never a forced single
direction. No LLM is involved.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from src.market_notes.models import Citation, Conclusion, Conflict, Direction, MarketNote
from src.market_notes.timeutil import parse_timestamp

_EXCERPT_CAP = 240

# Horizon -> (bucket label, window-config key).
_BUCKET = {
    "intraday": ("intraday", "intraday_hours", True),
    "days": ("multi-day", "days_days", False),
    "weeks": ("multi-week", "weeks_days", False),
    "unknown": ("unspecified-horizon", "unknown_days", False),
}


def _citation(note: MarketNote) -> Citation:
    return Citation(
        note_id=note.note_id,
        excerpt=note.excerpt[:_EXCERPT_CAP],
        source_file=note.source_file,
        source_ref=note.source_ref,
    )


def _window_delta(horizon: str, windows: dict[str, int]) -> timedelta:
    _label, key, is_hours = _BUCKET.get(horizon, _BUCKET["unknown"])
    value = int(windows.get(key, 7))
    return timedelta(hours=value) if is_hours else timedelta(days=value)


def _window_desc(horizon: str, windows: dict[str, int]) -> str:
    _label, key, is_hours = _BUCKET.get(horizon, _BUCKET["unknown"])
    value = int(windows.get(key, 7))
    return f"{value}h" if is_hours else f"{value}d"


def select_current_notes(
    notes: list[MarketNote], *, now: datetime, windows: dict[str, int]
) -> tuple[list[tuple[MarketNote, datetime]], int, int]:
    """Select only timestamped notes that belong in the current window.

    The deterministic card and any optional LLM augmentation must consume this
    exact same selection. Unknown-time and stale notes remain visible in
    history, but are never evidence for a current conclusion.
    """
    included: list[tuple[MarketNote, datetime]] = []
    excluded_unknown_time = 0
    excluded_stale = 0

    for note in notes:
        parsed = parse_timestamp(note.timestamp)
        if parsed is None:
            excluded_unknown_time += 1
            continue
        if now - parsed <= _window_delta(note.horizon, windows):
            included.append((note, parsed))
        else:
            excluded_stale += 1

    return included, excluded_unknown_time, excluded_stale


def build_current(notes: list[MarketNote], *, now: datetime, windows: dict[str, int]) -> dict:
    """Build the time-windowed current-conclusion card payload."""
    included, excluded_unknown_time, excluded_stale = select_current_notes(
        notes, now=now, windows=windows
    )

    windows_desc = {
        "intraday": _window_desc("intraday", windows),
        "days": _window_desc("days", windows),
        "weeks": _window_desc("weeks", windows),
        "unknown": _window_desc("unknown", windows),
    }

    if not included:
        return {
            "available": False,
            "message": "当前结论不可用：没有符合时间窗口且时间可验证的观点。 "
            "(No time-verifiable opinions inside the freshness window.)",
            "windows": windows_desc,
            "included_count": 0,
            "earliest": None,
            "latest": None,
            "excluded_unknown_time": excluded_unknown_time,
            "excluded_stale": excluded_stale,
            "conclusions": [],
            "conflicts": [],
        }

    times = [dt for _n, dt in included]
    conclusions, conflicts = _aggregate(included, windows_desc)

    return {
        "available": True,
        "message": None,
        "windows": windows_desc,
        "included_count": len(included),
        "earliest": min(times).isoformat(),
        "latest": max(times).isoformat(),
        "excluded_unknown_time": excluded_unknown_time,
        "excluded_stale": excluded_stale,
        "conclusions": [c.to_dict() for c in conclusions],
        "conflicts": [c.to_dict() for c in conflicts],
    }


def _aggregate(
    included: list[tuple[MarketNote, datetime]],
    windows_desc: dict[str, str],
) -> tuple[list[Conclusion], list[Conflict]]:
    # Group by (symbol, horizon bucket) so an old multi-week bull and a fresh
    # intraday bear are never mislabeled as a live conflict.
    groups: dict[tuple[str, str], list[MarketNote]] = defaultdict(list)
    for note, _dt in included:
        for sym in note.symbols:
            groups[(sym, note.horizon)].append(note)

    conclusions: list[Conclusion] = []
    conflicts: list[Conflict] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"c{counter}"

    for (symbol, horizon) in sorted(groups):
        group = groups[(symbol, horizon)]
        label = _BUCKET.get(horizon, _BUCKET["unknown"])[0]
        wdesc = windows_desc.get(horizon, "")
        dirs = {n.direction for n in group}
        cites = tuple(_citation(n) for n in group)

        if Direction.BULLISH in dirs and Direction.BEARISH in dirs:
            conflicts.append(
                Conflict(
                    symbol=symbol,
                    directions=tuple(sorted(d.value for d in dirs if d != Direction.UNKNOWN)),
                    note_ids=tuple(n.note_id for n in group),
                )
            )
            conclusions.append(
                Conclusion(
                    conclusion_id=next_id(),
                    text=f"{label} opinions on {symbol} CONFLICT (bullish and bearish within the last {wdesc}) — not resolving to one direction.",
                    origin="deterministic",
                    needs_review=False,
                    citations=cites,
                )
            )
            continue

        stance = next((d for d in (Direction.BEARISH, Direction.BULLISH, Direction.NEUTRAL) if d in dirs), None)
        if stance is not None:
            conclusions.append(
                Conclusion(
                    conclusion_id=next_id(),
                    text=f"{label} opinion on {symbol} leans {stance.value} ({len(group)} note(s) within the last {wdesc}).",
                    origin="deterministic",
                    needs_review=False,
                    citations=cites,
                )
            )

    # Echo explicit invalidation conditions from included notes.
    for note, _dt in included:
        if note.invalidation_condition:
            sym = note.symbols[0] if note.symbols else "the underlying"
            conclusions.append(
                Conclusion(
                    conclusion_id=next_id(),
                    text=f"Stated invalidation for {sym}: \"{note.invalidation_condition}\" — until then the opposite move is not treated as confirmation.",
                    origin="deterministic",
                    needs_review=False,
                    citations=(_citation(note),),
                )
            )

    return conclusions, conflicts
