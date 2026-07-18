"""Local-only private storage for the Options Risk Studio.

Raw broker statements are the most sensitive artifact in this project. They live
**only** under ``<repo>/data/private/`` and are never uploaded, never sent to an
LLM, and never committed. This module is the single place that decides where
those files go, and it hardens the directory on creation:

* The repo-root ``.gitignore`` already excludes ``data/private/`` (added by this
  fork). As belt-and-suspenders, :func:`get_private_dir` also drops a local
  ``.gitignore`` containing ``*`` inside ``data/private/`` so the raw files
  cannot be accidentally staged even if the root rule is edited away.

Parsed :class:`PortfolioSnapshot` objects (already de-identified) may be cached
as JSON for the UI; raw statement bytes are kept separate under
``data/private/statements/``.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.options_studio.models import PortfolioSnapshot

_REPO_ROOT = Path(__file__).resolve().parents[3]


def get_private_dir() -> Path:
    """Return (and harden) the ``data/private`` directory.

    Creates the directory tree and writes a local ``.gitignore`` (``*``) so the
    raw statements can never be committed even if the root ignore rule changes.
    """
    private = _REPO_ROOT / "data" / "private"
    private.mkdir(parents=True, exist_ok=True)
    gitignore = private / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Auto-generated: never commit private broker data.\n*\n", encoding="utf-8")
    return private


def get_statements_dir() -> Path:
    """Return the directory holding raw broker statement files."""
    d = get_private_dir() / "statements"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_snapshots_dir() -> Path:
    """Return the directory holding cached de-identified snapshot JSON."""
    d = get_private_dir() / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_raw_statement(content: str, *, safe_name: str) -> Path:
    """Persist raw statement text under ``data/private/statements/``.

    Args:
        content: Raw statement text.
        safe_name: A caller-sanitized base filename (no path separators). This
            function does not accept arbitrary paths; only a bare name.

    Returns:
        The path the content was written to.

    Raises:
        ValueError: If ``safe_name`` contains a path separator or ``..``.
    """
    if "/" in safe_name or "\\" in safe_name or ".." in safe_name:
        raise ValueError("safe_name must be a bare filename with no path separators.")
    target = get_statements_dir() / safe_name
    target.write_text(content, encoding="utf-8")
    return target


def save_snapshot(snapshot: PortfolioSnapshot, *, name: str = "latest") -> Path:
    """Cache a de-identified snapshot as JSON for the UI."""
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("name must be a bare filename with no path separators.")
    target = get_snapshots_dir() / f"{name}.json"
    target.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
