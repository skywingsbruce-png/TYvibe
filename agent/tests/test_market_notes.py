"""Tests for Market Notes / opinion audit (Phase 1).

All fixtures are entirely fictional. The held book is forced to the bundled
fictional sample by pointing the private dir at a tmp path.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import src.options_studio.storage as os_storage
from src.market_notes import llm as notes_llm
from src.market_notes.extractor import structure_message
from src.market_notes.llm import validate_llm_conclusions
from src.market_notes.models import Direction, Horizon, RawMessage
from src.market_notes.service import build_notes_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "market_notes"


@pytest.fixture
def bundle(monkeypatch, tmp_path):
    """Build a notes bundle from the fixtures with a sample held book."""
    monkeypatch.setattr(os_storage, "get_private_dir", lambda: tmp_path)
    notes_dir = tmp_path / "market-notes"
    notes_dir.mkdir(parents=True)
    for f in FIXTURES.iterdir():
        shutil.copy(f, notes_dir / f.name)
    # Ensure LLM stays OFF regardless of ambient env.
    monkeypatch.delenv("MARKET_NOTES_LLM_ENABLED", raising=False)
    return build_notes_bundle()


def _note(bundle, *, author=None, symbol=None):
    for n in bundle["notes"]:
        if author is not None and n["author"] != author:
            continue
        if symbol is not None and symbol not in n["symbols"]:
            continue
        return n
    return None


def test_imports_three_sources(bundle):
    assert bundle["source_count"] == 3
    kinds = {s["kind"] for s in bundle["sources"]}
    assert kinds == {"discord_json", "telegram", "manual"}
    for s in bundle["sources"]:
        assert len(s["sha256"]) == 64  # traceable hash
        assert s["imported_at"]  # real file mtime, not fabricated


def test_source_ref_and_hash_present(bundle):
    for n in bundle["notes"]:
        assert n["source_ref"]
        assert len(n["content_hash"]) == 64
        assert n["note_id"].startswith("n")


def test_structured_fields_extracted(bundle):
    alice = _note(bundle, author="Alice")
    assert alice is not None
    assert "SNDK" in alice["symbols"]
    assert alice["direction"] == Direction.BEARISH.value
    assert alice["horizon"] == Horizon.INTRADAY.value
    assert set(alice["key_levels"]) >= {"80", "82"}
    assert "reclaim 82" in (alice["invalidation_condition"] or "")


def test_missing_fields_are_unknown_not_guessed(bundle):
    # The second manual block has no author, no ticker, no direction.
    orphan = next(
        n for n in bundle["notes"] if "general note about the market" in n["content"]
    )
    assert orphan["author"] == "unknown"
    assert orphan["timestamp"] == "unknown"
    assert orphan["symbols"] == []
    assert orphan["direction"] == Direction.UNKNOWN.value
    assert orphan["setup_condition"] == "unknown"
    assert orphan["invalidation_condition"] == "unknown"


def test_conflict_detected_for_sndk(bundle):
    conflict_syms = {c["symbol"] for c in bundle["conflicts"]}
    assert "SNDK" in conflict_syms
    sndk = next(c for c in bundle["conflicts"] if c["symbol"] == "SNDK")
    assert "bullish" in sndk["directions"] and "bearish" in sndk["directions"]


def test_conclusions_are_cited_and_present_without_llm(bundle):
    assert bundle["llm_enabled"] is False
    assert bundle["conclusions"]  # deterministic conclusions exist offline
    for c in bundle["conclusions"]:
        if not c["needs_review"]:
            assert c["citations"]  # a formal conclusion always cites a note
            assert all(cite["note_id"] for cite in c["citations"])


def test_holdings_link_to_sample_book(bundle):
    holdings = bundle["holdings"]
    assert holdings["data_source"] == "sample"
    by_symbol = holdings["by_symbol"]
    # SNDK and MU are in the bundled sample held book; SOXL / SPX are not.
    assert "SNDK" in by_symbol
    assert "MU" in by_symbol
    assert "SPX" not in by_symbol
    assert by_symbol["SNDK"]["count"] >= 1
    assert by_symbol["SNDK"]["earliest_expiry"]
    assert "has_near_dte" in by_symbol["SNDK"]


def test_outcomes_all_unavailable(bundle):
    for n in bundle["notes"]:
        assert n["outcome_1d"] == "unavailable"
        assert n["outcome_1w"] == "unavailable"
        assert n["outcome_1m"] == "unavailable"


# --- LLM boundary (pure validation; no network) ---


def test_llm_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MARKET_NOTES_LLM_ENABLED", raising=False)
    assert notes_llm.llm_enabled() is False


def test_llm_invalid_json_rejected():
    assert validate_llm_conclusions("not-json{", {}) == []


def test_llm_uncited_conclusion_needs_review():
    msg = RawMessage(author="X", timestamp=None, text="SNDK short", source_file="f", source_ref="line:1")
    note = structure_message(msg)
    by_id = {note.note_id: note}
    # Cites a real note -> formal conclusion.
    good = validate_llm_conclusions(
        '{"conclusions":[{"text":"SNDK leans bearish","note_ids":["%s"]}]}' % note.note_id, by_id
    )
    assert len(good) == 1 and good[0].needs_review is False and good[0].citations
    # No valid citation -> needs manual review, never a formal conclusion.
    bad = validate_llm_conclusions('{"conclusions":[{"text":"SNDK to the moon","note_ids":["nope"]}]}', by_id)
    assert len(bad) == 1 and bad[0].needs_review is True and not bad[0].citations


def test_no_trading_path_in_package():
    pkg_dir = Path(__file__).resolve().parent.parent / "src" / "market_notes"
    forbidden = ("place_order", "submit_order", "buy_to_open", "sell_to_open", "ENABLE_REAL_TRADING=")
    for py in pkg_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} found in {py.name}"
