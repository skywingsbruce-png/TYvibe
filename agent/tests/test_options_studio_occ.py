"""Unit tests for OCC / IBKR option-symbol parsing."""

from __future__ import annotations

from datetime import date

from src.options_studio.broker.occ import (
    parse_expiry,
    parse_occ_symbol,
    parse_option_description,
)
from src.options_studio.models import OptionRight


def test_parse_canonical_occ_symbol():
    parsed = parse_occ_symbol("AAPL 260220C00200000")
    assert parsed is not None
    assert parsed.underlying == "AAPL"
    assert parsed.right == OptionRight.CALL
    assert parsed.strike == 200.0
    assert parsed.expiry == date(2026, 2, 20)


def test_parse_occ_symbol_put_and_fractional_strike():
    parsed = parse_occ_symbol("MU260220P00092500")
    assert parsed is not None
    assert parsed.right == OptionRight.PUT
    assert parsed.strike == 92.5
    assert parsed.underlying == "MU"


def test_parse_occ_symbol_rejects_non_option():
    assert parse_occ_symbol("AAPL") is None
    assert parse_occ_symbol("") is None
    assert parse_occ_symbol("NOTASYMBOL123") is None


def test_parse_option_description():
    parsed = parse_option_description("AAPL 20FEB26 200 C")
    assert parsed is not None
    assert parsed.underlying == "AAPL"
    assert parsed.right == OptionRight.CALL
    assert parsed.strike == 200.0
    assert parsed.expiry == date(2026, 2, 20)


def test_parse_expiry_formats():
    assert parse_expiry("20260220") == date(2026, 2, 20)
    assert parse_expiry("2026-02-20") == date(2026, 2, 20)
    assert parse_expiry("20FEB26") == date(2026, 2, 20)
    assert parse_expiry("garbage") is None
