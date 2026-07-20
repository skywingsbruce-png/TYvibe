"""Tests for Market Notes / opinion audit (hardened).

All fixtures are entirely fictional. The held book is forced to the bundled
fictional sample by pointing the private dir at a tmp path. A fixed ``now`` is
used so the time-window logic is deterministic.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import src.options_studio.storage as os_storage
from src.market_notes import llm as notes_llm
from src.market_notes.extractor import structure_message
from src.market_notes.llm import validate_llm_conclusions
from src.market_notes.models import Direction, RawMessage
from src.market_notes.service import build_notes_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "market_notes"
KNOWN = frozenset({"SNDK", "SPX", "SOXL", "MU"})


def _build(monkeypatch, tmp_path, now):
    monkeypatch.setattr(os_storage, "get_private_dir", lambda: tmp_path)
    notes_dir = tmp_path / "market-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.iterdir():
        shutil.copy(f, notes_dir / f.name)
    for var in ("MARKET_NOTES_LLM_ENABLED", "MARKET_NOTES_REMOTE_LLM_CONSENT"):
        monkeypatch.delenv(var, raising=False)
    return build_notes_bundle(now=now)


def _note(text: str, *, known=KNOWN):
    msg = RawMessage(author="X", timestamp=None, text=text, source_file="f", source_ref="line:1")
    return structure_message(msg, known)


# ---------------------------------------------------------------------------
# Fix 2 — symbol whitelist + semantic direction
# ---------------------------------------------------------------------------


def test_short_term_is_not_bearish_and_direction_unknown():
    n = _note("SNDK short term is volatile; no directional view.")
    assert n.symbols == ("SNDK",)
    assert n.direction == Direction.UNKNOWN


def test_fud_is_not_a_symbol():
    n = _note("FUD is noise; SNDK is unchanged.")
    assert n.symbols == ("SNDK",)  # FUD excluded


def test_cashtag_bearish_with_level():
    n = _note("$SNDK looks bearish below $1700.")
    assert "SNDK" in n.symbols
    assert n.direction == Direction.BEARISH
    assert "1700" in n.key_levels


def test_chinese_bearish_is_auditable():
    n = _note("看空 SNDK，跌破1700失效。")
    assert "SNDK" in n.symbols
    assert n.direction == Direction.BEARISH
    assert "1700" in n.key_levels
    assert n.invalidation_condition is not None


def test_noise_tokens_never_become_symbols():
    n = _note("AI and LOL and TACO are noise, but $TACO is a cashtag.")
    assert n.symbols == ("TACO",)  # only the explicit cashtag


def test_i_have_a_put_is_not_bearish():
    n = _note("I have a put on SNDK, long term hold otherwise.")
    assert "SNDK" in n.symbols
    assert n.direction == Direction.UNKNOWN


# ---------------------------------------------------------------------------
# Fix 3 — time-windowed current conclusions
# ---------------------------------------------------------------------------


def test_conflict_within_fresh_window(monkeypatch, tmp_path):
    # now within 24h of the intraday SNDK notes -> they are current.
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 7, 18, 20, tzinfo=timezone.utc))
    current = bundle["current"]
    assert current["available"] is True
    assert "SNDK" in {c["symbol"] for c in current["conflicts"]}


def test_stale_and_unknown_time_excluded(monkeypatch, tmp_path):
    # far-future now: every timestamped note is stale, orphan has no time.
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 9, 1, tzinfo=timezone.utc))
    current = bundle["current"]
    assert current["available"] is False
    assert "当前结论不可用" in current["message"]
    assert current["excluded_stale"] >= 1
    assert current["excluded_unknown_time"] >= 1  # the orphan manual block
    # History is intact even when the current card is empty.
    assert bundle["note_count"] >= 6


def test_current_card_reports_window_and_counts(monkeypatch, tmp_path):
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc))
    current = bundle["current"]
    assert current["windows"]  # window descriptors present
    assert "earliest" in current and "latest" in current
    # Fresh MU (days) / SPX (weeks) opinions are included; none are called
    # "short-term" — the label follows the horizon bucket.
    texts = " ".join(c["text"] for c in current["conclusions"])
    assert "Short-term opinion" not in texts


def test_notes_carry_parsed_timestamp_flag(monkeypatch, tmp_path):
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 7, 20, tzinfo=timezone.utc))
    orphan = next(n for n in bundle["notes"] if "general note about the market" in n["content"])
    assert orphan["timestamp_ok"] is False
    dave = next(n for n in bundle["notes"] if n["author"] == "Dave")
    assert dave["timestamp_ok"] is True


# ---------------------------------------------------------------------------
# Import + holdings + outcomes + citations
# ---------------------------------------------------------------------------


def test_imports_and_hashes(monkeypatch, tmp_path):
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert bundle["source_count"] == 3
    assert {s["kind"] for s in bundle["sources"]} == {"discord_json", "telegram", "manual"}
    for s in bundle["sources"]:
        assert len(s["sha256"]) == 64
    for n in bundle["notes"]:
        assert n["source_ref"] and len(n["content_hash"]) == 64


def test_holdings_link_to_sample_book(monkeypatch, tmp_path):
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert bundle["holdings"]["data_source"] == "sample"
    by_symbol = bundle["holdings"]["by_symbol"]
    assert "SNDK" in by_symbol and "MU" in by_symbol
    assert "SPX" not in by_symbol and "SOXL" not in by_symbol
    assert by_symbol["SNDK"]["count"] >= 1


def test_outcomes_all_unavailable(monkeypatch, tmp_path):
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 7, 18, 20, tzinfo=timezone.utc))
    for n in bundle["notes"]:
        assert n["outcome_1d"] == n["outcome_1w"] == n["outcome_1m"] == "unavailable"


def test_current_conclusions_are_cited(monkeypatch, tmp_path):
    bundle = _build(monkeypatch, tmp_path, datetime(2026, 7, 18, 20, tzinfo=timezone.utc))
    for c in bundle["current"]["conclusions"]:
        if not c["needs_review"]:
            assert c["citations"]


# ---------------------------------------------------------------------------
# Fix 1 — LLM privacy / consent
# ---------------------------------------------------------------------------


def test_llm_offline_by_default(monkeypatch):
    for var in ("MARKET_NOTES_LLM_ENABLED", "MARKET_NOTES_REMOTE_LLM_CONSENT"):
        monkeypatch.delenv(var, raising=False)
    assert notes_llm.llm_enabled() is False
    assert notes_llm.privacy_status() == "offline"
    assert notes_llm.remote_llm_allowed() is False


def test_enabled_without_consent_is_missing_consent_and_never_calls_llm(monkeypatch):
    monkeypatch.setenv("MARKET_NOTES_LLM_ENABLED", "true")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "some-model")
    monkeypatch.delenv("MARKET_NOTES_REMOTE_LLM_CONSENT", raising=False)  # consent missing

    assert notes_llm.privacy_status() == "enabled_missing_consent"
    assert notes_llm.remote_llm_allowed() is False

    # Even with flag + model set, no consent => build_llm must never be invoked.
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("build_llm must not be called without consent")

    import src.providers.llm as providers_llm

    monkeypatch.setattr(providers_llm, "build_llm", _boom, raising=False)
    note = _note("$SNDK looks bearish below $1700.")
    assert notes_llm.generate_llm_conclusions([note]) == []
    assert called["n"] == 0


def test_full_consent_reports_enabled_with_remote_consent(monkeypatch):
    monkeypatch.setenv("MARKET_NOTES_LLM_ENABLED", "true")
    monkeypatch.setenv("MARKET_NOTES_REMOTE_LLM_CONSENT", "true")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "some-model")
    assert notes_llm.privacy_status() == "enabled_with_remote_consent"
    assert notes_llm.remote_llm_allowed() is True


def test_llm_invalid_json_rejected():
    assert validate_llm_conclusions("not-json{", {}) == []


def test_llm_uncited_conclusion_needs_review():
    note = _note("$SNDK looks bearish below $1700.")
    by_id = {note.note_id: note}
    good = validate_llm_conclusions(
        '{"conclusions":[{"text":"SNDK leans bearish","note_ids":["%s"]}]}' % note.note_id, by_id
    )
    assert len(good) == 1 and good[0].needs_review is False and good[0].citations
    bad = validate_llm_conclusions('{"conclusions":[{"text":"SNDK to the moon","note_ids":["nope"]}]}', by_id)
    assert len(bad) == 1 and bad[0].needs_review is True and not bad[0].citations


def test_no_trading_path_in_package():
    pkg_dir = Path(__file__).resolve().parent.parent / "src" / "market_notes"
    forbidden = ("place_order", "submit_order", "buy_to_open", "sell_to_open", "ENABLE_REAL_TRADING=")
    for py in pkg_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} found in {py.name}"
