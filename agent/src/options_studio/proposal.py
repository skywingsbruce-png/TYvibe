"""Proposed-trade authorization (Phase 1.6).

This is the *second* Options-Studio surface, kept strictly separate from the
held-book review (:mod:`src.options_studio.reconciliation`). It answers a
different question: *"If I were to take this new trade, would my hard rules
allow it?"* - a read-only, pre-trade decision aid. It never places, modifies, or
routes an order, and it shares no code with the live-broker mandate flow in
``src.live``.

Separation guarantees:

* The candidate legs are built into their **own** snapshot and are never mixed
  into the held snapshot's position counts. The report and JSON keep ``held``
  and ``proposed`` in distinct sections.
* Rule severity differs by surface (see :class:`~src.options_studio.rules_engine.RuleMode`):
  the candidate is judged in ``PROPOSE`` mode (per-trade max-loss cap is a hard
  BLOCK), while the after-trade portfolio impact is judged in ``REVIEW`` mode
  (held positions are not blocked for being large).

Price provenance (:class:`~src.options_studio.rules_engine.PriceBasis`):

* A premium a user types in is treated as ``USER_ESTIMATE`` unless explicitly
  marked ``verified_quote`` - an estimate can drive a hypothetical payoff but the
  candidate can never be ALLOW (at most WATCH), and the report flags "assumed
  price".
* A leg with no premium is ``UNKNOWN``: nothing is fabricated (premium / IV /
  Greeks / quote stay unavailable) and the candidate cannot be ALLOW.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Optional

import yaml

from src.options_studio.broker.occ import parse_expiry
from src.options_studio.classification import classify
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
    UnderlyingPosition,
)
from src.options_studio.providers import MarketDataProvider, NullMarketDataProvider
from src.options_studio.risk_engine import PortfolioRisk, analyze_portfolio
from src.options_studio.rules_engine import (
    PriceBasis,
    RuleMode,
    RulesReport,
    Severity,
    evaluate,
    load_rules,
)


class ProposalError(ValueError):
    """Raised when a proposed-trade definition is invalid (path-free message)."""


@dataclass(frozen=True)
class ProposedOptionLeg:
    """One candidate option leg with its price provenance."""

    underlying: str
    right: OptionRight
    strike: float
    expiry: date
    quantity: float  # signed: + long, - short
    premium: Optional[float]
    price_basis: PriceBasis
    multiplier: int = 100


@dataclass(frozen=True)
class ProposedStockLeg:
    """A candidate stock leg (e.g. shares for a covered call)."""

    underlying: str
    quantity: float
    average_cost: Optional[float] = None


@dataclass(frozen=True)
class ProposedTrade:
    """A candidate (not-yet-held) strategy definition."""

    label: str
    option_legs: tuple[ProposedOptionLeg, ...] = field(default_factory=tuple)
    stock_legs: tuple[ProposedStockLeg, ...] = field(default_factory=tuple)

    def aggregate_price_basis(self) -> PriceBasis:
        """Worst-case price basis across option legs.

        A single UNKNOWN drags the whole candidate to UNKNOWN; otherwise a single
        USER_ESTIMATE drags it to USER_ESTIMATE; only all-verified stays
        VERIFIED_QUOTE. A leg with no premium is always UNKNOWN regardless of its
        declared basis (there is nothing to verify).
        """
        if not self.option_legs:
            return PriceBasis.UNKNOWN
        seen = set()
        for leg in self.option_legs:
            if leg.premium is None:
                seen.add(PriceBasis.UNKNOWN)
            else:
                seen.add(leg.price_basis)
        if PriceBasis.UNKNOWN in seen:
            return PriceBasis.UNKNOWN
        if PriceBasis.USER_ESTIMATE in seen:
            return PriceBasis.USER_ESTIMATE
        return PriceBasis.VERIFIED_QUOTE


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _parse_expiry_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    parsed = parse_expiry(str(value))
    if parsed is None:
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ProposalError(f"Unparseable expiry: {value!r}") from exc
    return parsed


def _parse_right(value: Any) -> OptionRight:
    token = str(value or "").strip().lower()
    if token in {"call", "c"}:
        return OptionRight.CALL
    if token in {"put", "p"}:
        return OptionRight.PUT
    raise ProposalError(f"right must be call/put, got {value!r}")


def _leg_price_basis(raw: dict[str, Any], premium: Optional[float]) -> PriceBasis:
    """Resolve a leg's price basis.

    Explicit ``price_basis`` wins. Otherwise a supplied premium defaults to
    USER_ESTIMATE (a typed number is an estimate until verified), and a missing
    premium is UNKNOWN.
    """
    if "price_basis" in raw and raw["price_basis"] is not None:
        return PriceBasis.parse(raw["price_basis"])
    return PriceBasis.USER_ESTIMATE if premium is not None else PriceBasis.UNKNOWN


def load_proposed_trade(text: str) -> ProposedTrade:
    """Parse a proposed-trade definition (YAML or JSON text) into a ProposedTrade."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProposalError(f"Invalid proposed-trade file: {exc}") from exc
    return proposed_trade_from_mapping(data)


