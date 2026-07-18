"""Tests for the structural no-real-trading guard."""

from __future__ import annotations

import pytest

from src.options_studio import guard


def test_guard_allows_read_only_by_default(monkeypatch):
    for name in guard._REAL_TRADING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Should not raise.
    guard.assert_no_real_trading()
    assert guard.real_trading_requested() is False


def test_guard_blocks_explicit_request():
    with pytest.raises(guard.RealTradingForbiddenError):
        guard.assert_no_real_trading(requested=True)


@pytest.mark.parametrize("var", list(guard._REAL_TRADING_ENV_VARS))
def test_guard_blocks_each_enable_env_var(monkeypatch, var):
    monkeypatch.setenv(var, "true")
    assert guard.real_trading_requested() is True
    with pytest.raises(guard.RealTradingForbiddenError):
        guard.assert_no_real_trading()


def test_guard_treats_falsey_values_as_off(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_TRADING", "0")
    assert guard.real_trading_requested() is False
    guard.assert_no_real_trading()  # should not raise
