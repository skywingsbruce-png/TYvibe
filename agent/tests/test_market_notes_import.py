"""Tests for real-format Discord import + de-identified diagnostics (Goal B)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.options_studio.storage as os_storage
from src.api.market_notes_routes import _enrich
from src.market_notes.import_stats import build_import_stats
from src.market_notes.service import build_notes_bundle
from src.market_notes.themes import load_theme_defs

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "market_notes_import"


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(os_storage, "get_private_dir", lambda: tmp_path)
    notes_dir = tmp_path / "market-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.iterdir():
        shutil.copy(f, notes_dir / f.name)
    for var in ("MARKET_NOTES_LLM_ENABLED", "MARKET_NOTES_REMOTE_LLM_CONSENT"):
        monkeypatch.delenv(var, raising=False)


def test_discord_export_import_stats_are_deidentified(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    stats = build_import_stats()

    assert stats["totals"]["files"] == 1
    f = stats["files"][0]
    assert f["kind"] == "discord_json"
    assert f["raw_messages"] == 4
    assert f["structured_notes"] == 3  # one empty/attachment-only message skipped
    assert f["skipped"] == 1
    assert f["skip_reasons"] == {"empty_or_attachment_only": 1}
    assert f["parseable_timestamps"] == 3
    # Channel is de-identified, not the real name.
    assert f["channel_ref"] and f["channel_ref"].startswith("c-")

    # Authors are counted + de-identified; two distinct in the structured notes.
    assert stats["authors"]["distinct"] == 2
    assert all(a["author_ref"].startswith("a-") for a in stats["authors"]["per_author"])

    # No real names / channel names anywhere in the stats.
    blob = json.dumps(stats)
    for leak in ("Persona1", "Persona2", "user_one", "user_two", "anon-options-chat", "Anon Server"):
        assert leak not in blob


def test_enriched_bundle_has_theme_associations_and_import_stats(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    if not load_theme_defs():
        pytest.skip("config/market_radar.yaml themes not present in this checkout")

    bundle = _enrich(build_notes_bundle(now=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)))

    # Import diagnostics attached.
    assert bundle["import_stats"]["totals"]["structured_notes"] == 3

    # A note mentioning MU is a direct_symbol semiconductors association.
    mu_note = next(n for n in bundle["notes"] if "MU" in n["symbols"])
    semis = [a for a in mu_note["theme_associations"] if a["theme_key"] == "semiconductors"]
    assert semis and "direct_symbol" in semis[0]["association_types"]

    # A note saying "memory" (no MU) would be configured_keyword; the MU note may
    # also carry the keyword. Verify keyword association exists somewhere and is
    # labelled configured_keyword (never presented as a direct mention).
    kw_assocs = [
        a
        for n in bundle["notes"]
        for a in n["theme_associations"]
        if "configured_keyword" in a["association_types"]
    ]
    assert kw_assocs
    assert all(a["matched_keywords"] for a in kw_assocs)

    # Themes summary present and references semiconductors.
    assert "semiconductors" in bundle["themes"]


def test_no_trading_path_in_new_modules():
    pkg = Path(__file__).resolve().parent.parent / "src" / "market_notes"
    forbidden = ("place_order", "submit_order", "buy_to_open", "sell_to_open")
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} in {py.name}"