def proposed_trade_from_mapping(data: Any) -> ProposedTrade:
    """Build a ProposedTrade from an already-decoded mapping (e.g. a JSON body)."""
    if not isinstance(data, dict):
        raise ProposalError("Proposed trade must be a mapping with a 'legs' list.")

    label = str(data.get("label") or "Proposed trade")
    raw_legs = data.get("legs") or data.get("option_legs") or []
    if not isinstance(raw_legs, list) or not raw_legs:
        raise ProposalError("Proposed-trade file must define a non-empty 'legs' list.")

    option_legs: list[ProposedOptionLeg] = []
    for raw in raw_legs:
        if not isinstance(raw, dict):
            raise ProposalError("Each leg must be a mapping.")
        premium = raw.get("premium")
        premium_val = float(premium) if premium is not None else None
        qty = raw.get("quantity")
        if qty is None:
            raise ProposalError("Each leg needs a signed 'quantity' (+ long, - short).")
        quantity = float(qty)
        action = str(raw.get("action") or "").strip().lower()
        if action == "sell" and quantity > 0:
            quantity = -quantity
        elif action == "buy" and quantity < 0:
            quantity = -quantity
        if quantity == 0:
            raise ProposalError("Leg quantity cannot be zero.")
        option_legs.append(
            ProposedOptionLeg(
                underlying=str(raw.get("underlying", "")).strip().upper(),
                right=_parse_right(raw.get("right")),
                strike=float(raw["strike"]),
                expiry=_parse_expiry_value(raw.get("expiry")),
                quantity=quantity,
                premium=premium_val,
                price_basis=_leg_price_basis(raw, premium_val),
                multiplier=int(raw.get("multiplier", 100)),
            )
        )

    stock_legs: list[ProposedStockLeg] = []
    for raw in data.get("stock_legs", []) or []:
        if not isinstance(raw, dict):
            raise ProposalError("Each stock leg must be a mapping.")
        cost = raw.get("average_cost")
        stock_legs.append(
            ProposedStockLeg(
                underlying=str(raw.get("underlying", "")).strip().upper(),
                quantity=float(raw.get("quantity", 0)),
                average_cost=float(cost) if cost is not None else None,
            )
        )

    return ProposedTrade(label=label, option_legs=tuple(option_legs), stock_legs=tuple(stock_legs))


# ---------------------------------------------------------------------------
# Snapshot construction (kept separate from held positions)
# ---------------------------------------------------------------------------


def _proposed_snapshot(trade: ProposedTrade, held: PortfolioSnapshot) -> PortfolioSnapshot:
    """Build a snapshot containing ONLY the candidate legs."""
    options = tuple(
        OptionContract(
            underlying=leg.underlying,
            right=leg.right,
            strike=leg.strike,
            expiry=leg.expiry,
            multiplier=leg.multiplier,
            quantity=leg.quantity,
            average_cost=leg.premium,
        )
        for leg in trade.option_legs
    )
    underlyings = tuple(
        UnderlyingPosition(symbol=s.underlying, quantity=s.quantity, average_cost=s.average_cost)
        for s in trade.stock_legs
    )
    return PortfolioSnapshot(
        account_label=held.account_label,
        as_of=held.as_of,
        base_currency=held.base_currency,
        cash=held.cash,
        buying_power=None,
        underlyings=underlyings,
        options=options,
        source_label="proposed_trade",
    )


def _proposed_net_cash(trade: ProposedTrade) -> Optional[float]:
    """Net cash flow of the candidate (+ credit, - debit). None if unknowable."""
    total = 0.0
    for leg in trade.option_legs:
        if leg.premium is None:
            return None
        total += -leg.quantity * leg.premium * leg.multiplier
    for leg in trade.stock_legs:
        if leg.average_cost is None:
            return None
        total += -leg.quantity * leg.average_cost
    return total


