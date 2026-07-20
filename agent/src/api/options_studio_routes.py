"""Options Studio HTTP routes for the Web UI (Phase 2 - read-only).

Mounted by ``agent/api_server.py`` via ``register_options_studio_routes(app)``.

* ``GET  /options-studio/review``  - held-book reconciliation (de-identified).
* ``POST /options-studio/propose`` - authorize a CANDIDATE new trade against the
  hard rules. Read-only decision support: it evaluates a hypothetical trade, it
  never places, modifies, or routes an order.

Data-safety contract (held book, shared by both routes):

* The bundled fictional **sample** is used **only** when the user's real
  ``data/private/ibkr_statement.csv`` does not exist at all.
* When the real file exists, ``data_source="real"`` always — no fallback to
  sample. If it fails to parse or surfaces a risk-invalidating condition
  (``unresolved_option``, ``unsupported_asset_category``, ``duplicate_position``,
  ``duplicate_trade``, ``cash_not_found``, or any ``unclassified`` strategy) the
  review sets ``risk_usable=false`` with explicit ``blocking_issues``.

Everything is local - no network, no LLM, no broker.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI

from src.options_studio import storage
from src.options_studio.proposal import (
    ProposalError,
    evaluate_proposal,
    proposed_trade_from_mapping,
)
from src.options_studio.reconciliation import ReconciliationResult, run_reconciliation

logger = logging.getLogger(__name__)

_SAMPLE_STATEMENT = (
    Path(__file__).resolve().parents[1] / "options_studio" / "samples" / "ibkr_sample_statement.csv"
)
_REAL_STATEMENT_NAME = "ibkr_statement.csv"

_RISK_BLOCKING_WARNING_CODES = frozenset(
    {
        "unresolved_option",
        "unsupported_asset_category",
        "duplicate_position",
        "duplicate_trade",
        "cash_not_found",
    }
)
_UNRESOLVED_POSITION_CODES = frozenset({"unresolved_option", "unsupported_asset_category"})
_DUPLICATE_CODES = frozenset({"duplicate_position", "duplicate_trade"})


def _real_statement_path() -> Path:
    return storage.get_private_dir() / _REAL_STATEMENT_NAME


def _held_source() -> tuple[str, str]:
    """Return ``(text, data_source)`` for the held book, preferring the real file."""
    real = _real_statement_path()
    if real.exists():
        return real.read_text(encoding="utf-8", errors="replace"), "real"
    return _SAMPLE_STATEMENT.read_text(encoding="utf-8"), "sample"


def _blocking_issues(result: ReconciliationResult) -> list[dict[str, str]]:
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


def _held_counts(result: ReconciliationResult) -> tuple[int, int]:
    """Return (unresolved_positions, duplicate_warnings) for the held book."""
    unresolved = sum(1 for w in result.warnings if w.code in _UNRESOLVED_POSITION_CODES)
    duplicates = sum(1 for w in result.warnings if w.code in _DUPLICATE_CODES)
    return unresolved, duplicates


def build_review_payload() -> dict:
    """Build the held-book review payload with the hardened data-safety semantics."""
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


def build_propose_payload(body: Any) -> dict:
    """Evaluate a candidate trade against the held book (read-only)."""
    # Validate the candidate first so a bad body returns a clean 4xx-style error.
    try:
        trade = proposed_trade_from_mapping(body)
    except ProposalError as exc:
        return {"error": {"code": "invalid_trade", "message": str(exc)}}

    text, data_source = _held_source()
    try:
        held_result = run_reconciliation(text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Held statement failed to parse during propose")
        return {
            "error": {"code": "held_parse_failed", "message": f"Held statement could not be parsed: {type(exc).__name__}"},
            "data_source": data_source,
        }

    unresolved, duplicates = _held_counts(held_result)
    card = evaluate_proposal(
        held_result.snapshot,
        trade,
        held_unresolved_positions=unresolved,
        held_duplicate_warnings=duplicates,
    )
    payload = card.to_payload()
    payload["data_source"] = data_source
    payload["held_risk_usable"] = len(_blocking_issues(held_result)) == 0
    payload["error"] = None
    return payload


def register_options_studio_routes(app: FastAPI) -> None:
    """Register the read-only Options Studio routes on ``app``."""

    @app.get("/options-studio/review")
    def options_studio_review() -> dict:  # noqa: ANN202 - FastAPI handler
        return build_review_payload()

    @app.post("/options-studio/propose")
    def options_studio_propose(body: dict = Body(...)) -> dict:  # noqa: ANN202, B008 - FastAPI handler
        return build_propose_payload(body)
