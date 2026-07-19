"""``vibe-trading options-studio`` CLI subcommand.

Mirrors the repo convention used by ``src.factors.cli_handlers`` (Alpha Zoo) and
``src.hypotheses.cli_handlers``: a self-contained module exposing
:func:`add_subparser` and :func:`dispatch`, wired into ``cli/_legacy.py`` with two
lines. This keeps the large legacy dispatcher untouched beyond registration.

Actions (all local, read-only, never a broker order):

* ``review`` (alias ``reconcile``) — review your HELD positions. Held size over
  your preference is a WATCH exposure alert, not a BLOCK.
* ``propose --trade <yaml>`` — authorize a CANDIDATE new trade against your hard
  rules (per-trade cap is a hard BLOCK here), with the candidate kept strictly
  separate from the held book.

Outputs are written under the git-ignored ``data/private/outputs/``. Nothing
here touches the network, an LLM, or a broker.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from src.options_studio import guard, storage
from src.options_studio.proposal import ProposalError, evaluate_proposal, load_proposed_trade
from src.options_studio.reconciliation import run_reconciliation

_SNAPSHOT_NAME = "portfolio_snapshot.json"
_REPORT_NAME = "reconciliation_report.md"
_PROPOSAL_JSON_NAME = "proposal_card.json"
_PROPOSAL_MD_NAME = "proposal_card.md"


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``options-studio`` command group."""
    parser = subparsers.add_parser(
        "options-studio",
        help="Local, read-only options-risk tools (Personal Options Risk Studio)",
    )
    sub = parser.add_subparsers(dest="options_studio_command")

    def _add_common_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--file",
            type=str,
            default=None,
            help="Path to an IBKR Activity/Flex CSV. Defaults to the single CSV found under data/private/.",
        )
        p.add_argument(
            "--account-label",
            type=str,
            default="acct-1",
            help="Opaque anonymized account label to stamp on the snapshot (never the real account number).",
        )
        p.add_argument(
            "--out-dir",
            type=str,
            default=None,
            help="Directory for the output file(s). Defaults to data/private/outputs/.",
        )
        grp = p.add_mutually_exclusive_group()
        grp.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            default=True,
            help="Read-only: parse + compute + print summary, write output file(s) (default).",
        )
        grp.add_argument(
            "--no-write",
            dest="dry_run",
            action="store_false",
            help="Compute and print the summary only; do not write output file(s).",
        )

    # review == held-book review (canonical name). reconcile stays as an alias.
    _add_common_args(sub.add_parser("review", help="Review your HELD positions (max risk, DTE, concentration)"))
    _add_common_args(sub.add_parser("reconcile", help="Alias of 'review' (held-book reconciliation)"))

    # propose == read-only pre-trade authorization for a CANDIDATE new trade.
    propose = sub.add_parser(
        "propose",
        help="Authorize a CANDIDATE new trade against your hard rules (read-only, not an order)",
    )
    _add_common_args(propose)
    propose.add_argument(
        "--trade",
        type=str,
        required=True,
        help="Path to a proposed-trade YAML/JSON (place it under data/private/). Defines the candidate legs.",
    )


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch an ``options-studio`` subcommand. Returns a process exit code."""
    command = getattr(args, "options_studio_command", None)
    if command in {"review", "reconcile"}:
        return _cmd_reconcile(args)
    if command == "propose":
        return _cmd_propose(args)
    print("options-studio requires a subcommand. Try: vibe-trading options-studio review --file <path>")
    return 2


def _resolve_file(raw: Optional[str]) -> Path:
    """Resolve the input CSV path, auto-detecting a single one under data/private/."""
    if raw:
        path = Path(raw).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Statement file not found: {path}")
        return path
    detected = storage.find_single_statement()
    if detected is None:
        raise FileNotFoundError(
            "No --file given and could not auto-detect a single CSV under data/private/. "
            "Place exactly one CSV there, or pass --file <path>."
        )
    return detected


def _cmd_reconcile(args: argparse.Namespace) -> int:
    # Read-only posture is structural; assert it up front and fail closed.
    try:
        guard.assert_no_real_trading()
    except guard.RealTradingForbiddenError as exc:
        print(f"Refusing to run: {exc}")
        return 2

    try:
        csv_path = _resolve_file(args.file)
    except FileNotFoundError as exc:
        print(str(exc))
        return 2

    text = csv_path.read_text(encoding="utf-8", errors="replace")
    result = run_reconciliation(text, account_label=args.account_label)

    # Print a compact summary to stdout (no account identifiers).
    print("Options Studio reconciliation (local, read-only)")
    print(f"  source        : {result.snapshot.source_label}")
    print(f"  as of         : {result.snapshot.as_of.isoformat()}")
    print(f"  stocks        : {len(result.snapshot.underlyings)}")
    print(f"  options       : {len(result.snapshot.options)}")
    print(f"  trades        : {len(result.snapshot.trade_lots)}")
    print(f"  strategies    : {len(result.snapshot.strategies)}")
    print(f"  warnings      : {len(result.warnings)}")
    print(f"  total max loss: ${result.portfolio_risk.total_defined_max_loss:,.2f} "
          f"(+{result.portfolio_risk.unbounded_loss_strategies} unbounded)")
    print(f"  rules overall : {result.rules_report.overall.label}")

    if not args.dry_run:
        print("  (--no-write) output files not written.")
        return 0

    out_dir = Path(args.out_dir).expanduser() if args.out_dir else storage.get_outputs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = out_dir / _SNAPSHOT_NAME
    report_path = out_dir / _REPORT_NAME
    snapshot_path.write_text(result.snapshot_json(), encoding="utf-8")
    report_path.write_text(result.report_markdown, encoding="utf-8")

    print(f"  wrote         : {snapshot_path}")
    print(f"  wrote         : {report_path}")
    return 0


def _cmd_propose(args: argparse.Namespace) -> int:
    try:
        guard.assert_no_real_trading()
    except guard.RealTradingForbiddenError as exc:
        print(f"Refusing to run: {exc}")
        return 2

    try:
        held_path = _resolve_file(args.file)
    except FileNotFoundError as exc:
        print(str(exc))
        return 2
    trade_path = Path(args.trade).expanduser()
    if not trade_path.exists():
        print(f"Proposed-trade file not found: {trade_path}")
        return 2

    held_text = held_path.read_text(encoding="utf-8", errors="replace")
    held_result = run_reconciliation(held_text, account_label=args.account_label)
    held_unresolved = sum(
        1 for w in held_result.warnings if w.code in {"unresolved_option", "unsupported_asset_category"}
    )
    held_duplicates = sum(1 for w in held_result.warnings if w.code in {"duplicate_position", "duplicate_trade"})

    try:
        trade = load_proposed_trade(trade_path.read_text(encoding="utf-8"))
    except ProposalError as exc:
        print(f"Invalid proposed trade: {exc}")
        return 2

    card = evaluate_proposal(
        held_result.snapshot,
        trade,
        held_unresolved_positions=held_unresolved,
        held_duplicate_warnings=held_duplicates,
    )

    print("Options Studio proposed-trade authorization (local, read-only; NOT an order)")
    print(f"  candidate     : {card.label}")
    print(f"  price basis   : {card.price_basis.value}")
    print(f"  candidate rule: {card.rules_candidate.overall.label}")
    print(f"  portfolio rule: {card.rules_portfolio.overall.label}")
    print(f"  DECISION      : {card.overall.label}")
    print(f"  held options  : {len(held_result.snapshot.options)} (unchanged); "
          f"candidate legs: {len(trade.option_legs)}")

    if not args.dry_run:
        print("  (--no-write) output files not written.")
        return 0

    out_dir = Path(args.out_dir).expanduser() if args.out_dir else storage.get_outputs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    card_json = out_dir / _PROPOSAL_JSON_NAME
    card_md = out_dir / _PROPOSAL_MD_NAME
    card_json.write_text(card.to_json(), encoding="utf-8")
    card_md.write_text(card.report_markdown, encoding="utf-8")
    print(f"  wrote         : {card_json}")
    print(f"  wrote         : {card_md}")
    return 0
