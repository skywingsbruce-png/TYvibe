"""Options Studio HTTP routes for the Web UI (Phase 2 v0 - read-only).

Mounted by ``agent/api_server.py`` via ``register_options_studio_routes(app)``.

``GET /options-studio/review`` runs the local held-book reconciliation and
returns a de-identified payload (no account number, name, or address).

Data-safety contract (Phase 2 v0 hardening):

* The bundled fictional **sample** is returned **only** when the user's real
  ``data/private/ibkr_statement.csv`` does not exist at all.
* When the real file exists, the response is always ``data_source="real"`` — it
  never falls back to the sample. If the real statement fails to parse, or its
  reconciliation surfaces a risk-invalidating condition (``unresolved_option``,
  ``unsupported_asset_category``, ``duplicate_position``, ``duplicate_trade``,
  ``cash_not_found``, or any ``unclassified`` strategy), the response sets
  ``risk_usable=false`` and lists ``blocking_issues`` so the UI can refuse to
  present the risk aggregate as trustworthy.
* ``risk_usable=true`` only when real (or sample) data parsed cleanly.

Everything is local — no network, no LLM, no broker.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from src.options_studio import storage
from src.options_studio.reconciliation import run_reconciliation

logger = logging.getLogger(__name__)

# Bundled fictional sample = the single source of truth for the demo. Derived
# from the sample CSV at request time (no duplicated JSON artifact).
_SAMPLE_STATEMENT = (
    Path(__file__).resolve().parents[1] / "options_studio" / "samples" / "ibkr_sample_statement.csv"
)
_REAL_STATEMENT_NAME = "ibkr_statement.csv"

# Warning codes that invalidate the risk aggregate for REAL data: they mean a
# held position is missing, double-counted, or misinterpreted.
_RISK_BLOCKING_WARNING_CODES = frozenset(
    {
        "unresolved_option",
        "unsupported_asset_category",
        "duplicate_position",
        "duplicate_trade",
        "cash_not_found",
    }
)


def _real_statement_path() -> Path:
    return storage.get_private_dir() / _REAL_STATEMENT_NAME


def _blocking_issues(result: Any) -> list[dict[str, str]]:
    """Collect risk-invalidating conditions from a reconciliation result."""
    issues: list[dict[str, str]] = []
    for w in result.warnings:
        if w.code in _RISK_BLOCKING_WARNING_CODES:
            issues.append({"code": w.code, "message": w.message, "context": w.context})
    unclassified = result.portfolio_risk.unclassified_strategies
    if unclassified > 0:
        issues.append(
            {
                "code": "unclassified_strategies",
                "message": f"{unclassified} strategy(ies) could not be classified.",
                "context": "",
            }
        )
    return issues


def build_review_payload() -> dict:
    """Build the review payload with the hardened data-safety semantics."""
    real = _real_statement_path()

    # 1) No real file at all -> the fictional sample (clean by construction).
    if not real.exists():
        result = run_reconciliation(_SAMPLE_STATEMENT.read_text(encoding="utf-8"))
        payload = result.to_payload()
        payload.update(data_source="sample", risk_usable=True, blocking_issues=[], error=None)
        return payload

    # 2) Real file exists -> always data_source="real", never fall back to sample.
    text = real.read_text(encoding="utf-8", errors="replace")
    try:
        result = run_reconciliation(text)
    except Exception as exc:  # noqa: BLE001 - surface a clean, path-free error
        logger.exception("Real statement failed to parse")
        return {
            "data_source": "real",
            "risk_usable": False,
            "error": {"code": "parse_failed", "message": f"Real statement could not be parsed: {type(exc).__name__}"},
            "blocking_issues": [{"code": "parse_failed", "message": "Real statement could not be parsed.", "context": ""}],
            "warnings": [],
            "snapshot": None,
            "portfolio_risk": None,
            "rules": None,
        }

    payload = result.to_payload()
    blocking = _blocking_issues(result)
    payload.update(
        data_source="real",
        risk_usable=len(blocking) == 0,
        blocking_issues=blocking,
        error=None,
    )
    return payload


def register_options_studio_routes(app: FastAPI) -> None:
    """Register the read-only Options Studio routes on ``app``."""

    @app.get("/options-studio/review")
    def options_studio_review() -> dict:  # noqa: ANN202 - FastAPI handler
        return build_review_payload()
