"""Frontend build-status check for the production serve path.

The official backend (``vibe-trading serve``) serves the pre-built SPA from
``frontend/dist``. If that build is **missing** or **stale** (older than the
current ``frontend/src``), serving it silently would show an out-of-date page —
exactly the trap this check exists to prevent. :func:`frontend_build_status`
returns a machine status plus a human build hint the serve entry prints loudly.

Pure and dependency-free so it is trivially unit-testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NamedTuple

BuildState = Literal["ok", "missing", "stale"]

# Source file extensions whose modification implies the dist is out of date.
_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json"}

_BUILD_HINT = "Run: cd frontend && npm run build"


class BuildStatus(NamedTuple):
    """Result of the dist freshness check."""

    state: BuildState
    message: str

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def _newest_source_mtime(src_dir: Path) -> float:
    """Return the newest mtime among relevant source files (0.0 if none)."""
    newest = 0.0
    if not src_dir.exists():
        return newest
    for path in src_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    return newest


def frontend_build_status(dist_dir: Path, src_dir: Path) -> BuildStatus:
    """Classify the built SPA as ok / missing / stale.

    Args:
        dist_dir: The ``frontend/dist`` directory the server would mount.
        src_dir: The ``frontend/src`` directory whose changes invalidate dist.

    Returns:
        A :class:`BuildStatus`. ``missing`` when ``dist/index.html`` is absent;
        ``stale`` when any source file is newer than the built ``index.html``;
        ``ok`` otherwise.
    """
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        return BuildStatus(
            "missing",
            f"No frontend build found at {dist_dir}. {_BUILD_HINT}",
        )
    try:
        dist_mtime = index_html.stat().st_mtime
    except OSError:
        return BuildStatus("missing", f"Cannot stat {index_html}. {_BUILD_HINT}")

    newest_src = _newest_source_mtime(src_dir)
    if newest_src > dist_mtime:
        return BuildStatus(
            "stale",
            f"frontend/dist is STALE (source is newer than the last build). {_BUILD_HINT}",
        )
    return BuildStatus("ok", "frontend/dist is up to date.")
