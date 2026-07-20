# Market Notes / 观点审计 (v1)

A fork-local, **read-only** opinion-audit workspace. It imports trading-chat
opinions, structures them, produces **traceable** conclusions, and links them to
your existing (read-only) Options Studio held book. It does not select stocks,
fetch live quotes, score outcomes, trade, or connect to a broker.

## Import method

Drop files into (git-ignored):

```
data/private/market-notes/
```

Supported sources (auto-detected by extension + content sniff):

| Format | File | Importer |
|---|---|---|
| DiscordChatExporter JSON | `*.json` (messages with `author` object + `content`) | `discord_json` |
| Telegram | `*.json` (Telegram export, `from`/`text`) or `*.txt` (`[time] Author: msg` lines) | `telegram` |
| Manual excerpts | `*.md` / `*.txt` (blocks split by `---`; optional `author:` / `time:` headers) | `manual` |

Open the page at **`/market-notes`** (sidebar: "Market Notes") and Refresh.

Raw files stay on your machine — never uploaded, never sent to a broker, and
sent to an LLM only if you explicitly enable the optional layer (below).

## What it produces (deterministic, no LLM)

Each message becomes a structured note: author, time, symbols, direction
(bullish / bearish / neutral / conditional / unknown), content, key levels,
horizon (intraday / days / weeks / unknown), setup condition, invalidation
condition, verbatim excerpt, source file + in-file reference, and a content
hash. **Anything the text does not state is `unknown` / `unavailable` — never
guessed.**

- **Conclusion card**: per-symbol leans and stated invalidations, each
  expandable to the exact cited excerpt. Contradictory opinions surface as
  **观点冲突 / conflict** — never a forced single direction.
- **Holdings linkage**: a symbol you actually hold shows the related strategy
  count, names, earliest expiry, and a ≤14-DTE flag, expandable to the legs
  (information link only — no live Delta / IV / price, no suggested action).
- `outcome_1d` / `outcome_1w` / `outcome_1m` are reserved and always
  `unavailable` in v1 (no historical-quote backfill).

The page has **no buy / sell / add / trim / order controls.**

## Optional LLM layer — default OFF

Import, structuring, conclusions, and holdings linkage all work fully offline.
To let an LLM write near-human interpretive conclusions, set in your local
`.env` (see `agent/.env.example`):

```
MARKET_NOTES_LLM_ENABLED=1
```

Rules enforced in code (`llm.py`): the API key comes only from `.env` (reuses
your existing provider; never in code / logs / tests / page / Git); the LLM may
only rephrase/cite your notes and must never compute price / yield / Greeks /
risk or emit a trade instruction; **every conclusion must cite real note ids** —
an answer with no valid citation, or invalid JSON, is rejected and shown at most
as "needs manual review", never as a formal conclusion.

## Tests

```
.venv/Scripts/python -m pytest agent/tests/test_market_notes.py -q
```

Fixtures under `agent/tests/fixtures/market_notes/` are entirely fictional.
