"""Market Notes assembly: import -> structure -> conclude -> link.

Pure orchestration over the local files. Returns a de-identified, JSON-safe
payload for the API / frontend. Runs fully offline; the optional LLM layer only
augments the deterministic conclusions when enabled.
"""

from __future__ import annotations

from src.market_notes import conclusions as _conclusions
from src.market_notes import holdings as _holdings
from src.market_notes import llm as _llm
from src.market_notes import storage as _storage
from src.market_notes.extractor import structure_message
from src.market_notes.importers import import_file
from src.market_notes.importers.base import ImportError_
from src.market_notes.models import MarketNote, SourceFile


def build_notes_bundle() -> dict:
    """Read the local note files and return the full de-identified payload."""
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
            note = structure_message(msg)
            if note.note_id in seen_ids:
                continue  # de-dup identical excerpts from the same locator
            seen_ids.add(note.note_id)
            notes.append(note)

    conflicts = _conclusions.build_conflicts(notes)
    all_conclusions = _conclusions.build_conclusions(notes)
    all_conclusions.extend(_llm.generate_llm_conclusions(notes))

    holdings_index = _holdings.build_holdings_index()
    note_symbols = {s for n in notes for s in n.symbols}
    linked = _holdings.link_symbols(note_symbols, holdings_index)

    return {
        "sources": [s.to_dict() for s in sources],
        "import_errors": import_errors,
        "notes": [n.to_dict() for n in notes],
        "conclusions": [c.to_dict() for c in all_conclusions],
        "conflicts": [c.to_dict() for c in conflicts],
        "holdings": {
            "data_source": holdings_index.get("data_source", "none"),
            "as_of": holdings_index.get("as_of"),
            "by_symbol": linked,
        },
        "llm_enabled": _llm.llm_enabled(),
        "note_count": len(notes),
        "source_count": len(sources),
    }
