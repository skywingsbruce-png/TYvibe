"""Hard risk-rules engine with veto power.

Reads thresholds from ``user_config/risk_rules.yaml`` (conservative defaults;
never hard-coded here) and evaluates a :class:`~src.options_studio.risk_engine.PortfolioRisk`
into a list of per-rule verdicts, each ``ALLOW`` / ``WATCH`` / ``BLOCK`` with an
explicit reason. The overall verdict is the most severe individual verdict.

This engine is intended to outrank any LLM output: callers should treat a
``BLOCK`` as final. The ``real_trading_forbidden`` rule is not configurable and
**fails closed** — it calls :func:`src.options_studio.guard.assert_no_real_trading`
and emits ``BLOCK`` if any real-trading toggle is on.

Phase 1 scope note: event-window rules (earnings / CPI / NFP / FOMC) and
leveraged-ETF-overlap rules are intentionally deferred to Phase 2; this module
ships the six rules the Phase 1 spec requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Optional

import yaml

from src.options_studio import guard
from src.options_studio.risk_engine import PortfolioRisk

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_RULES_PATH = _REPO_ROOT / "user_config" / "risk_rules.yaml"

#: Embedded conservative defaults used only if the YAML file is missing. Keeping
#: them here means the engine still fails safe (strict) rather than permissive
#: when the config is absent.
_EMBEDDED_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "limits": {
        "max_single_trade_loss": 500,
        "zero_dte": {"max_positions": 0},
        "near_dte_days": 14,
        "single_underlying_concentration_pct": 0.35,
        "theme_concentration_pct": 0.50,
    },
    "severity": {
        "zero_dte_over_limit": "block",
        "near_dte": "watch",
        "per_trade_over_limit": "block",
        "underlying_over_limit": "watch",
        "theme_over_limit": "watch",
    },
    "themes": {},
}


class Severity(IntEnum):
    """Verdict severity. Higher is more restrictive; ordering drives the roll-up."""

    ALLOW = 0
    WATCH = 1
    BLOCK = 2

    @classmethod
    def parse(cls, value: str) -> "Severity":
        return {"allow": cls.ALLOW, "watch": cls.WATCH, "block": cls.BLOCK}.get(
            str(value).strip().lower(), cls.WATCH
        )

    @property
    def label(self) -> str:
        return self.name


class RuleMode(str, Enum):
    """Which surface the rules run for.

    The two surfaces intentionally differ on *held* size limits (Phase 1.6):

    * ``REVIEW`` — reviewing positions you already hold. A held position that
      exceeds your personal per-trade loss preference is an **exposure alert
      (WATCH)**, not a BLOCK: you cannot un-hold it by being told "no". Held
      BLOCKs are reserved for structural problems (real-trading toggle,
      unbounded loss, a materially incomplete portfolio from unparsed
      positions).
    * ``PROPOSE`` — authorizing a *candidate new* trade. Here the per-trade max
      loss cap is a hard **BLOCK**, because the trade has not been taken yet and
      the whole point is to stop it before it is.
    """

    REVIEW = "review"
    PROPOSE = "propose"


class PriceBasis(str, Enum):
    """Provenance of a candidate trade's premium inputs (Phase 1.6).

    * ``VERIFIED_QUOTE`` — from a traceable real quote. Eligible for ALLOW.
    * ``USER_ESTIMATE`` — hand-entered / estimated. Usable for hypothetical
      payoff, but the candidate can never be ALLOW — at most WATCH — and the
      report must flag "assumed price".
    * ``UNKNOWN`` — no premium supplied. Nothing is fabricated: premium / IV /
      Greeks / quote stay unavailable and the candidate cannot be ALLOW.
    """

    VERIFIED_QUOTE = "verified_quote"
    USER_ESTIMATE = "user_estimate"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Optional[str]) -> "PriceBasis":
        return {
            "verified_quote": cls.VERIFIED_QUOTE,
            "verified": cls.VERIFIED_QUOTE,
            "user_estimate": cls.USER_ESTIMATE,
            "estimate": cls.USER_ESTIMATE,
            "unknown": cls.UNKNOWN,
        }.get(str(value or "").strip().lower(), cls.UNKNOWN)


@dataclass(frozen=True)
class RuleVerdict:
    """One rule's outcome."""

    rule_id: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.label,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class RulesReport:
    """Aggregate rules outcome."""

    overall: Severity
    verdicts: tuple[RuleVerdict, ...]

    def to_dict(self) -> dict:
        return {
            "overall": self.overall.label,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


def load_rules(path: Optional[Path] = None) -> dict[str, Any]:
    """Load rule config from YAML, falling back to embedded strict defaults."""
    target = path or _DEFAULT_RULES_PATH
    if not target.exists():
        return dict(_EMBEDDED_DEFAULTS)
    try:
        loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return dict(_EMBEDDED_DEFAULTS)
    # Shallow-merge onto defaults so a partial file cannot silently drop a limit.
    merged = dict(_EMBEDDED_DEFAULTS)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def evaluate(
    portfolio: PortfolioRisk,
    rules: Optional[dict[str, Any]] = None,
    *,
    mode: RuleMode = RuleMode.PROPOSE,
    real_trading_requested: bool = False,
    unresolved_positions: int = 0,
    duplicate_warnings: int = 0,
    price_basis: Optional[PriceBasis] = None,
    include_concentration: bool = True,
) -> RulesReport:
    """Evaluate all rules and return a rolled-up report.

    Args:
        portfolio: The analyzed portfolio risk.
        rules: Loaded rule config; defaults to :func:`load_rules`.
        mode: :class:`RuleMode`. ``REVIEW`` softens the per-trade loss cap on
            *held* positions to a WATCH exposure alert; ``PROPOSE`` keeps it a
            hard BLOCK for a candidate new trade. Defaults to ``PROPOSE`` (the
            strict default) so an unqualified call never under-warns.
        real_trading_requested: Explicit in-process real-trading request (must
            be ``False``; any truthy value trips the fail-closed guard rule).
        unresolved_positions: Count of held positions that could not be parsed
            (e.g. ``unresolved_option`` / ``unsupported_asset_category``). Any
            such gap makes the portfolio materially incomplete and BLOCKs.
        duplicate_warnings: Count of duplicate-line warnings (WATCH: risk may be
            double-counted).
        price_basis: For ``PROPOSE`` only — provenance of the candidate's
            premiums. ``USER_ESTIMATE`` / ``UNKNOWN`` cap the candidate at WATCH;
            only ``VERIFIED_QUOTE`` is eligible for ALLOW.
    """
    rules = rules or load_rules()
    limits = rules.get("limits", {})
    severity_cfg = rules.get("severity", {})
    verdicts: list[RuleVerdict] = []

    verdicts.append(_rule_real_trading(real_trading_requested))
    verdicts.append(_rule_portfolio_completeness(unresolved_positions, duplicate_warnings))
    verdicts.append(_rule_zero_dte(portfolio, limits, severity_cfg))
    verdicts.append(_rule_near_dte(portfolio, limits, severity_cfg))
    verdicts.append(_rule_per_trade_loss(portfolio, limits, severity_cfg, mode))
    if include_concentration:
        # Concentration is a PORTFOLIO property; skip it when judging a single
        # candidate in isolation (it would always read 100% of itself).
        verdicts.append(_rule_underlying_concentration(portfolio, limits, severity_cfg))
        verdicts.append(_rule_theme_concentration(portfolio, rules, limits, severity_cfg))
    if mode == RuleMode.PROPOSE and price_basis is not None:
        verdicts.append(_rule_price_basis(price_basis))

    overall = max((v.severity for v in verdicts), default=Severity.ALLOW)
    return RulesReport(overall=overall, verdicts=tuple(verdicts))


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def _rule_real_trading(requested: bool) -> RuleVerdict:
    """Fail-closed: any real-trading toggle => BLOCK. Always calls the guard."""
    try:
        guard.assert_no_real_trading(requested=requested)
    except guard.RealTradingForbiddenError as exc:
        return RuleVerdict(
            rule_id="real_trading_forbidden",
            severity=Severity.BLOCK,
            message=str(exc),
            details={"read_only": True},
        )
    return RuleVerdict(
        rule_id="real_trading_forbidden",
        severity=Severity.ALLOW,
        message="Read-only mode confirmed: no real-trading capability is enabled.",
        details={"read_only": True},
    )


def _rule_zero_dte(portfolio: PortfolioRisk, limits: dict, severity_cfg: dict) -> RuleVerdict:
    max_positions = int(limits.get("zero_dte", {}).get("max_positions", 0))
    zero_dte_ids = [s.strategy_id for s in portfolio.strategies if s.dte == 0]
    if len(zero_dte_ids) > max_positions:
        return RuleVerdict(
            rule_id="zero_dte_limit",
            severity=Severity.parse(severity_cfg.get("zero_dte_over_limit", "block")),
            message=f"{len(zero_dte_ids)} zero-DTE position(s) exceed the limit of {max_positions}.",
            details={"zero_dte_strategy_ids": zero_dte_ids, "max_positions": max_positions},
        )
    return RuleVerdict(
        rule_id="zero_dte_limit",
        severity=Severity.ALLOW,
        message=f"{len(zero_dte_ids)} zero-DTE position(s), within the limit of {max_positions}.",
        details={"zero_dte_count": len(zero_dte_ids)},
    )


def _rule_near_dte(portfolio: PortfolioRisk, limits: dict, severity_cfg: dict) -> RuleVerdict:
    near_days = int(limits.get("near_dte_days", 14))
    ids = list(portfolio.near_dte_strategy_ids)
    if ids:
        return RuleVerdict(
            rule_id="near_dte_warning",
            severity=Severity.parse(severity_cfg.get("near_dte", "watch")),
            message=f"{len(ids)} strategy(ies) expire within {near_days} days (near-term gamma/assignment risk).",
            details={"near_dte_strategy_ids": ids, "near_dte_risk": portfolio.near_dte_risk},
        )
    return RuleVerdict(
        rule_id="near_dte_warning",
        severity=Severity.ALLOW,
        message=f"No strategies expire within {near_days} days.",
        details={},
    )


def _rule_portfolio_completeness(unresolved_positions: int, duplicate_warnings: int) -> RuleVerdict:
    """BLOCK when held positions are unparsed; WATCH on duplicate lines.

    An unparsed position means the risk aggregate is missing real exposure, so
    the portfolio cannot be trusted — this is one of the few structural BLOCKs
    that apply even in ``REVIEW`` mode. Duplicates over-count rather than
    under-count, so they are a WATCH.
    """
    if unresolved_positions > 0:
        return RuleVerdict(
            rule_id="portfolio_completeness",
            severity=Severity.BLOCK,
            message=(
                f"{unresolved_positions} held position(s) could not be parsed; the portfolio risk "
                "aggregate is materially incomplete. Fix the parser before trusting it."
            ),
            details={"unresolved_positions": unresolved_positions},
        )
    if duplicate_warnings > 0:
        return RuleVerdict(
            rule_id="portfolio_completeness",
            severity=Severity.WATCH,
            message=f"{duplicate_warnings} duplicate-line warning(s); risk may be double-counted. Verify the export.",
            details={"duplicate_warnings": duplicate_warnings},
        )
    return RuleVerdict(
        rule_id="portfolio_completeness",
        severity=Severity.ALLOW,
        message="All positions parsed; no duplicate-line warnings.",
        details={},
    )


def _rule_per_trade_loss(portfolio: PortfolioRisk, limits: dict, severity_cfg: dict, mode: RuleMode) -> RuleVerdict:
    """Per-strategy max-loss rule, mode-aware (Phase 1.6).

    * Unbounded loss => BLOCK in **both** modes (a structural problem).
    * Finite loss over the cap => BLOCK when proposing a *new* trade, but only a
      WATCH exposure alert when reviewing a position you *already hold* (you
      cannot be blocked out of an existing holding).
    """
    cap = float(limits.get("max_single_trade_loss", 500))
    finite_severity = (
        Severity.parse(severity_cfg.get("per_trade_over_limit", "block"))
        if mode == RuleMode.PROPOSE
        else Severity.WATCH
    )
    unbounded: list[dict[str, Any]] = []
    finite: list[dict[str, Any]] = []
    for s in portfolio.strategies:
        payoff = s.payoff
        if not payoff.available:
            continue
        if payoff.unbounded_loss or payoff.max_loss is None:
            unbounded.append({"strategy_id": s.strategy_id, "max_loss": "unbounded", "underlying": s.underlying})
        elif abs(min(payoff.max_loss, 0.0)) > cap:
            finite.append(
                {"strategy_id": s.strategy_id, "max_loss": abs(payoff.max_loss), "underlying": s.underlying}
            )

    if not unbounded and not finite:
        return RuleVerdict(
            rule_id="per_trade_max_risk",
            severity=Severity.ALLOW,
            message=f"All defined-risk strategies are within the per-trade max loss of {cap:g}.",
            details={"cap": cap, "mode": mode.value},
        )

    severity = Severity.ALLOW
    parts: list[str] = []
    if unbounded:
        severity = max(severity, Severity.BLOCK)
        parts.append(f"{len(unbounded)} with unbounded loss (BLOCK)")
    if finite:
        severity = max(severity, finite_severity)
        label = "exposure alert" if mode == RuleMode.REVIEW else "over the cap"
        parts.append(f"{len(finite)} {label} above {cap:g}")
    return RuleVerdict(
        rule_id="per_trade_max_risk",
        severity=severity,
        message="; ".join(parts) + (
            " - held exposure over your preference is a WATCH, not a block."
            if mode == RuleMode.REVIEW and finite and not unbounded
            else "."
        ),
        details={"unbounded": unbounded, "over_cap": finite, "cap": cap, "mode": mode.value},
    )


def _rule_price_basis(price_basis: PriceBasis) -> RuleVerdict:
    """Candidate premium provenance rule (PROPOSE only).

    Only ``VERIFIED_QUOTE`` can be ALLOW. ``USER_ESTIMATE`` and ``UNKNOWN`` cap
    the candidate at WATCH so an assumed / missing price can never be approved.
    """
    if price_basis == PriceBasis.VERIFIED_QUOTE:
        return RuleVerdict(
            rule_id="price_basis",
            severity=Severity.ALLOW,
            message="Premiums come from a verified quote.",
            details={"price_basis": price_basis.value},
        )
    if price_basis == PriceBasis.USER_ESTIMATE:
        return RuleVerdict(
            rule_id="price_basis",
            severity=Severity.WATCH,
            message="Premiums are a USER ESTIMATE (assumed price); the candidate cannot be approved on estimated prices.",
            details={"price_basis": price_basis.value},
        )
    return RuleVerdict(
        rule_id="price_basis",
        severity=Severity.WATCH,
        message="Premiums are UNKNOWN; payoff/IV/Greeks stay unavailable and the candidate cannot be approved.",
        details={"price_basis": price_basis.value},
    )


def _rule_underlying_concentration(portfolio: PortfolioRisk, limits: dict, severity_cfg: dict) -> RuleVerdict:
    threshold = float(limits.get("single_underlying_concentration_pct", 0.35))
    severity = Severity.parse(severity_cfg.get("underlying_over_limit", "watch"))
    offenders = [
        {"underlying": u, "fraction": f}
        for u, f in portfolio.concentration_by_underlying
        if f > threshold
    ]
    if offenders:
        return RuleVerdict(
            rule_id="single_underlying_concentration",
            severity=severity,
            message=f"{len(offenders)} underlying(s) exceed {threshold:.0%} of total defined risk.",
            details={"offenders": offenders, "threshold": threshold},
        )
    return RuleVerdict(
        rule_id="single_underlying_concentration",
        severity=Severity.ALLOW,
        message=f"No single underlying exceeds {threshold:.0%} of total defined risk.",
        details={"threshold": threshold},
    )


def _rule_theme_concentration(portfolio: PortfolioRisk, rules: dict, limits: dict, severity_cfg: dict) -> RuleVerdict:
    threshold = float(limits.get("theme_concentration_pct", 0.50))
    severity = Severity.parse(severity_cfg.get("theme_over_limit", "watch"))
    themes = rules.get("themes", {}) or {}
    fraction_by_symbol = {u: f for u, f in portfolio.concentration_by_underlying}
    offenders: list[dict[str, Any]] = []
    for theme_id, theme in themes.items():
        symbols = {str(s).upper() for s in (theme.get("symbols", []) if isinstance(theme, dict) else [])}
        combined = round(sum(f for u, f in fraction_by_symbol.items() if u.upper() in symbols), 4)
        if combined > threshold:
            offenders.append(
                {
                    "theme": theme_id,
                    "label": theme.get("label", theme_id) if isinstance(theme, dict) else theme_id,
                    "fraction": combined,
                }
            )
    if offenders:
        return RuleVerdict(
            rule_id="theme_concentration",
            severity=severity,
            message=f"{len(offenders)} theme(s) exceed {threshold:.0%} of total defined risk.",
            details={"offenders": offenders, "threshold": threshold},
        )
    return RuleVerdict(
        rule_id="theme_concentration",
        severity=Severity.ALLOW,
        message=f"No correlated theme exceeds {threshold:.0%} of total defined risk.",
        details={"threshold": threshold},
    )
