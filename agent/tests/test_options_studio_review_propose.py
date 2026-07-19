"""Phase 1.6 tests: held-book REVIEW vs candidate-trade PROPOSE semantics.

Mandatory behaviours verified here:
* An existing long stock whose max loss exceeds the per-trade cap is a WATCH in
  review, NOT a BLOCK.
* A brand-new candidate with the same-size loss IS a BLOCK in propose.
* A candidate priced by user_estimate can be WATCH at best, never ALLOW.
* Held and proposed data stay fully isolated (JSON, report, counts).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.options_studio.classification import classify
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
    UnderlyingPosition,
)
from src.options_studio.proposal import (
    ProposedOptionLeg,
    ProposedTrade,
    evaluate_proposal,
    load_proposed_trade,
)
from src.options_studio.risk_engine import analyze_portfolio
from src.options_studio.rules_engine import (
    PriceBasis,
    RuleMode,
    Severity,
    evaluate,
    load_rules,
)

AS_OF = date(2026, 1, 15)
FAR = date(2026, 6, 19)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "options_studio"


def _held(*, underlyings=(), options=(), cash=0.0):
    return PortfolioSnapshot(
        account_label="acct-test",
        as_of=AS_OF,
        base_currency="USD",
        cash=cash,
        buying_power=None,
        underlyings=tuple(underlyings),
        options=tuple(options),
    )


def _debit_spread(underlying, expiry=FAR, long_cost=3.0, short_cost=1.0, basis=None):
    legs = [
        OptionContract(underlying, OptionRight.CALL, 100, expiry, 100, 1, average_cost=long_cost),
        OptionContract(underlying, OptionRight.CALL, 105, expiry, 100, -1, average_cost=short_cost),
    ]
    return legs


def _proposed_debit(underlying, long_strike=400, short_strike=420, long_prem=15.0, short_prem=6.0, basis="verified_quote"):
    return ProposedTrade(
        label=f"{underlying} {long_strike}/{short_strike} debit",
        option_legs=(
            ProposedOptionLeg(underlying, OptionRight.CALL, long_strike, date(2026, 2, 20), 1, long_prem, PriceBasis.parse(basis)),
            ProposedOptionLeg(underlying, OptionRight.CALL, short_strike, date(2026, 2, 20), -1, short_prem, PriceBasis.parse(basis)),
        ),
    )


# --- 1. Held long stock over cap => WATCH in review, BLOCK only under propose ---


def test_held_long_stock_over_cap_is_watch_in_review_not_block():
    held = _held(underlyings=[UnderlyingPosition("AAPL", 100, average_cost=150.0)])  # max loss ~15,000
    risk = analyze_portfolio(classify(held))
    review = evaluate(risk, load_rules(), mode=RuleMode.REVIEW)
    per_trade = next(v for v in review.verdicts if v.rule_id == "per_trade_max_risk")
    assert per_trade.severity == Severity.WATCH
    assert review.overall != Severity.BLOCK  # a held position is never blocked for being large

    # Contrast: the SAME risk judged as a candidate new trade is a hard BLOCK.
    propose = evaluate(risk, load_rules(), mode=RuleMode.PROPOSE)
    assert next(v for v in propose.verdicts if v.rule_id == "per_trade_max_risk").severity == Severity.BLOCK


# --- 2. Same-size NEW candidate in propose => BLOCK ---


def test_candidate_over_cap_blocks_in_propose():
    held = _held(options=_debit_spread("ZAA"))  # tiny clean held book
    trade = _proposed_debit("MSFT")  # max loss 900 > 500
    card = evaluate_proposal(held, trade)
    per_trade = next(v for v in card.rules_candidate.verdicts if v.rule_id == "per_trade_max_risk")
    assert per_trade.severity == Severity.BLOCK
    assert card.overall == Severity.BLOCK


# --- 3. user_estimate candidate => WATCH at most, never ALLOW ---


def test_user_estimate_candidate_is_at_most_watch():
    held = _held(options=_debit_spread("ZAA"))
    # Small candidate (max loss 80) so the per-trade cap is not the driver.
    trade = ProposedTrade(
        label="ZBB tiny debit (estimate)",
        option_legs=(
            ProposedOptionLeg("ZBB", OptionRight.CALL, 100, FAR, 1, 1.20, PriceBasis.USER_ESTIMATE),
            ProposedOptionLeg("ZBB", OptionRight.CALL, 102, FAR, -1, 0.40, PriceBasis.USER_ESTIMATE),
        ),
    )
    card = evaluate_proposal(held, trade)
    assert card.price_basis == PriceBasis.USER_ESTIMATE
    price_rule = next(v for v in card.rules_candidate.verdicts if v.rule_id == "price_basis")
    assert price_rule.severity == Severity.WATCH
    assert card.overall == Severity.WATCH  # never ALLOW on an estimated price
    assert "ASSUMED PRICE" in card.report_markdown


def test_verified_small_candidate_can_allow():
    held = _held(options=_debit_spread("ZAA"))
    trade = ProposedTrade(
        label="ZBB tiny debit (verified)",
        option_legs=(
            ProposedOptionLeg("ZBB", OptionRight.CALL, 100, FAR, 1, 1.20, PriceBasis.VERIFIED_QUOTE),
            ProposedOptionLeg("ZBB", OptionRight.CALL, 102, FAR, -1, 0.40, PriceBasis.VERIFIED_QUOTE),
        ),
    )
    card = evaluate_proposal(held, trade)
    # candidate's own rules should permit ALLOW (verified price, small loss).
    assert card.rules_candidate.overall == Severity.ALLOW


# --- 4. Held vs proposed isolation ---


def test_held_and_proposed_are_isolated():
    held = _held(options=_debit_spread("ZAA"))
    original_held_options = len(held.options)
    trade = _proposed_debit("ZBB")
    card = evaluate_proposal(held, trade)

    # Held object is untouched.
    assert len(held.options) == original_held_options == 2

    payload = json.loads(card.to_json())
    assert set(payload) >= {"proposed", "held_before", "portfolio_after", "deltas"}
    assert payload["deltas"]["held_option_count"] == 2
    assert payload["deltas"]["proposed_option_count"] == 2

    before_underlyings = {s["underlying"] for s in payload["held_before"]["strategies"]}
    proposed_underlyings = {s["underlying"] for s in payload["proposed"]["strategies"]}
    assert "ZBB" not in before_underlyings  # candidate not counted in held
    assert proposed_underlyings == {"ZBB"}
    assert "ZAA" not in proposed_underlyings  # held not counted in candidate


# --- price basis / loader / unknown premium ---


def test_loader_defaults_price_basis():
    text = (
        "label: t\n"
        "legs:\n"
        "  - {underlying: MSFT, right: call, strike: 400, expiry: 2026-02-20, quantity: 1, premium: 15, price_basis: verified_quote}\n"
        "  - {underlying: MSFT, right: put, strike: 380, expiry: 2026-02-20, quantity: -1, premium: 3}\n"
        "  - {underlying: MSFT, right: call, strike: 420, expiry: 2026-02-20, quantity: -1}\n"
    )
    trade = load_proposed_trade(text)
    assert trade.option_legs[0].price_basis == PriceBasis.VERIFIED_QUOTE
    assert trade.option_legs[1].price_basis == PriceBasis.USER_ESTIMATE  # premium but no basis
    assert trade.option_legs[2].price_basis == PriceBasis.UNKNOWN  # no premium
    assert trade.aggregate_price_basis() == PriceBasis.UNKNOWN  # worst wins


def test_unknown_premium_yields_unavailable_and_not_allow():
    held = _held(options=_debit_spread("ZAA"))
    trade = ProposedTrade(
        label="unknown price candidate",
        option_legs=(
            ProposedOptionLeg("ZBB", OptionRight.CALL, 100, FAR, 1, None, PriceBasis.UNKNOWN),
        ),
    )
    card = evaluate_proposal(held, trade)
    assert card.price_basis == PriceBasis.UNKNOWN
    # Payoff unavailable (no premium) — never fabricated.
    assert all(not s.payoff.available for s in card.proposed_risk.strategies)
    assert card.rules_candidate.overall != Severity.ALLOW
    assert "PRICE UNAVAILABLE" in card.report_markdown


# --- CLI propose integration (writes to tmp only) ---


def test_propose_cli_writes_card_and_isolates(tmp_path):
    from cli import _legacy

    rc = _legacy.main(
        [
            "options-studio",
            "propose",
            "--file",
            str(FIXTURES / "ibkr_activity_sample.csv"),
            "--trade",
            str(FIXTURES / "proposed_trade.yaml"),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    card_json = tmp_path / "proposal_card.json"
    card_md = tmp_path / "proposal_card.md"
    assert card_json.exists() and card_md.exists()
    payload = json.loads(card_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "propose"
    assert payload["deltas"]["held_option_count"] == 7
    assert payload["deltas"]["proposed_option_count"] == 2
    blob = card_json.read_text(encoding="utf-8") + card_md.read_text(encoding="utf-8")
    assert "U0000000-FICTIONAL" not in blob and "SAMPLE ONLY" not in blob


def test_review_alias_matches_reconcile(tmp_path):
    from cli import _legacy

    rc = _legacy.main(
        ["options-studio", "review", "--file", str(FIXTURES / "ibkr_activity_sample.csv"), "--out-dir", str(tmp_path)]
    )
    assert rc == 0
    assert (tmp_path / "reconciliation_report.md").exists()


@pytest.mark.parametrize("held_max_loss_cost", [150.0])
def test_review_pipeline_block_is_structural_not_size(held_max_loss_cost, tmp_path):
    """A clean, large held book reviews as WATCH (size), not BLOCK."""
    from src.options_studio.reconciliation import run_reconciliation

    # A clean single-underlying book: 100 shares, large loss, NO unresolved lines.
    csv = (
        "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Value\n"
        f"Open Positions,Data,Summary,Stocks,USD,AAPL,100,1,{held_max_loss_cost},15000\n"
    )
    result = run_reconciliation(csv)
    # No unresolved => completeness ALLOW; big held stock => WATCH, not BLOCK.
    assert result.rules_report.overall == Severity.WATCH
    completeness = next(v for v in result.rules_report.verdicts if v.rule_id == "portfolio_completeness")
    assert completeness.severity == Severity.ALLOW
