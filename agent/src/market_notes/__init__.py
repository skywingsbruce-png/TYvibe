"""Market Notes / 观点审计 — a fork-local, read-only opinion-audit workspace.

First version scope: **import opinions, structure them, produce traceable
conclusions, and link them to existing held positions.** Nothing here selects
stocks, fetches live quotes, scores outcomes automatically, trades, or connects
to a broker.

Guarantees:

* Raw notes live only under ``data/private/market-notes/`` (git-ignored); they
  are never uploaded or sent to a broker. They are sent to an LLM **only** when
  the optional interpretation layer is explicitly enabled (default OFF).
* Every structured field that is not explicitly present in the source is
  reported as ``unknown`` / ``unavailable`` — never guessed or back-filled.
* Conclusions are traceable: each cites the specific source note(s) it draws
  from. Contradictory opinions surface as a **conflict**, never a forced single
  direction.
* ``outcome_1d`` / ``outcome_1w`` / ``outcome_1m`` are reserved and always
  ``unavailable`` in v1 (no historical-quote backfill, no simulated data).

There is no trading path in this package.
"""

from __future__ import annotations

__all__ = ["models", "extractor", "conclusions", "storage", "importers"]
