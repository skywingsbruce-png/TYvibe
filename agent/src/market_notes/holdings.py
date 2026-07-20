"""Link note symbols to the existing read-only held book (Options Studio).

Reuses the Options Studio reconciliation (real ``data/private/ibkr_statement.csv``
when present, otherwise the bundled fictional sample). This module only
*associates* information — it never fabricates live Delta / IV / price, and it
never suggests a trade.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import src.options_studio as _os_pkg
from src.options_studio import storage as _os_storage
from src.options_studio.reconciliation import run_reconciliation

_SAMPLE_STATEMENT = Path(_os_pkg.__file__).resolve().parent / "samples" / "ibkr_sample_statement.csv"
_REAL_NAME = "ibkr_statement.csv"
_NEAR_DTE_DAYS = 14


def _held_text() -> tuple[Optional[str], str]:
    real = _os_storage.get_private_dir() / _REAL_NAME
    if real.exists():
        return real.read_text(encoding="utf-8", errors="replace"), "real"
    if _SAMPLE_STATEMENT.exists():
        return _SAMPLE_STATEMENT.read_text(encoding="utf-8"), "sample"
    return None, "none"


def build_holdings_index() -> dict:
    """Return per-underlying strategy info from the held book (or an empty index).

    Shape::

        {
          "data_source": "real"|"sample"|"none"|"unavailable",
          "as_of": "YYYY-MM-DD" | None,
          "by_symbol": { "SNDK": {count, strategies:[{id,type,dte,earliest_expiry,legs:[...]}],
                                  earliest_expiry, has_near_dte} }
        }
    """
    text, data_source = _held_text()
    if text is None:
        return {"data_source": "none", "as_of": None, "by_symbol": {}}

    try:
        result = run_reconciliation(text)
    except Exception:  # noqa: BLE001 - degrade gracefully; never crash the notes page
        return {"data_source": "unavailable", "as_of": None, "by_symbol": {}}

    snapshot = result.snapshot
    risk_by_id = {r.strategy_id: r for r in result.portfolio_risk.strategies}
    by_symbol: dict[str, dict] = {}

    for strat in snapshot.strategies:
        risk = risk_by_id.get(strat.strategy_id)
        dte = risk.dte if risk is not None else None
        legs = [
            {
                "right": leg.contract.right.value,
                "strike": leg.contract.strike,
                "expiry": leg.contract.expiry.isoformat(),
                "quantity": leg.quantity,
            }
            for leg in strat.option_legs
        ]
        expiries = [leg.contract.expiry for leg in strat.option_legs]
        earliest = min(expiries).isoformat() if expiries else None
        entry = by_symbol.setdefault(strat.underlying, {"count": 0, "strategies": [], "expiries": []})
        entry["count"] += 1
        entry["strategies"].append(
            {
                "strategy_id": strat.strategy_id,
                "type": strat.strategy_type.value,
                "dte": dte,
                "earliest_expiry": earliest,
                "legs": legs,
            }
        )
        entry["expiries"].extend(expiries)

    # Finalize per-symbol aggregates.
    for entry in by_symbol.values():
        expiries: list[date] = entry.pop("expiries")
        entry["earliest_expiry"] = min(expiries).isoformat() if expiries else None
        entry["has_near_dte"] = any(
            r.dte is not None and 0 <= r.dte <= _NEAR_DTE_DAYS
            for r in result.portfolio_risk.strategies
            if r.underlying in by_symbol and r.strategy_id in {s["strategy_id"] for s in entry["strategies"]}
        )

    return {"data_source": data_source, "as_of": snapshot.as_of.isoformat(), "by_symbol": by_symbol}


def link_symbols(symbols: set[str], index: dict) -> dict:
    """Return the subset of the holdings index that matches the given symbols."""
    by_symbol = index.get("by_symbol", {})
    return {sym: by_symbol[sym] for sym in symbols if sym in by_symbol}
