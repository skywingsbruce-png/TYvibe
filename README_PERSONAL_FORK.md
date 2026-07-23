# Personal fork — local read-only research workbench

This repository is a **personal fork** of [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading),
extended with three local, read-only modules I use for my own research:

| Module | What it does |
|---|---|
| **Options Studio** | Imports a broker statement *from local disk*, classifies option strategies, and computes deterministic risk (payoff, DTE, concentration) plus hard `ALLOW / WATCH / BLOCK` rules. |
| **Market Notes** | Turns imported opinions/notes into structured, citation-backed conclusion cards. |
| **Market Radar** | Ranks a configured watchlist into sector/theme candidates. |

## What this is — and is not

* This is a **personal research and simulation aid**. It is **not** investment
  advice, not a recommendation, and not a professional risk system.
* The three workbench modules above are **read-only**, with **no automatic order
  placement**: they connect to no broker for order entry and never route or
  modify an order, and a fail-closed guard (`options_studio/guard.py`) refuses to
  run if a real-trading toggle is ever set. Other capabilities that may exist in
  the upstream general-purpose platform are outside this workbench's scope and
  should not be configured for real trading.
* The "propose a trade" screen is a **pre-trade rule check on a hypothetical
  trade**. It evaluates and prints a decision card. It never places, routes, or
  modifies an order.
* Numbers that require live market data (Greeks, IV, spot, scenario P/L) are
  reported **unavailable** rather than simulated. Time spreads (PMCC / calendar /
  diagonal) report an **approximate net-debit risk proxy**, explicitly not a
  precise maximum loss.
* Nothing is sent to an LLM or over the network by default. The optional LLM
  layer in Market Notes is **off** unless you explicitly opt in.

## Your real data stays out of this repository

**Put every real file under `data/private/`.** That path is git-ignored
(`.gitignore`), and so are `.env`, build output, and generated reports.

```
data/private/
  ibkr_statement.csv     <- your real broker export (ignored by git)
  outputs/               <- generated reports/snapshots (ignored by git)
```

Do **not** commit any of the following, in code, tests, fixtures, docs, or
comments:

* Real broker statements (CSV/PDF), account numbers, account holder names or addresses
* Real positions, real cash balances, or any real portfolio figure — even as a
  code comment or a test's expected value
* Real Telegram / Discord / chat exports or message text
* API keys, tokens, or any credential (use `.env`, which is git-ignored)

Every sample and fixture committed here is **fictional by construction** and
labelled as such (e.g. account `U0000000-FICTIONAL`, "FICTIONAL SAMPLE"). If you
fork this, keep it that way.

## Running it locally

```bash
python -m cli options-studio review --file <path-to-your-statement.csv>
```

Outputs are written to the git-ignored `data/private/outputs/`. See
[`agent/src/options_studio/README.md`](agent/src/options_studio/README.md) and
[`agent/src/market_notes/README.md`](agent/src/market_notes/README.md) for module
detail.

## Licence and attribution

Upstream code remains under the upstream project's [LICENSE](LICENSE) and
copyright. This fork adds the modules listed above; it is not affiliated with,
endorsed by, or supported by the upstream authors.
