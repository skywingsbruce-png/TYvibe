"""Timestamp parsing for Market Notes.

Returns an aware UTC ``datetime`` when a value parses to an unambiguous time,
else ``None``. Naive inputs are assumed UTC. Nothing is guessed: an unparseable
value yields ``None`` so the note is kept in history but excluded from the
time-windowed current-conclusion card.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse a timestamp string to an aware UTC datetime, or ``None``."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None

    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso)
        return _as_utc(dt)
    except ValueError:
        pass

    for fmt in _FORMATS:
        try:
            return _as_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
