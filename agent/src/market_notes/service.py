"""Market Notes assembly: import -> structure -> time-windowed conclude -> link.

Pure orchestration over the local files. Returns a de-identified, JSON-safe
payload. Runs fully offline; the optional LLM layer only augments the current
conclusions when remote consent is explicitly granted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.market_notes import conclusions as _conclusions
from src.market_notes import holdings as _holdings
from src.market_notes import llm as _llm
from src.market_notes import storage as _storage
from src.market_notes.config import load_config
from src.market_notes.extractor import structure_message
from src.market_notes.importers import import_file
from src.market_notes.importers.base import ImportError_
from src.market_notes.models import MarketNote, SourceFile
from src.market_notes.timeutil import parse_timestamp


def _note_payload(note: MarketNote) -> dict:
    data = note.to_dict()
    parsed = parse_timestamp(note.timestamp)
    data["timestamp_parsed"] = parsed.isoformat() if parsed else None
    data["timestamp_ok"] = parsed is not None
    return data


def build_notes_bundle(now: Optional[datetime] = None) -> dict:
    """Read the local note files and return the full de-identified payload."""
    now = now or datetime.now(timezone.utc)
    config = load_config()

    holdings_index = _holdings.build_holdings_index()
    held_underlyings = {s.upper() for s in holdings_index.get("by_symbol", {})}
    known_symbols = frozenset(config.indices | config.whitelist | held_underlyings)

    sources: list[SourceFile] = []
    import_errors: list[dict[str, str]] = []
    notes: list[MarketNote] = []
    seen_ids: set[str] = set()

    for path in _storage.list_source_files():
        try:
            messages, kind = import_file(path)
        except ImportError_ as exc:
            import_errors.append({"file": path.name, "message": str(exc)})
            continue
        sources.append(
            SourceFile(
                name=path.name,
                kind=kind,
                imported_at=_storage.imported_at_iso(path),
                message_count=len(messages),
                sha256=_storage.sha256_of(path),
            )
        )
        for msg in messages:
            note = structure_message(msg, known_symbols)
            if note.note_id in seen_ids:
                continue
            seen_ids.add(note.note_id)
            notes.append(note)

    current_notes, _unknown_time, _stale = _conclusions.select_current_notes(
        notes, now=now, windows=config.windows
    )
    current = _conclusions.build_current(notes, now=now, windows=config.windows)

    # Optional LLM conclusions augment the CURRENT card only, and only with
    # explicit remote consent. Fully offline otherwise.
    if current["available"]:
        llm_conclusions = _llm.generate_llm_conclusions([note for note, _dt in current_notes])
        current["conclusions"].extend(c.to_dict() for c in llm_conclusions)

    note_symbols = {s for n in notes for s in n.symbols}
    linked = _holdings.link_symbols(note_symbols, holdings_index)

    return {
        "sources": [s.to_dict() for s in sources],
        "import_errors": import_errors,
        "notes": [_note_payload(n) for n in notes],
        "current": current,
        "holdings": {
            "data_source": holdings_index.get("data_source", "none"),
            "as_of": holdings_index.get("as_of"),
            "by_symbol": linked,
        },
        "llm_enabled": _llm.llm_enabled(),
        "llm_privacy": _llm.privacy_status(),
        "note_count": len(notes),
        "source_count": len(sources),
        "generated_at": now.isoformat(),
    }
