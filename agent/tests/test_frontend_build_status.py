"""Tests for the production-serve frontend build-status guard."""

from __future__ import annotations

import os
from pathlib import Path

from src.api.frontend_build import frontend_build_status


def _touch(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_missing_dist_reports_missing_with_hint(tmp_path):
    dist = tmp_path / "dist"
    src = tmp_path / "src"
    src.mkdir()
    status = frontend_build_status(dist, src)
    assert status.state == "missing"
    assert not status.ok
    assert "npm run build" in status.message


def test_fresh_dist_is_ok(tmp_path):
    dist = tmp_path / "dist"
    src = tmp_path / "src"
    _touch(src / "App.tsx", 1000.0)
    _touch(dist / "index.html", 2000.0)  # built after source
    status = frontend_build_status(dist, src)
    assert status.state == "ok"
    assert status.ok


def test_stale_dist_is_flagged_not_silent(tmp_path):
    dist = tmp_path / "dist"
    src = tmp_path / "src"
    _touch(dist / "index.html", 1000.0)  # built first
    _touch(src / "App.tsx", 2000.0)  # source edited afterwards
    status = frontend_build_status(dist, src)
    assert status.state == "stale"
    assert not status.ok
    assert "STALE" in status.message
    assert "npm run build" in status.message