def _combined_snapshot(held: PortfolioSnapshot, proposed: PortfolioSnapshot, net_cash: Optional[float]) -> PortfolioSnapshot:
    """Held + candidate positions, with cash adjusted by the candidate's net cash."""
    after_cash = held.cash + (net_cash or 0.0)
    return replace(
        held,
        cash=after_cash,
        options=tuple(held.options) + tuple(proposed.options),
        underlyings=tuple(held.underlyings) + tuple(proposed.underlyings),
        strategies=(),  # re-classified fresh below
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalCard:
    """The full proposed-trade authorization outcome."""

    label: str
    price_basis: PriceBasis
    proposed_risk: PortfolioRisk
    before: PortfolioRisk
    after: PortfolioRisk
    rules_candidate: RulesReport
    rules_portfolio: RulesReport
    overall: Severity
    deltas: dict[str, Any]
    report_markdown: str

    def to_payload(self) -> dict:
        """Return the card as a JSON-safe dict (for the API / frontend)."""
        return {
            "mode": "propose",
            "label": self.label,
            "price_basis": self.price_basis.value,
            "overall": self.overall.label,
            "proposed": self.proposed_risk.to_dict(),
            "held_before": self.before.to_dict(),
            "portfolio_after": self.after.to_dict(),
            "rules_candidate": self.rules_candidate.to_dict(),
            "rules_portfolio": self.rules_portfolio.to_dict(),
            "deltas": self.deltas,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, indent=2)


def evaluate_proposal(
    held: PortfolioSnapshot,
    trade: ProposedTrade,
    *,
    provider: Optional[MarketDataProvider] = None,
    rules: Optional[dict] = None,
    held_unresolved_positions: int = 0,
    held_duplicate_warnings: int = 0,
) -> ProposalCard:
    """Evaluate a candidate trade against the rules, in isolation from the held book."""
    provider = provider or NullMarketDataProvider()
    rules = rules if rules is not None else load_rules()

    before = analyze_portfolio(classify(held), provider)

    proposed_snapshot = classify(_proposed_snapshot(trade, held))
    proposed_risk = analyze_portfolio(proposed_snapshot, provider)

    net_cash = _proposed_net_cash(trade)
    after = analyze_portfolio(classify(_combined_snapshot(held, proposed_snapshot, net_cash)), provider)

    price_basis = trade.aggregate_price_basis()

    # Candidate: PROPOSE mode - per-trade cap is a hard BLOCK; price basis gates ALLOW.
    # Concentration is judged on the portfolio (below), not on the candidate alone
    # (which would always be 100% of itself).
    rules_candidate = evaluate(
        proposed_risk, rules, mode=RuleMode.PROPOSE, price_basis=price_basis, include_concentration=False
    )
    # After-trade portfolio: REVIEW mode - held positions aren't blocked for size;
    # this catches NEW structural issues the trade introduces (unbounded loss,
    # theme/underlying concentration) and any pre-existing incompleteness.
    rules_portfolio = evaluate(
        after,
        rules,
        mode=RuleMode.REVIEW,
        unresolved_positions=held_unresolved_positions,
        duplicate_warnings=held_duplicate_warnings,
    )

    overall = max(rules_candidate.overall, rules_portfolio.overall)
    deltas = {
        "total_defined_max_loss": round(after.total_defined_max_loss - before.total_defined_max_loss, 4),
        "unbounded_loss_strategies": after.unbounded_loss_strategies - before.unbounded_loss_strategies,
        "near_dte_risk": round(after.near_dte_risk - before.near_dte_risk, 4),
        "held_option_count": len(held.options),
        "proposed_option_count": len(proposed_snapshot.options),
    }
    report = _render_card(trade, price_basis, proposed_risk, before, after, rules_candidate, rules_portfolio, overall, deltas)
    return ProposalCard(
        label=trade.label,
        price_basis=price_basis,
        proposed_risk=proposed_risk,
        before=before,
        after=after,
        rules_candidate=rules_candidate,
        rules_portfolio=rules_portfolio,
        overall=overall,
        deltas=deltas,
        report_markdown=report,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "unbounded/undefined"
    return f"${value:,.2f}"


def _price_basis_banner(price_basis: PriceBasis) -> str:
    if price_basis == PriceBasis.VERIFIED_QUOTE:
        return "> Price basis: **verified quote**."
    if price_basis == PriceBasis.USER_ESTIMATE:
        return ("> :warning: **ASSUMED PRICE (user estimate).** Payoff below is hypothetical. "
                "This candidate cannot be approved on estimated prices - WATCH at best.")
    return ("> :warning: **PRICE UNAVAILABLE (unknown).** Premiums were not supplied; payoff / IV / "
            "Greeks are unavailable and nothing is fabricated. This candidate cannot be approved.")


def _render_card(
    trade: ProposedTrade,
    price_basis: PriceBasis,
    proposed_risk: PortfolioRisk,
    before: PortfolioRisk,
    after: PortfolioRisk,
    rules_candidate: RulesReport,
    rules_portfolio: RulesReport,
    overall: Severity,
    deltas: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# Options Risk Studio - Proposed Trade Authorization")
    lines.append("")
    lines.append("> **PROPOSED / hypothetical.** This is a read-only pre-trade check. "
                 "It is NOT an order and places nothing. Separate from your held-book review.")
    lines.append("")
    lines.append(f"- Candidate: **{trade.label}**")
    lines.append(f"- Decision: **{overall.label}**")
    lines.append("")
    lines.append(_price_basis_banner(price_basis))
    lines.append("")

    # Proposed legs
    lines.append("## Proposed legs")
    lines.append("")
    lines.append("| Underlying | Right | Strike | Expiry | Qty | Premium | Price basis |")
    lines.append("|-----------|-------|--------|--------|-----|---------|-------------|")
    for leg in trade.option_legs:
        prem = f"{leg.premium:.2f}" if leg.premium is not None else "unavailable"
        lines.append(
            f"| {leg.underlying} | {leg.right.value} | {leg.strike:g} | {leg.expiry.isoformat()} | "
            f"{leg.quantity:+g} | {prem} | {leg.price_basis.value} |"
        )
    for s in trade.stock_legs:
        cost = f"{s.average_cost:.2f}" if s.average_cost is not None else "unavailable"
        lines.append(f"| {s.underlying} | shares | - | - | {s.quantity:+g} | {cost} | - |")
    lines.append("")

    # Proposed standalone risk
    lines.append("## Candidate standalone risk")
    lines.append("")
    lines.append("| ID | Strategy | DTE | Max loss | Max profit | Breakevens |")
    lines.append("|----|----------|-----|----------|-----------|-----------|")
    for r in proposed_risk.strategies:
        p = r.payoff
        if not p.available:
            ml = mp = be = "unavailable"
        else:
            ml = "unbounded" if p.unbounded_loss else _fmt_money(p.max_loss)
            mp = "unbounded" if p.unbounded_profit else _fmt_money(p.max_profit)
            be = ", ".join(f"{b:,.2f}" for b in p.breakevens) if p.breakevens else "-"
        dte = r.dte if r.dte is not None else "-"
        lines.append(f"| {r.strategy_id} | {r.strategy_type} | {dte} | {ml} | {mp} | {be} |")
    lines.append("")

    # Incremental portfolio impact
    lines.append("## Incremental portfolio impact (held -> held + candidate)")
    lines.append("")
    lines.append("| Metric | Before (held) | After | Delta |")
    lines.append("|--------|---------------|-------|-------|")
    lines.append(f"| Total defined max loss | {_fmt_money(before.total_defined_max_loss)} | "
                 f"{_fmt_money(after.total_defined_max_loss)} | {_fmt_money(deltas['total_defined_max_loss'])} |")
    lines.append(f"| Unbounded-loss strategies | {before.unbounded_loss_strategies} | "
                 f"{after.unbounded_loss_strategies} | {deltas['unbounded_loss_strategies']:+d} |")
    lines.append(f"| Near-DTE risk | {_fmt_money(before.near_dte_risk)} | "
                 f"{_fmt_money(after.near_dte_risk)} | {_fmt_money(deltas['near_dte_risk'])} |")
    lines.append("")
    if after.concentration_by_underlying:
        conc = ", ".join(f"{u} {f:.0%}" for u, f in after.concentration_by_underlying)
        lines.append(f"- After concentration by underlying: {conc}")
    lines.append(f"- Held option lines: {deltas['held_option_count']} (unchanged); "
                 f"candidate option lines: {deltas['proposed_option_count']} (kept separate)")
    lines.append("")

    # Verdict detail
    lines.append("## Authorization verdict")
    lines.append("")
    lines.append(f"**Overall: {overall.label}** "
                 "(candidate rules in PROPOSE mode; portfolio impact in REVIEW mode)")
    lines.append("")
    lines.append("### Candidate rules (PROPOSE - per-trade cap is a hard block)")
    lines.append("")
    lines.append("| Rule | Verdict | Reason |")
    lines.append("|------|---------|--------|")
    for v in rules_candidate.verdicts:
        lines.append(f"| {v.rule_id} | {v.severity.label} | {v.message.replace('|', chr(92) + '|')} |")
    lines.append("")
    lines.append("### Portfolio-impact rules (REVIEW - held size not blocked)")
    lines.append("")
    lines.append("| Rule | Verdict | Reason |")
    lines.append("|------|---------|--------|")
    for v in rules_portfolio.verdicts:
        lines.append(f"| {v.rule_id} | {v.severity.label} | {v.message.replace('|', chr(92) + '|')} |")
    lines.append("")
    lines.append("_Read-only. The rules engine outranks any LLM or persona. A BLOCK is final; "
                 "resolve it before you place anything yourself in your broker._")
    lines.append("")
    return "\n".join(lines)
