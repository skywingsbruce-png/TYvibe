"""Deterministic tests for Market Notes theme associations (Goal A)."""

from __future__ import annotations

import pytest

from src.market_notes.themes import ThemeDef, associate_note, load_theme_defs, themes_summary

SEMIS = ThemeDef(
    key="semiconductors",
    label="Semiconductors",
    members=frozenset({"SNDK", "MU", "NVDA", "SMH", "SOXL"}),
    keywords=("semiconductor", "memory", "chip"),
)
SOFTWARE = ThemeDef(key="software", label="Software", members=frozenset({"MSFT", "ORCL"}), keywords=("software", "saas"))
DEFS = [SEMIS, SOFTWARE]


def test_direct_symbol_association():
    out = associate_note(["SNDK"], "SNDK looks weak into close", DEFS)
    assert len(out) == 1
    a = out[0]
    assert a["theme_key"] == "semiconductors"
    assert a["association_types"] == ["direct_symbol"]
    assert a["matched_symbols"] == ["SNDK"]
    assert a["matched_keywords"] == []


def test_configured_keyword_association_only_when_configured():
    out = associate_note([], "the memory cycle is turning up", DEFS)
    assert len(out) == 1
    a = out[0]
    assert a["theme_key"] == "semiconductors"
    assert a["association_types"] == ["configured_keyword"]
    assert a["matched_keywords"] == ["memory"]
    assert a["matched_symbols"] == []


def test_direct_and_keyword_are_kept_distinct():
    out = associate_note(["MU"], "memory names like MU are strong", DEFS)
    assert len(out) == 1
    assert set(out[0]["association_types"]) == {"direct_symbol", "configured_keyword"}
    assert out[0]["matched_symbols"] == ["MU"]
    assert "memory" in out[0]["matched_keywords"]


def test_no_match_produces_no_association():
    # AAPL is not a semis/software member; "TACO"/"lol" are not keywords.
    assert associate_note(["AAPL"], "aapl only, TACO and lol noise", DEFS) == []


def test_keyword_uses_word_boundaries():
    # "microchip" must NOT trigger the "chip" keyword (no word boundary).
    assert associate_note([], "a microchip pun, nothing to see", DEFS) == []
    assert associate_note([], "chip shortage headlines", DEFS)[0]["matched_keywords"] == ["chip"]


def test_themes_summary_links_holdings():
    holdings_index = {
        "by_symbol": {
            "SNDK": {"count": 2, "earliest_expiry": "2026-01-23", "has_near_dte": True},
            "MU": {"count": 1, "earliest_expiry": "2026-02-20", "has_near_dte": False},
        }
    }
    notes = [{"theme_associations": associate_note(["MU"], "memory + MU", DEFS)}]
    summary = themes_summary(notes, holdings_index, DEFS)
    assert "semiconductors" in summary
    s = summary["semiconductors"]
    assert s["held_symbols"] == ["MU", "SNDK"]
    assert s["strategy_count"] == 3
    assert s["earliest_expiry"] == "2026-01-23"
    assert s["has_near_dte"] is True


def test_load_theme_defs_reuses_market_radar_config():
    defs = load_theme_defs()
    if not defs:
        pytest.skip("config/market_radar.yaml themes not present in this checkout")
    semis = next((d for d in defs if d.key == "semiconductors"), None)
    assert semis is not None
    # Reused member symbols come from market_radar.yaml; keywords from notes config.
    assert "SNDK" in semis.members
    assert semis.keywords  # configured keywords attached
