"""Read-only Market Radar route."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from src.market_radar.service import build_market_radar

logger = logging.getLogger(__name__)


def register_market_radar_routes(app: FastAPI) -> None:
    """Expose market observations only; this router never contains an order path."""

    @app.get("/market-radar/data")
    def market_radar() -> dict:  # noqa: ANN202 - FastAPI handler
        try:
            return build_market_radar()
        except Exception:  # noqa: BLE001 - visible degradation, no fabricated market result
            logger.exception("Market Radar bundle failed")
            return {
                "generated_at": None,
                "data_source": {"name": "unavailable", "grade": "unavailable", "execution_safe": False},
                "benchmarks": {},
                "themes": [],
                "candidates": [],
                "holdings": {"data_source": "unavailable", "as_of": None, "related_themes": []},
                "failures": {"radar": "internal error building market radar"},
                "limitations": ["No market result was produced."],
            }
