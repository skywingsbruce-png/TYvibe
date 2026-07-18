"""Personal Options Risk Studio — a fork-local extension for individual US
options investors (Phase 1: read-only, deterministic backend).

This package is intentionally isolated from Vibe-Trading's core research /
backtest / swarm workflow. It adds a *read-only* personal options-risk backend
with hard, deterministic guarantees:

* Broker statements are imported read-only; raw files never leave
  ``data/private/`` and are never sent to any LLM or remote service.
* Every risk number (payoff, max loss, breakevens, scenarios, concentration) is
  computed by deterministic Python. Expiry-payoff metrics use only real strikes
  and real premiums (cost basis); Greeks and scenarios use only real data from a
  :class:`~src.options_studio.providers.MarketDataProvider` and are reported
  *unavailable* when that data is missing — never simulated.
* A YAML-driven hard-rules engine holds veto power (ALLOW / WATCH / BLOCK) and
  always outranks the LLM, memory, persona, or community sentiment.
* Real trading is structurally impossible from this package: see
  :mod:`src.options_studio.guard`. There is no broker order path here.

Phase 1 modules that exist today:
``models``, ``guard``, ``storage``, ``classification``, ``providers``,
``risk_engine``, ``rules_engine``, and ``broker`` (``base`` / ``ibkr`` /
``occ``). Frontend, LLM routing, Discord import, and network data sources are
deliberately out of scope for Phase 1.
"""

from __future__ import annotations

__all__ = [
    "models",
    "guard",
    "storage",
    "classification",
    "providers",
    "risk_engine",
    "rules_engine",
    "broker",
]
