"""Options Studio HTTP routes for the Web UI (Phase 2 v0 - read-only).

Mounted by ``agent/api_server.py`` via ``register_options_studio_routes(app)``.

Exposes one read-only endpoint that runs the local held-book reconciliation and
returns its de-identified payload (no account number, name, or address):

- ``GET /options-studio/review`` - reconcile the local statement and return
  ``{snapshot, portfolio_risk, rules, warnings, data_source}``.

Data source: the user's real ``data/private/ibkr_statement.csv`` when present;
otherwise a bundled, entirely fictional sample so the dashboard renders before
any real export is placed. The response is always local — no network, no LLM,
no broker. ``data_source`` is ``"real"`` or ``"sample"`` so the UI can flag it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from src.options_studio import storage
from src.options_studio.reconciliation import run_reconciliation

logger = logging.getLogger(__name__)

# Bundled fictional sample (agent/src/options_studio/samples/...). Path is
# derived from this module's location so it tracks the package layout.
_SAMPLE_STATEMENT = (
    Path(__file__).resolve().parents[1] / "options_studio" / "samples" / "ibkr_sample_statement.csv"
)
_REAL_STATEMENT_NAME = "ibkr_statement.csv"


def _load_statement() -> tuple[str, str]:
    """Return ``(text, data_source)`` for the review, preferring the real file."""
    real = storage.get_private_dir() / _REAL_STATEMENT_NAME
    if real.exists():
        return real.read_text(encoding="utf-8", errors="replace"), "real"
    return _SAMPLE_STATEMENT.read_text(encoding="utf-8"), "sample"


def register_options_studio_routes(app: FastAPI) -> None:
    """Register the read-only Options Studio routes on ``app``."""

    @app.get("/options-studio/review")
    def options_studio_review() -> dict:  # noqa: ANN202 - FastAPI handler
        text, data_source = _load_statement()
        try:
            result = run_reconciliation(text)
        except Exception:  # noqa: BLE001 - surface a clean error, never a stack
            logger.exception("Options Studio reconciliation failed")
            return {"error": "reconciliation_failed", "data_source": data_source}
        payload = result.to_payload()
        payload["data_source"] = data_source
        return payload
