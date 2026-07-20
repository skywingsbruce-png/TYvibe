"""Local statement reconciliation pipeline (Phase 1.5).

Pure orchestration: given raw IBKR statement text, run the full read-only chain
- parse → classify → static payoff risk → YAML rules - and produce two local
artifacts:

* a JSON portfolio snapshot (already de-identified), and
* a human-readable Markdown reconciliation report.

Nothing here touches the network, an LLM, or a broker. Greeks / IV / prices are
computed only when a real :class:`MarketDataProvider` is supplied; the default
:class:`NullMarketDataProvider` yields ``unavailable`` and the report says so
explicitly rather than inventing values.

Redaction is structural: the report and snapshot are assembled from a
whitelist of market/position fields only. Account number, name, and address are
already dropped at the parser boundary; :func:`assert_report_clean` re-checks
the rendered text as a defensive backstop.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from src.options_studio.broker import ParseResult, ParseWarning, parse_statement
from src.options_studio.classification import classify
from src.options_studio.models import PortfolioSnapshot, StrategyType
from src.options_studio.providers import MarketDataProvider, NullMarketDataProvider
from src.options_studio.risk_engine import PortfolioRisk, analyze_portfolio
from src.options_studio.rules_engine import RuleMode, RulesReport, Severity, evaluate, load_rules

#: Fields the Studio cannot verify from a statement alone. Surfaced verbatim in
#: the report so the user never mistakes "not shown" for "checked and fine".
UNVERIFIABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("Buying power / margin", "Not exported in the position CSV; shown as unavailable, never estimated."),
    ("Maintenance margin per position", "Requires broker margin model; not reconstructed."),
    ("Multi-currency balances", "Only the base-currency cash line is read; other currencies are ignored."),
    ("Exercise / assignment reconstruction", "Assignment/exercise fills are listed in the blotter but positions are not re-derived from them."),
    ("Live Greeks / IV / prices", "Available only with a real market-data provider; the default run reports them unavailable."),
    ("Dividends / interest / corporate actions", "Not parsed in Phase 1.5."),
)


@dataclass(frozen=True)
class ReconciliationResult:
    """The full outcome of a reconciliation run (in-memory; no file IO)."""

    snapshot: PortfolioSnapshot
    portfolio_risk: PortfolioRisk
    rules_report: RulesReport
    warnings: tuple[ParseWarning, ...]
    report_markdown: str

    def to_payload(self) -> dict:
        """Return the de-identified review payload (JSON-safe dict).

        This is the single shape consumed by the JSON file, the API route, and
        the frontend dashboard. It contains no account number, name, or address.
        """
        return {
            "snapshot": self.snapshot.to_dict(),
            "portfolio_risk": self.portfolio_risk.to_dict(),
            "rules": self.rules_report.to_dict(),
            "warnings": [w.to_dict() for w in self.warnings],
        }

    def snapshot_json(self) -> str:
        """Return the de-identified snapshot as pretty JSON."""
        return json.dumps(self.to_payload(), ensure_ascii=False, indent=2)


def run_reconciliation(
    text: str,
    *,
    account_label: str = "acct-1",
    provider: Optional[MarketDataProvider] = None,
    rules: Optional[dict] = None,
) -> ReconciliationResult:
    """Run the read-only reconciliation chain on raw statement text."""
    provider = provider or NullMarketDataProvider()
    rules = rules if rules is not None else load_rules()

    parsed: ParseResult = parse_statement(text, account_label=account_label)
    classified = classify(parsed.snapshot)
    portfolio_risk = analyze_portfolio(classified, provider)

    warnings = tuple(parsed.warnings) + _detect_duplicates(classified)
    unresolved_positions = sum(
        1 for w in warnings if w.code in {"unresolved_option", "unsupported_asset_category"}
    )
    duplicate_warnings = sum(1 for w in warnings if w.code in {"duplicate_position", "duplicate_trade"})

    # REVIEW mode: a held position that is merely large is a WATCH exposure
    # alert, not a BLOCK. Held BLOCKs are structural only (real-trading toggle,
    # unbounded loss, or a materially incomplete portfolio from unparsed lines).
    rules_report = evaluate(
        portfolio_risk,
        rules,
        mode=RuleMode.REVIEW,
        unresolved_positions=unresolved_positions,
        duplicate_warnings=duplicate_warnings,
    )

    report = _render_report(classified, portfolio_risk, rules_report, warnings)
    return ReconciliationResult(
        snapshot=classified,
        portfolio_risk=portfolio_risk,
        rules_report=rules_report,
        warnings=warnings,
        report_markdown=report,
    )


# ---------------------------------------------------------------------------
# Duplicate detection (reconciliation-level warnings)
# ---------------------------------------------------------------------------


def _detect_duplicates(snapshot: PortfolioSnapshot) -> tuple[ParseWarning, ...]:
    """Flag exact-duplicate option lines and trade fills as warnings."""
    warnings: list[ParseWarning] = []

    option_keys = Counter(
        (o.underlying, o.right.value, o.strike, o.expiry.isoformat(), o.quantity) for o in snapshot.options
    )
    for (underlying, right, strike, expiry, _qty), count in option_keys.items():
        if count > 1:
            warnings.append(
                ParseWarning(
                    code="duplicate_position",
                    message=f"{count} identical option lines for the same contract; verify they are distinct lots.",
                    context=f"{underlying} {right} {strike} {expiry}",
                )
            )

    trade_keys = Counter(
        (t.symbol, t.asset_kind, t.trade_date.isoformat(), t.quantity, t.price) for t in snapshot.trade_lots
    )
    for (symbol, _kind, tdate, _qty, _price), count in trade_keys.items():
        if count > 1:
            warnings.append(
                ParseWarning(
                    code="duplicate_trade",
                    message=f"{count} identical trade fills; verify the export was not concatenated twice.",
                    context=f"{symbol} {tdate}",
                )
            )
    return tuple(warnings)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "unbounded/undefined"
    return f"${value:,.2f}"


def _render_report(
    snapshot: PortfolioSnapshot,
    portfolio: PortfolioRisk,
    rules: RulesReport,
    warnings: tuple[ParseWarning, ...],
) -> str:
    lines: list[str] = []
    lines.append("# Options Risk Studio - Reconciliation Report")
    lines.append("")
    lines.append("> Local, read-only. Not sent to any LLM or network. No trading capability.")
    lines.append("")
    lines.append(f"- Source format: `{snapshot.source_label}`")
    lines.append(f"- Account label (de-identified): `{snapshot.account_label}`")
    lines.append(f"- As of: {snapshot.as_of.isoformat()}")
    lines.append(f"- Base currency: {snapshot.base_currency}")
    lines.append("")

    # 1) Parse summary
    lines.append("## 1. Parse summary")
    lines.append("")
    lines.append(f"- Stock positions: **{len(snapshot.underlyings)}**")
    lines.append(f"- Option positions: **{len(snapshot.options)}**")
    lines.append(f"- Cash (base currency): **{_fmt_money(snapshot.cash)}**")
    lines.append(f"- Trade fills: **{len(snapshot.trade_lots)}**")
    lines.append(f"- Parser/reconciliation warnings: **{len(warnings)}**")
    lines.append("")

    # 2) Underlyings & expiries
    lines.append("## 2. Underlyings & expiries")
    lines.append("")
    underlyings = sorted({o.underlying for o in snapshot.options} | {u.symbol for u in snapshot.underlyings})
    lines.append(f"- Underlyings: {', '.join(underlyings) if underlyings else '(none)'}")
    expiries = sorted({o.expiry for o in snapshot.options})
    if expiries:
        parts = []
        for exp in expiries:
            dte = (exp - snapshot.as_of).days
            parts.append(f"{exp.isoformat()} (DTE {dte})")
        lines.append(f"- Option expiries: {', '.join(parts)}")
    else:
        lines.append("- Option expiries: (none)")
    lines.append("")

    # 3) Strategies
    lines.append("## 3. Recognized strategies")
    lines.append("")
    risk_by_id = {r.strategy_id: r for r in portfolio.strategies}
    unclassified: list[str] = []
    if not snapshot.strategies:
        lines.append("_No strategies classified._")
    else:
        lines.append("| ID | Underlying | Strategy | DTE | Max loss | Max profit | Breakevens |")
        lines.append("|----|-----------|----------|-----|----------|-----------|-----------|")
        for strat in snapshot.strategies:
            if strat.strategy_type == StrategyType.UNCLASSIFIED:
                unclassified.append(strat.strategy_id)
            r = risk_by_id.get(strat.strategy_id)
            payoff = r.payoff if r else None
            dte = r.dte if r and r.dte is not None else "-"
            if payoff is None or not payoff.available:
                max_loss = max_profit = "unavailable"
                bes = "unavailable"
            else:
                max_loss = "unbounded" if payoff.unbounded_loss else _fmt_money(payoff.max_loss)
                max_profit = "unbounded" if payoff.unbounded_profit else _fmt_money(payoff.max_profit)
                bes = ", ".join(f"{b:,.2f}" for b in payoff.breakevens) if payoff.breakevens else "-"
            lines.append(
                f"| {strat.strategy_id} | {strat.underlying} | {strat.strategy_type.value} | {dte} | "
                f"{max_loss} | {max_profit} | {bes} |"
            )
    lines.append("")
    lines.append(f"- Unclassified strategies: **{len(unclassified)}**"
                 + (f" ({', '.join(unclassified)})" if unclassified else ""))
    lines.append("")

    # 4) Warnings
    lines.append("## 4. Warnings")
    lines.append("")
    if not warnings:
        lines.append("_No warnings._")
    else:
        counts = Counter(w.code for w in warnings)
        lines.append("Counts: " + ", ".join(f"`{code}`x{n}" for code, n in sorted(counts.items())))
        lines.append("")
        for w in warnings:
            ctx = f" - `{w.context}`" if w.context else ""
            lines.append(f"- **{w.code}**: {w.message}{ctx}")
    lines.append("")

    # 5) Risk rules
    lines.append("## 5. Risk rules")
    lines.append("")
    lines.append("_Mode: **review** (held positions). A held position that merely exceeds your "
                 "per-trade loss preference is a WATCH exposure alert, not a BLOCK. Held BLOCKs are "
                 "structural only: real-trading toggle, unbounded loss, or a materially incomplete "
                 "portfolio from unparsed positions._")
    lines.append("")
    lines.append(f"**Overall: {rules.overall.label}**")
    lines.append("")
    lines.append("| Rule | Verdict | Reason |")
    lines.append("|------|---------|--------|")
    for v in rules.verdicts:
        reason = v.message.replace("|", "\\|")
        lines.append(f"| {v.rule_id} | {v.severity.label} | {reason} |")
    lines.append("")
    lines.append(_rules_note(rules.overall))
    lines.append("")

    # 6) Portfolio aggregates
    lines.append("## 6. Portfolio aggregates")
    lines.append("")
    lines.append(f"- Total defined max loss: **{_fmt_money(portfolio.total_defined_max_loss)}**")
    lines.append(f"- Strategies with unbounded loss: **{portfolio.unbounded_loss_strategies}**")
    lines.append(f"- Strategies with indeterminate risk (missing cost basis): **{portfolio.indeterminate_risk_strategies}**")
    lines.append(f"- Near-{_near_dte()}-DTE risk: **{_fmt_money(portfolio.near_dte_risk)}**")
    if portfolio.concentration_by_underlying:
        conc = ", ".join(f"{u} {f:.0%}" for u, f in portfolio.concentration_by_underlying)
        lines.append(f"- Concentration by underlying (of defined risk): {conc}")
    lines.append("")

    # 7) Cannot verify
    lines.append("## 7. Cannot be verified from this statement")
    lines.append("")
    for name, why in UNVERIFIABLE_FIELDS:
        lines.append(f"- **{name}** - {why}")
    lines.append("")

    return "\n".join(lines)


def _near_dte() -> int:
    from src.options_studio.risk_engine import NEAR_DTE_DAYS

    return NEAR_DTE_DAYS


def _rules_note(overall: Severity) -> str:
    if overall == Severity.BLOCK:
        return ("_BLOCK is final: the rules engine outranks any later LLM or human-persona commentary. "
                "Resolve the blocking rule(s) before acting._")
    if overall == Severity.WATCH:
        return "_WATCH items need review but do not block. The rules engine outranks any LLM commentary._"
    return "_All rules pass. The rules engine still outranks any later LLM commentary._"


# ---------------------------------------------------------------------------
# Defensive redaction backstop
# ---------------------------------------------------------------------------


def assert_report_clean(report_text: str, forbidden: tuple[str, ...]) -> None:
    """Raise if any forbidden token (e.g. real account id/name) leaked in.

    Structural redaction (whitelist rendering) is the primary guarantee; this
    is a defensive check callers can run with the tokens they know from the raw
    file, so a regression is caught loudly rather than shipped.
    """
    lowered = report_text.lower()
    hits = [tok for tok in forbidden if tok and tok.lower() in lowered]
    if hits:
        raise ValueError(f"Reconciliation report leaked forbidden token(s): {hits}")
