"""Market Notes HTTP route (read-only opinion audit).

Mounted by ``agent/api_server.py`` via ``register_market_notes_routes(app)``.

``GET /market-notes/data`` reads the local note files under
``data/private/market-notes/``, structures them, builds cited conclusions +
conflicts, links symbols to the read-only held book, adds configured theme
associations (reused from ``config/market_radar.yaml``), and de-identified
import diagnostics. It never uploads anything and (unless the optional LLM layer
is explicitly enabled with consent) never leaves the machine. No trading path.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from src.market_notes import holdings as _holdings
from src.market_notes import import_stats as _import_stats
from src.market_notes import themes as _themes
from src.market_notes.service import build_notes_bundle

logger = logging.getLogger(__name__)


def _enrich(bundle: dict) -> dict:
    """Add theme associations, theme→holdings summary, and import diagnostics."""
    defs = _themes.load_theme_defs()
    holdings_index = _holdings.build_holdings_index()
    for note in bundle.get("notes", []):
        note["theme_associations"] = _themes.associate_note(
            note.get("symbols", []), note.get("content", ""), defs
        )
    bundle["themes"] = _themes.themes_summary(bundle.get("notes", []), holdings_index, defs)
    bundle["import_stats"] = _import_stats.build_import_stats()
    return bundle


def _empty_payload(message: str) -> dict:
    return {
        "sources": [],
        "import_errors": [{"file": "", "message": message}],
        "notes": [],
        "current": {
            "available": False,
            "message": "当前结论不可用：内部错误。",
            "windows": {},
            "included_count": 0,
            "earliest": None,
            "latest": None,
            "excluded_unknown_time": 0,
            "excluded_stale": 0,
            "conclusions": [],
            "conflicts": [],
        },
        "holdings": {"data_source": "unavailable", "as_of": None, "by_symbol": {}},
        "themes": {},
        "import_stats": {
            "files": [],
            "errors": [],
            "totals": {"files": 0, "raw_messages": 0, "structured_notes": 0, "parseable_timestamps": 0, "skipped": 0},
            "authors": {"distinct": 0, "per_author": []},
            "channels": {"distinct": 0, "refs": []},
        },
        "llm_enabled": False,
        "llm_privacy": "offline",
        "note_count": 0,
        "source_count": 0,
    }


def register_market_notes_routes(app: FastAPI) -> None:
    """Register the read-only Market Notes route on ``app``."""

    @app.get("/market-notes/data")
    def market_notes() -> dict:  # noqa: ANN202 - FastAPI handler
        try:
            return _enrich(build_notes_bundle())
        except Exception:  # noqa: BLE001 - never leak a stack; degrade cleanly
            logger.exception("Market Notes bundle failed")
            return _empty_payload("internal error building notes")
