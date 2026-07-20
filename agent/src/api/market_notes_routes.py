"""Market Notes HTTP route (Phase 1 - read-only opinion audit).

Mounted by ``agent/api_server.py`` via ``register_market_notes_routes(app)``.

``GET /market-notes/data`` reads the local note files under
``data/private/market-notes/``, structures them, builds cited conclusions +
conflicts, and links symbols to the read-only held book. It never uploads
anything and (unless the optional LLM layer is explicitly enabled) never leaves
the machine. There is no trading path.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from src.market_notes.service import build_notes_bundle

logger = logging.getLogger(__name__)


def register_market_notes_routes(app: FastAPI) -> None:
    """Register the read-only Market Notes route on ``app``."""

    @app.get("/market-notes/data")
    def market_notes() -> dict:  # noqa: ANN202 - FastAPI handler
        try:
            return build_notes_bundle()
        except Exception:  # noqa: BLE001 - never leak a stack; degrade cleanly
            logger.exception("Market Notes bundle failed")
            return {
                "sources": [],
                "import_errors": [{"file": "", "message": "internal error building notes"}],
                "notes": [],
                "conclusions": [],
                "conflicts": [],
                "holdings": {"data_source": "unavailable", "as_of": None, "by_symbol": {}},
                "llm_enabled": False,
                "note_count": 0,
                "source_count": 0,
            }
