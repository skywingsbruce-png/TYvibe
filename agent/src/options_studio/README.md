# Personal Options Risk Studio — Phase 1

A **fork-local, read-only, deterministic** options-risk backend for an
individual US options investor. It lives alongside Vibe-Trading's core workflow
but does not modify it.

> Phase 1 is backend only. No frontend, no LLM routing, no Discord import, no
> network market-data source, and no trading connector are included here.

## What Phase 1 does

1. **Imports IBKR statements (read-only)** via an extensible
   `BrokerStatementParser` interface. Two IBKR shapes are supported at a
   minimal-but-usable level:
   - **Activity Statement CSV** (multi-section, `Header`/`Data` tagged)
   - **Flex Query CSV** (flat, single-header)
2. **Builds a standard portfolio model** (`models.py`) and **classifies
   strategies** (`classification.py`): single-leg long/short call/put, long/short
   stock, vertical debit/credit spreads, covered calls, cash-secured puts, PMCC,
   and LEAPS. Anything not confidently identifiable is left `unclassified` — it
   is never given a strategy name.
3. **Computes risk deterministically** (`risk_engine.py`):
   - Expiry payoff — **max profit, max loss, breakevens, DTE, multiplier** — for
     vertical spreads (and other defined-risk structures) using only real
     strikes and real premiums (cost basis). **No live data required.**
   - Correctly reports **unbounded** max profit/loss for undefined-risk
     structures (e.g. a naked short call) instead of a fake number.
   - **Greeks** (via Black-Scholes) and **-20%…+20% expiry scenarios** only from
     a real spot / real IV supplied by a `MarketDataProvider`. With the default
     `NullMarketDataProvider` these are reported **unavailable** — never
     simulated.
   - Portfolio aggregation: total defined max loss, concentration by underlying,
     near-14-DTE risk, unbounded-loss count, unclassified count.
4. **Enforces hard rules** (`rules_engine.py` + `user_config/risk_rules.yaml`):
   `ALLOW / WATCH / BLOCK` with per-rule reasons. Rules: real-trading-forbidden
   (fail-closed, always calls `guard.py`), 0-DTE limit, near-14-DTE warning,
   per-trade max risk, single-underlying concentration, theme concentration.
   Thresholds live in YAML, not in code.

## Privacy boundary

- Raw broker files may only be written under `data/private/` (git-ignored at the
  repo root **and** hardened with a local `.gitignore` of `*` by `storage.py`).
- The real broker **account number, name, and account total are never surfaced**
  on the parsed snapshot: the parser stamps only an opaque `account_label`
  (see `test_options_studio_ibkr_parser.py::test_activity_statement_does_not_leak_account_number`).
- Phase 1 sends **nothing to any LLM or remote service** — there is no such code
  path in this package.

## Supported vs. unsupported IBKR CSV fields

**Used (Activity Statement):**
- `Statement` → `Period` / `WhenGenerated` (snapshot `as_of`)
- `Financial Instrument Information` → `Symbol`, `Underlying`, `Multiplier`,
  `Expiry`, `Type`/`Put/Call`, `Strike`
- `Open Positions` → `DataDiscriminator`, `Asset Category`, `Currency`, `Symbol`,
  `Quantity`, `Mult`, `Cost Price`, `Value`
- `Trades` → `Asset Category`, `Symbol`, `Date/Time`, `Quantity`, `T. Price`,
  `Comm/Fee`
- `Cash Report` → `Ending Cash` (base currency)

**Used (Flex Query):** `CurrencyPrimary`/`Currency`, `AssetClass`, `Symbol`,
`UnderlyingSymbol`, `PutCall`, `Strike`, `Expiry`, `Multiplier`, `Position`,
`CostBasisPrice`, `PositionValue`, `ReportDate`, and for trade rows
`Buy/Sell`, `TradePrice`, `TradeDate`, `IBCommission`.

**Not supported in Phase 1 (ignored or surfaced as a structured warning):**
- Corporate actions, dividends, interest, and fees sections (beyond per-trade
  `Comm/Fee`)
- Option exercise / assignment reconciliation (the fills are kept in the
  blotter, but positions are not re-derived from them)
- Bonds, futures (FUT), FX/CFD, funds, and other non-equity/non-equity-option
  asset categories → emitted as `unsupported_asset_category` warnings
