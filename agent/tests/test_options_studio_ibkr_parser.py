"""Tests for the IBKR statement parsers (Activity + Flex).

Fixtures are entirely fictional (see agent/tests/fixtures/options_studio/). No
real account data is used anywhere.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.options_studio.broker import parse_statement
from src.options_studio.broker.base import ParseError, get_parser_for
from src.options_studio.models import OptionRight

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "options_studio"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_activity_statement_detected_and_parsed():
    result = parse_statement(_read("ibkr_activity_sample.csv"), account_label="acct-test")
    snap = result.snapshot
    assert snap.source_label == "ibkr_activity_csv"
    assert snap.base_currency == "USD"
    assert snap.cash == 20000.0
    assert snap.account_label == "acct-test"
    # 200 AAPL shares imported.
    aapl = [u for u in snap.underlyings if u.symbol == "AAPL"]
    assert aapl and aapl[0].quantity == 200
    # 7 resolvable option lines (MYSTERY9 is skipped, not guessed).
    assert len(snap.options) == 7


def test_activity_statement_emits_warning_for_unresolved_option():
    result = parse_statement(_read("ibkr_activity_sample.csv"))
    codes = {w.code for w in result.warnings}
    assert "unresolved_option" in codes
    mystery = [w for w in result.warnings if w.context == "MYSTERY9"]
    assert mystery and mystery[0].code == "unresolved_option"


def test_activity_statement_parses_localized_ibkr_columns_without_duplicate_lots():
    localized = "\n".join(
        [
            "Statement,Header,域名称,域值",
            'Statement,Data,Period,"七月 17, 2026"',
            "账户信息,Header,域名称,域值",
            "账户信息,Data,基础货币,EUR",
            "现金报告,Header,货币总结,货币,总数",
            "现金报告,Data,期末现金,基础货币总结,1234.50",
            "金融产品信息,Header,资产分类,代码,描述,底层,乘数,到期,类型,执行,代码",
            "金融产品信息,Data,股票和指数期权,ACME   260821P00100000,ACME 21AUG26 100 P,ACME,100,2026-08-21,P,100,",
            "未平仓持仓,Header,DataDiscriminator,资产分类,货币,代码,开盘,数量,合约乘数,成本价格,价值,代码",
            "未平仓持仓,Data,Summary,股票,USD,ACME,-,10,1,20,250,",
            "未平仓持仓,Data,Summary,股票和指数期权,USD,ACME 21AUG26 100 P,-,-1,100,3,-300,",
            "未平仓持仓,Data,Lot,股票和指数期权,USD,ACME 21AUG26 100 P,2026-07-01,-1,,3,-300,",
        ]
    )

    result = parse_statement(localized)

    assert result.snapshot.base_currency == "EUR"
    assert result.snapshot.cash == 1234.50
    assert result.snapshot.as_of.isoformat() == "2026-07-17"
    assert len(result.snapshot.underlyings) == 1
    assert len(result.snapshot.options) == 1
    assert result.snapshot.options[0].underlying == "ACME"
    assert result.snapshot.options[0].quantity == -1
    assert "cash_not_found" not in {warning.code for warning in result.warnings}


def test_activity_statement_does_not_leak_account_number():
    # The real account id in the fixture must never appear on the snapshot.
    result = parse_statement(_read("ibkr_activity_sample.csv"), account_label="acct-1")
    blob = str(result.snapshot.to_dict())
    assert "U0000000-FICTIONAL" not in blob
    assert "SAMPLE ONLY" not in blob


def test_activity_statement_parses_trades_and_fees():
    result = parse_statement(_read("ibkr_activity_sample.csv"))
    lots = result.snapshot.trade_lots
    assert len(lots) == 3
    stock_fill = [t for t in lots if t.asset_kind == "stock"][0]
    assert stock_fill.symbol == "AAPL"
    assert stock_fill.quantity == 200
    assert stock_fill.fees == pytest.approx(1.0)
    opt_fill = [t for t in lots if t.asset_kind == "option" and t.symbol == "MU"][0]
    assert opt_fill.right == OptionRight.PUT
    assert opt_fill.strike == 90
    assert opt_fill.expiry == date(2026, 2, 20)


def test_flex_query_detected_and_parsed():
    text = _read("ibkr_flex_sample.csv")
    assert get_parser_for(text).format_label == "ibkr_flex_csv"
    result = parse_statement(text)
    snap = result.snapshot
    assert snap.source_label == "ibkr_flex_csv"
    assert len([o for o in snap.options]) == 4
    assert any(u.symbol == "AAPL" and u.quantity == 200 for u in snap.underlyings)


def test_unknown_format_raises_parse_error():
    with pytest.raises(ParseError):
        parse_statement("just,some,random,text\n1,2,3,4\n")


def test_utf8_bom_is_stripped_and_positions_still_parse():
    # A leading UTF-8 BOM (common in Windows/Excel round-trips) must not silently
    # drop the first section. Parse must still see the position + warn on MYSTERY9.
    bom = "﻿"
    text = bom + (
        "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Quantity,Mult,Cost Price,Value\n"
        "Open Positions,Data,Summary,Stocks,USD,AAPL,100,1,150,15000\n"
        "Open Positions,Data,Summary,Equity and Index Options,USD,MYSTERY9,1,100,1.00,100\n"
        "Cash Report,Header,Currency Summary,Currency,Total\n"
        "Cash Report,Data,Ending Cash,USD,20000\n"
    )
    result = parse_statement(text)
    assert any(u.symbol == "AAPL" and u.quantity == 100 for u in result.snapshot.underlyings)
    assert "unresolved_option" in {w.code for w in result.warnings}
