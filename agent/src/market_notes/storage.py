"""Local-only storage for Market Notes.

Raw note files live under ``data/private/market-notes/``, inside the already
git-ignored + hardened ``data/private/`` tree (see
:func:`src.options_studio.storage.get_private_dir`). Nothing here uploads or
transmits anything.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from src.options_studio import storage as _os_storage

#: File extensions we attempt to import.
_SUPPORTED_SUFFIXES = {".json", ".txt", ".md"}


def get_market_notes_dir() -> Path:
    """Return (and create) ``data/private/market-notes/``."""
    d = _os_storage.get_private_dir() / "market-notes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_source_files() -> list[Path]:
    """Return supported note files under the market-notes dir, name-sorted."""
    root = get_market_notes_dir()
    files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES]
    return sorted(files, key=lambda p: p.name)


def sha256_of(path: Path) -> str:
    """Return the SHA256 hex digest of a file's bytes."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def imported_at_iso(path: Path) -> str:
    """Return the file's last-modified time as an ISO8601 UTC string."""
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
