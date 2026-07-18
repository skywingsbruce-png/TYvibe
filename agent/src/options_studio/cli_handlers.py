"""``vibe-trading options-studio`` CLI subcommand.

Mirrors the repo convention used by ``src.factors.cli_handlers`` (Alpha Zoo) and
``src.hypotheses.cli_handlers``: a self-contained module exposing
:func:`add_subparser` and :func:`dispatch`, wired into ``cli/_legacy.py`` with two
lines. This keeps the large legacy dispatcher untouched beyond registration.

Only one action ships in Phase 1.5: ``reconcile``. It is local and read-only —
it parses an IBKR CSV, classifies strategies, runs the deterministic payoff
risk + YAML rules, and writes two artifacts under the git-ignored
``data/private/outputs/``. It never touches the network, an LLM, or a broker.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from src.options_studio import guard, storage
from src.options_studio.reconciliation import run_reconciliation

_SNAPSHOT_NAME = "portfolio_snapshot.json"
_REPORT_NAME = "reconciliation_report.md"


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``options-studio`` command group."""
    parser = subparsers.add_parser(
        "options-studio",
        help="Local, read-only options-risk tools (Personal Options Risk Studio)",
    )
    sub = parser.add_subparsers(dest="options_studio_command")

    reconcile = sub.add_parser(
        "reconcile",
        help="Reconcile a local IBKR CSV into a private risk report (read-only)",
    )
    reconcile.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to an IBKR Activity/Flex CSV. Defaults to the single CSV found under data/private/.",
    )
    reconcile.add_argument(
        "--account-label",
        type=str,
        default="acct-1",
        help="Opaque anonymized account label to stamp on the snapshot (never the real account number).",
    )
    reconcile.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Directory for the two output files. Defaults to data/private/outputs/.",
    )
    dry = reconcile.add_mutually_exclusive_group()
    dry.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Read-only: parse + compute + print summary, write output files (default).",
    )
    dry.add_argument(
        "--no-write",
        dest="dry_run",
        action="store_false",
        help="Compute and print the summary only; do not write the output files.",
    )


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch an ``options-studio`` subcommand. Returns a process exit code."""
    command = getattr(args, "options_studio_command", None)
    if command == "reconcile":
        return _cmd_reconcile(args)
    print("options-studio requires a subcommand. Try: vibe-trading options-studio reconcile --file <path>")
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
