"""Market Notes data contracts (frozen dataclasses).

These are the stable boundary between the importers, the deterministic
extractor, the conclusion builder, the holdings linker, the optional LLM layer,
and the API / frontend.

Sentinel discipline: a field that is not explicitly present in the source is
``None`` in the model and rendered as ``"unknown"`` / ``"unavailable"`` in the
payload — it is never guessed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

#: Reserved outcome fields are always this in v1 (no historical-quote backfill).
UNAVAILABLE = "unavailable"


class Direction(str, Enum):
    """Opinion direction. ``UNKNOWN`` when the text does not state one."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class Horizon(str, Enum):
    """Opinion time horizon. ``UNKNOWN`` when the text does not state one."""

    INTRADAY = "intraday"
    DAYS = "days"
    WEEKS = "weeks"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceFile:
    """A raw note file discovered under ``data/private/market-notes/``.

    Attributes:
        name: Bare filename (never a full path — paths are internal topology).
        kind: Importer label that handled it (e.g. ``"discord_json"``).
        imported_at: ISO8601 of the file's last-modified time (the honest
            "when it arrived" signal — never fabricated).
        message_count: Number of messages/blocks parsed from the file.
        sha256: Hash of the raw file bytes for traceability.
    """

    name: str
    kind: str
    imported_at: str
    message_count: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketNote:
    """One structured opinion extracted from a source message.

    Every ``Optional`` field is ``None`` when the source did not state it; the
    payload renders that as ``unknown``/``unavailable``. Nothing is guessed.

    Attributes:
        note_id: Stable id ``"n<8-hex>"`` derived from source_ref + content hash.
        author: Message author, or ``None`` if the export omits it.
        timestamp: ISO8601 message time, or ``None`` if absent.
        symbols: Tuple of upper-cased tickers/indices/themes mentioned.
        direction: :class:`Direction` (``UNKNOWN`` when not stated).
        content: The opinion text (normalized whitespace).
        key_levels: Tuple of explicit price levels mentioned (empty if none).
        horizon: :class:`Horizon` (``UNKNOWN`` when not stated).
        setup_condition: Text of a stated setup/trigger condition, or ``None``.
        invalidation_condition: Text of a stated invalidation, or ``None``.
        excerpt: Verbatim original excerpt (bounded length).
        source_file: Bare source filename.
        source_ref: In-file locator (e.g. ``"discord:msg=123"``, ``"line:42"``).
        content_hash: SHA256 of the excerpt for traceability/dedup.
        outcome_1d/1w/1m: Reserved. Always :data:`UNAVAILABLE` in v1.
    """

    note_id: str
    author: Optional[str]
    timestamp: Optional[str]
    symbols: tuple[str, ...]
    direction: Direction
    content: str
    key_levels: tuple[str, ...]
    horizon: Horizon
    setup_condition: Optional[str]
    invalidation_condition: Optional[str]
    excerpt: str
    source_file: str
    source_ref: str
    content_hash: str
    outcome_1d: str = UNAVAILABLE
    outcome_1w: str = UNAVAILABLE
    outcome_1m: str = UNAVAILABLE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["symbols"] = list(self.symbols)
        data["key_levels"] = list(self.key_levels)
        data["direction"] = self.direction.value
        data["horizon"] = self.horizon.value
        # Render absent structured fields as explicit sentinels for the UI.
        for key in ("author", "timestamp", "setup_condition", "invalidation_condition"):
            if data[key] is None:
                data[key] = "unknown"
        return data


@dataclass(frozen=True)
class RawMessage:
    """A pre-structuring message yielded by an importer."""

    author: Optional[str]
    timestamp: Optional[str]
    text: str
    source_file: str
    source_ref: str


@dataclass(frozen=True)
class Citation:
    """A conclusion's traceable reference back to a source note."""

    note_id: str
    excerpt: str
    source_file: str
    source_ref: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Conclusion:
    """One aggregated conclusion with mandatory citations.

    Attributes:
        conclusion_id: Stable id within a bundle.
        text: Human-readable conclusion statement.
        origin: ``"deterministic"`` or ``"llm"``.
        needs_review: True when the conclusion could not be traced to a valid
            citation (e.g. an LLM answer with no usable reference). Such an item
            is shown as "needs manual review", never as a formal conclusion.
        citations: Source notes this conclusion draws from (>=1 when valid).
    """

    conclusion_id: str
    text: str
    origin: str
    needs_review: bool
    citations: tuple[Citation, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conclusion_id": self.conclusion_id,
            "text": self.text,
            "origin": self.origin,
            "needs_review": self.needs_review,
            "citations": [c.to_dict() for c in self.citations],
        }


@dataclass(frozen=True)
class Conflict:
    """A symbol carrying contradictory opinions across notes."""

    symbol: str
    directions: tuple[str, ...]
    note_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "directions": list(self.directions), "note_ids": list(self.note_ids)}