- Multi-currency accounts (only the base currency's cash is read)
- Buying power / margin figures (reported as `None`)
- Any option line whose strike/expiry/right cannot be resolved from the
  instrument table, OCC symbol, or description → `unresolved_option` warning and
  the line is **skipped, never guessed**

## Known limitations / not yet implemented

- No live/network market-data provider ships here by design, so Greeks and
  scenarios are unavailable unless a provider is injected. This keeps the module
  free of any hidden fabrication surface.
- Covered-call max loss requires the shares' cost basis; when the statement
  omits it, the payoff is reported unavailable rather than guessed.
- Event-window rules (earnings/CPI/NFP/FOMC) and leveraged-ETF-overlap rules are
  deferred to a later phase.
- Deferred to later phases entirely: redaction layer for an LLM view, opinion
  (Discord) import, frontend pages, and LLM provider routing.

## Phase 1.5 — local reconciliation CLI

Put your IBKR CSV under `data/private/` (git-ignored), then run:

```
vibe-trading options-studio reconcile --file data/private/my_statement.csv
```

- `--file` may be omitted if there is exactly one CSV under `data/private/`.
- Optional: `--account-label acct-1`, `--out-dir <dir>`, `--no-write` (print
  summary only, write nothing).

It runs the read-only chain — parse → classify → static payoff risk → YAML
rules — and writes two files to the git-ignored `data/private/outputs/`:

- `portfolio_snapshot.json` — de-identified snapshot + risk + rules + warnings
- `reconciliation_report.md` — human-readable report with parse counts,
  underlyings/expiries, recognized strategies (with max loss / max profit /
  breakevens), warnings (`unresolved_option` / `unsupported_asset_category` /
  `duplicate_*`), the `ALLOW/WATCH/BLOCK` rule verdicts, and an explicit
  "Cannot be verified" section (buying power, margin, multi-currency,
  exercise/assignment, live Greeks/IV/prices).

The command is local and read-only: it never contacts the network, an LLM, or a
broker, and the report contains no account number, name, address, or raw trade
description.

## Phase 1.6 — held-book review vs. proposed-trade authorization

Two clearly separated surfaces (both local, read-only, never an order):

### `review` (held positions)

```
vibe-trading options-studio review --file data/private/ibkr_statement.csv
```

(`reconcile` is a backward-compatible alias.) Reviews what you **already hold**.
Semantics that matter here:

- A held position that merely exceeds your personal `max_single_trade_loss`
  preference is a **WATCH exposure alert, not a BLOCK** — you cannot be blocked
  out of a position you already own.
- Held **BLOCK** is reserved for structural problems only: the real-trading
  toggle, an **unbounded-loss** position, or a **materially incomplete
  portfolio** (an `unresolved_option` / `unsupported_asset_category` line means
  real exposure is missing, so the aggregate can't be trusted — fix the parser
  first).

### `propose` (candidate new trade)

```
vibe-trading options-studio propose \
  --file data/private/ibkr_statement.csv \
  --trade data/private/my_candidate.yaml
```

Authorizes a **candidate new trade** you have not taken yet, writing
`proposal_card.md` + `proposal_card.json` to `data/private/outputs/`. Here:

- The `max_single_trade_loss` cap **is** a hard **BLOCK** — the whole point is to
  stop a bad new trade before it is placed.
- The candidate is kept strictly separate from the held book (distinct JSON
  sections, position counts never merged), and the card shows the incremental
  before → after portfolio impact.

Proposed-trade file (`--trade`), YAML or JSON — each premium carries a
`price_basis`:

```yaml
label: MSFT 400/420 bull call debit spread
legs:
  - {underlying: MSFT, right: call, strike: 400, expiry: 2026-02-20, quantity: 1,  premium: 15.00, price_basis: verified_quote}
  - {underlying: MSFT, right: call, strike: 420, expiry: 2026-02-20, quantity: -1, premium: 6.00,  price_basis: verified_quote}
```

`price_basis` provenance gates approval:

- `verified_quote` — from a traceable real quote; eligible for ALLOW.
- `user_estimate` — hand-entered/assumed; usable for a hypothetical payoff, but
  the candidate is **WATCH at best, never ALLOW**, and the report flags "assumed
  price". (A premium with no explicit `price_basis` defaults to `user_estimate`.)
- `unknown` — no premium; **nothing is fabricated** (payoff / IV / Greeks stay
  unavailable) and the candidate cannot be ALLOW.

## Phase 2 v0 — web dashboard (read-only)

A first read-only dashboard is wired into the Vibe Web UI at **`/options-studio`**
(sidebar: "Options Studio"). It shows the held-book review: parse summary,
recognized strategies (max loss / max profit / breakevens / DTE), concentration,
near-DTE risk, the `ALLOW/WATCH/BLOCK` rule verdicts, and warnings.

- Backend route: `GET /options-studio/review` (in `src/api/options_studio_routes.py`)
  returns the de-identified reconciliation payload — your real
  `data/private/ibkr_statement.csv` if present, otherwise a bundled fictional
  sample so the page renders before any real export exists (`data_source` field
  flags which).
- The page falls back to the bundled `frontend/public/options-studio-sample.json`
  when the backend is not running, so it renders in a pure-frontend dev session
  too.
- Greeks / IV / prices display as **unavailable** (never simulated); the
  `propose` authorization card is not yet in the UI (CLI only) — that is the next
  Phase 2 step.

## Running the tests

From the repo root, with the project venv:

```
.venv/Scripts/python -m pytest \
  agent/tests/test_options_studio_occ.py \
  agent/tests/test_options_studio_ibkr_parser.py \
  agent/tests/test_options_studio_classification.py \
  agent/tests/test_options_studio_risk_engine.py \
  agent/tests/test_options_studio_guard.py \
  agent/tests/test_options_studio_rules.py \
  agent/tests/test_options_studio_cli.py \
  agent/tests/test_options_studio_review_propose.py -q

.venv/Scripts/python -m ruff check agent/src/options_studio agent/tests/test_options_studio_*.py
```

All fixtures under `agent/tests/fixtures/options_studio/` are **entirely
fictional**. Never commit a real broker statement.

## Guarantees

- **No simulated data anywhere.** Every price / IV / Greek / scenario is either
  real (from a provider or the statement) or reported unavailable.
- **No real-trading path anywhere.** `guard.py` fails closed; any
  `ENABLE_REAL_TRADING`-style toggle raises, and the rules engine returns
  `BLOCK`.
