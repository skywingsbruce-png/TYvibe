"""IBKR broker-statement adapters.

Two IBKR export shapes are supported at a minimal-but-usable level:

* **Activity Statement CSV** — the multi-section export where each row is tagged
  with a section name and a ``Header`` / ``Data`` discriminator. Parsed by
  :class:`IbkrActivityStatementParser`.
* **Flex Query CSV** — a flat, single-header CSV (one row per position or
  trade). Parsed by :class:`IbkrFlexQueryParser`.

Both produce a de-identified :class:`ParseResult`. The real IBKR account id is
deliberately dropped: the caller-supplied ``account_label`` is the only account
handle that survives.

Numbers are parsed leniently (thousands separators, parentheses-as-negative) but
structure is parsed strictly: an option line whose strike / expiry / right
cannot be resolved from the instrument table, its symbol, or its description is
**skipped with a structured :class:`ParseWarning`** — never guessed. A
:class:`ParseError` is raised only when the text is not a usable statement of
that shape at all.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Optional

from src.options_studio.broker.base import (
    BrokerStatementParser,
    ParseError,
    ParseResult,
    ParseWarning,
    register_parser,
)
from src.options_studio.broker.occ import (
    parse_expiry,
    parse_occ_symbol,
    parse_option_description,
)
from src.options_studio.models import (
    OptionContract,
    OptionRight,
    PortfolioSnapshot,
    TradeLot,
    UnderlyingPosition,
)


def _num(raw: Optional[str]) -> Optional[float]:
    """Parse a lenient numeric cell; return ``None`` when empty/unparseable."""
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if not text or text in {"--", "-", "N/A"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def _is_option_category(category: str) -> bool:
    return "option" in category.lower() or "期权" in category


def _is_stock_category(category: str) -> bool:
    cat = category.lower()
    return "stock" in cat or cat in {"stk", "equity", "equities"} or "股票" in category


# IBKR Activity Statements use localized section and column names when exported
# from a non-English portal. Canonicalizing only structural labels keeps values
# untouched and lets the existing conservative parsers make the risk decisions.
_SECTION_ALIASES = {
    "账户信息": "Account Information",
    "净资产值": "Net Asset Value",
    "现金报告": "Cash Report",
    "未平仓持仓": "Open Positions",
    "交易": "Trades",
    "金融产品信息": "Financial Instrument Information",
}

_COLUMN_ALIASES = {
    "域名称": "Field Name",
    "域值": "Field Value",
    "资产分类": "Asset Category",
    "货币": "Currency",
    "代码": "Symbol",
    "描述": "Description",
    "数量": "Quantity",
    "合约乘数": "Mult",
    "乘数": "Multiplier",
    "成本价格": "Cost Price",
    "价值": "Value",
    "底层": "Underlying",
    "到期": "Expiry",
    "类型": "Type",
    "执行": "Strike",
    "日期/时间": "Date/Time",
    "交易价格": "T. Price",
    "佣金/税": "Comm/Fee",
    "总数": "Total",
    "货币总结": "Currency Summary",
}

_CHINESE_MONTHS = {
    "一月": 1,
    "二月": 2,
    "三月": 3,
    "四月": 4,
    "五月": 5,
    "六月": 6,
    "七月": 7,
    "八月": 8,
    "九月": 9,
    "十月": 10,
    "十一月": 11,
    "十二月": 12,
}


class IbkrActivityStatementParser(BrokerStatementParser):
    """Parser for IBKR multi-section Activity Statement CSV exports."""

    format_label = "ibkr_activity_csv"

    def can_parse(self, text: str) -> bool:
        head = text.lstrip()[:4000]
        has_sections = "Header" in head and "Data" in head
        has_known_section = any(
            marker in head
            for marker in (
                "Open Positions",
                "Financial Instrument Information",
                "Statement,",
                "未平仓持仓",
                "金融产品信息",
            )
        )
        return has_sections and has_known_section

    def parse(self, text: str, *, account_label: str = "acct-1") -> ParseResult:
        sections = _read_sections(text)
        if not sections:
            raise ParseError("No recognizable IBKR Activity Statement sections found.")

        warnings: list[ParseWarning] = []
        instrument_map = _build_instrument_map(sections.get("Financial Instrument Information", []))
        base_currency = _detect_currency(sections)

        underlyings, options = _parse_open_positions(
            sections.get("Open Positions", []), instrument_map, warnings
        )
        trade_lots = _parse_trades(sections.get("Trades", []), instrument_map, warnings)
        cash = _parse_ending_cash(sections.get("Cash Report", []), base_currency)
        if cash is None:
            cash = _parse_net_asset_cash(sections.get("Net Asset Value", []))
        if cash is None:
            warnings.append(
                ParseWarning(
                    code="cash_not_found",
                    message="Ending cash not found in Cash Report; defaulted to 0.0.",
                    context="Cash Report",
                )
            )
        as_of = _detect_as_of(sections)

        snapshot = PortfolioSnapshot(
            account_label=account_label,
            as_of=as_of,
            base_currency=base_currency,
            cash=cash if cash is not None else 0.0,
            buying_power=None,
            underlyings=tuple(underlyings),
            options=tuple(options),
            trade_lots=tuple(trade_lots),
            source_label=self.format_label,
        )
        return ParseResult(snapshot=snapshot, warnings=tuple(warnings))


class IbkrFlexQueryParser(BrokerStatementParser):
    """Parser for IBKR Flex Query flat CSV exports (positions and/or trades)."""

    format_label = "ibkr_flex_csv"

    def can_parse(self, text: str) -> bool:
        head = text.lstrip()
        first_line = head.splitlines()[0] if head else ""
        lowered = first_line.lower()
        flex_markers = ("assetclass", "put/call", "putcall", "underlyingsymbol")
        looks_flex = any(marker in lowered for marker in flex_markers)
        not_activity = "header" not in lowered
        return looks_flex and not_activity

    def parse(self, text: str, *, account_label: str = "acct-1") -> ParseResult:
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise ParseError("IBKR Flex CSV has a header but no data rows.")

        warnings: list[ParseWarning] = []
        underlyings: list[UnderlyingPosition] = []
        options: list[OptionContract] = []
        trade_lots: list[TradeLot] = []
        base_currency = "USD"
        as_of = date.today()

        for row in rows:
            norm = {_norm_key(k): (v or "").strip() for k, v in row.items()}
            currency = norm.get("currency") or ""
            if currency:
                base_currency = currency
            report_date = parse_expiry(norm.get("reportdate", "") or norm.get("date", ""))
            if report_date is not None:
                as_of = report_date

            asset_class = norm.get("assetclass", "") or norm.get("assetcategory", "")
            symbol = (norm.get("symbol", "") or "").upper()
            qty = _num(norm.get("position") or norm.get("quantity"))
            if qty is None:
                trade = _flex_trade_row(norm)
                if trade is not None:
                    trade_lots.append(trade)
                continue

            if _flex_is_option(asset_class, norm):
                contract = _flex_option(norm, symbol, qty, warnings)
                if contract is not None:
                    options.append(contract)
            elif symbol:
                underlyings.append(
                    UnderlyingPosition(
                        symbol=symbol,
                        quantity=qty,
                        average_cost=_num(norm.get("costbasisprice") or norm.get("costprice")),
                        market_value=_num(norm.get("positionvalue") or norm.get("value")),
                    )
                )
            else:
                warnings.append(
                    ParseWarning(code="empty_symbol", message="Row has no symbol; skipped.", context="flex_row")
                )

        snapshot = PortfolioSnapshot(
            account_label=account_label,
            as_of=as_of,
            base_currency=base_currency,
            cash=0.0,
            buying_power=None,
            underlyings=tuple(underlyings),
            options=tuple(options),
            trade_lots=tuple(trade_lots),
            source_label=self.format_label,
        )
        return ParseResult(snapshot=snapshot, warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# Activity Statement helpers
# ---------------------------------------------------------------------------


def _read_sections(text: str) -> dict[str, list[dict[str, str]]]:
    """Group Activity-Statement data rows by section into header-keyed dicts."""
    sections: dict[str, list[dict[str, str]]] = {}
    headers: dict[str, list[str]] = {}
    reader = csv.reader(io.StringIO(text))
    for raw in reader:
        if len(raw) < 2:
            continue
        section, kind = _SECTION_ALIASES.get(raw[0], raw[0]), raw[1]
        cols = raw[2:]
        if kind == "Header":
            headers[section] = [_COLUMN_ALIASES.get(column, column) for column in cols]
        elif kind == "Data":
            header = headers.get(section)
            if not header:
                continue
            row: dict[str, str] = {}
            for index, column in enumerate(header):
                value = cols[index] if index < len(cols) else ""
                # Localized IBKR files can emit duplicate "Code" columns. The
                # first populated value is the actual instrument identity.
                if column not in row or (not row[column].strip() and value.strip()):
                    row[column] = value
            sections.setdefault(section, []).append(row)
    return sections


def _build_instrument_map(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Map an option Symbol to its structured instrument fields."""
    mapping: dict[str, dict[str, str]] = {}
    for row in rows:
        symbol = (row.get("Symbol", "") or "").strip().upper()
        description = (row.get("Description", "") or "").strip().upper()
        if symbol:
            mapping[symbol] = row
        if description:
            mapping.setdefault(description, row)
    return mapping


def _resolve_option(
    symbol: str,
    description: str,
    instrument: Optional[dict[str, str]],
) -> Optional[tuple[str, OptionRight, float, date, int]]:
    """Resolve (underlying, right, strike, expiry, multiplier) or ``None``.

    Resolution order: structured instrument table → OCC symbol → human
    description. Never guesses; returns ``None`` if all sources fail so the
    caller can emit a structured warning.
    """
    if instrument is not None:
        underlying = (instrument.get("Underlying") or "").strip().upper()
        strike = _num(instrument.get("Strike"))
        expiry = parse_expiry(instrument.get("Expiry", ""))
        type_raw = (instrument.get("Type") or instrument.get("Put/Call") or "").strip().upper()
        mult = _num(instrument.get("Multiplier"))
        right: Optional[OptionRight] = None
        if type_raw.startswith("C"):
            right = OptionRight.CALL
        elif type_raw.startswith("P"):
            right = OptionRight.PUT
        if underlying and strike is not None and expiry is not None and right is not None:
            return underlying, right, strike, expiry, int(mult or 100)

    parsed = parse_occ_symbol(symbol) or parse_option_description(description or symbol)
    if parsed is not None:
        return parsed.underlying, parsed.right, parsed.strike, parsed.expiry, 100
    return None


def _parse_open_positions(
    rows: list[dict[str, str]],
    instrument_map: dict[str, dict[str, str]],
    warnings: list[ParseWarning],
) -> tuple[list[UnderlyingPosition], list[OptionContract]]:
    underlyings: list[UnderlyingPosition] = []
    options: list[OptionContract] = []
    for row in rows:
        discriminator = (row.get("DataDiscriminator") or "").strip().lower()
        if discriminator and discriminator != "summary":
            continue
        category = row.get("Asset Category", "") or row.get("Asset Class", "")
        symbol = (row.get("Symbol", "") or "").strip().upper()
        qty = _num(row.get("Quantity"))
        if not symbol or qty is None:
            continue
        cost_price = _num(row.get("Cost Price"))
        value = _num(row.get("Value") or row.get("Position Value"))

        if _is_option_category(category):
            resolved = _resolve_option(symbol, row.get("Description", ""), instrument_map.get(symbol))
            if resolved is None:
                warnings.append(
                    ParseWarning(
                        code="unresolved_option",
                        message="Could not resolve option strike/expiry/right; position skipped (not guessed).",
                        context=symbol,
                    )
                )
                continue
            underlying, right, strike, expiry, mult = resolved
            mult = int(_num(row.get("Mult")) or mult or 100)
            options.append(
                OptionContract(
                    underlying=underlying,
                    right=right,
                    strike=strike,
                    expiry=expiry,
                    multiplier=mult,
                    quantity=qty,
                    average_cost=cost_price,
                    market_value=value,
                    occ_symbol=symbol if parse_occ_symbol(symbol) else None,
                )
            )
        elif _is_stock_category(category):
            underlyings.append(
                UnderlyingPosition(
                    symbol=symbol,
                    quantity=qty,
                    average_cost=cost_price,
                    market_value=value,
                )
            )
        else:
            warnings.append(
                ParseWarning(
                    code="unsupported_asset_category",
                    message=f"Asset category {category!r} is not supported in Phase 1; position skipped.",
                    context=symbol,
                )
            )
    return underlyings, options


def _parse_trades(
    rows: list[dict[str, str]],
    instrument_map: dict[str, dict[str, str]],
    warnings: list[ParseWarning],
) -> list[TradeLot]:
    lots: list[TradeLot] = []
    for row in rows:
        discriminator = (row.get("DataDiscriminator") or "").strip().lower()
        if discriminator and discriminator not in {"order", "trade", "execution"}:
            continue
        category = row.get("Asset Category", "") or row.get("Asset Class", "")
        symbol = (row.get("Symbol", "") or "").strip().upper()
        qty = _num(row.get("Quantity"))
        price = _num(row.get("T. Price") or row.get("TradePrice"))
        if not symbol or qty is None or price is None:
            continue
        trade_dt = _parse_datetime_cell(row.get("Date/Time") or row.get("Date", ""))
        fees = _num(row.get("Comm/Fee") or row.get("Comm in USD"))
        fee_abs = abs(fees) if fees is not None else 0.0

        if _is_option_category(category):
            resolved = _resolve_option(symbol, row.get("Description", ""), instrument_map.get(symbol))
            if resolved is None:
                # Preserve the fill in the blotter (with no fabricated contract
                # identity) and flag it. It is not used for risk math.
                warnings.append(
                    ParseWarning(
                        code="unresolved_option_trade",
                        message="Option trade kept in blotter without contract identity (strike/expiry/right unknown).",
                        context=symbol,
                    )
                )
                lots.append(
                    TradeLot(
                        symbol=symbol,
                        asset_kind="option",
                        trade_date=trade_dt,
                        quantity=qty,
                        price=price,
                        fees=fee_abs,
                    )
                )
                continue
            underlying, right, strike, expiry, _mult = resolved
            lots.append(
                TradeLot(
                    symbol=underlying,
                    asset_kind="option",
                    trade_date=trade_dt,
                    quantity=qty,
                    price=price,
                    fees=fee_abs,
                    right=right,
                    strike=strike,
                    expiry=expiry,
                )
            )
        else:
            lots.append(
                TradeLot(
                    symbol=symbol,
                    asset_kind="stock",
                    trade_date=trade_dt,
                    quantity=qty,
                    price=price,
                    fees=fee_abs,
                )
            )
    return lots


def _parse_ending_cash(rows: list[dict[str, str]], base_currency: str) -> Optional[float]:
    """Extract ending cash in the base currency from the Cash Report section."""
    for row in rows:
        first_val = next(iter(row.values()), "")
        candidate_label = (first_val or "").strip().lower()
        currency = (row.get("Currency", "") or "").strip().upper()
        is_ending_cash = "ending cash" in candidate_label or "期末现金" in candidate_label
        is_base_summary = currency in {base_currency.upper(), "BASE_SUMMARY", "基础货币总结", ""}
        if is_ending_cash and is_base_summary:
            total = _num(row.get("Total") or row.get("Ending Cash"))
            if total is not None:
                return total
    return None


def _parse_net_asset_cash(rows: list[dict[str, str]]) -> Optional[float]:
    """Fallback to the base-currency cash total in Net Asset Value."""
    for row in rows:
        category = (row.get("Asset Category", "") or "").strip().lower()
        if category == "cash" or category == "现金":
            amount = _num(row.get("Current Total") or row.get("Current Long") or row.get("Total"))
            if amount is not None:
                return amount
    return None


def _detect_currency(sections: dict[str, list[dict[str, str]]]) -> str:
    for row in sections.get("Account Information", []):
        field_name = (row.get("Field Name", "") or "").strip().lower()
        if field_name in {"base currency", "基础货币"}:
            currency = (row.get("Field Value", "") or "").strip().upper()
            if currency:
                return currency
    for row in sections.get("Open Positions", []):
        currency = (row.get("Currency", "") or "").strip().upper()
        if currency:
            return currency
    return "USD"


def _detect_as_of(sections: dict[str, list[dict[str, str]]]) -> date:
    for row in sections.get("Statement", []):
        if (row.get("Field Name", "") or "").strip().lower() in {"period", "when generated"}:
            field_value = row.get("Field Value", "") or ""
            parsed = _parse_statement_date(field_value)
            if parsed is not None:
                return parsed
    return date.today()


def _parse_statement_date(raw: str) -> Optional[date]:
    """Parse ISO/English dates plus localized IBKR period labels."""
    text = (raw or "").strip()
    parsed = parse_expiry(text)
    if parsed is not None:
        return parsed
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        return parse_expiry(iso_match.group(1))
    chinese_match = re.search(r"(一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|十一月|十二月)\s*(\d{1,2})\s*,\s*(\d{4})", text)
    if chinese_match:
        month, day, year = chinese_match.groups()
        return date(int(year), _CHINESE_MONTHS[month], int(day))
    return None


def _parse_datetime_cell(raw: str) -> date:
    text = (raw or "").strip().strip('"')
    if not text:
        return date.today()
    datepart = text.split(",")[0].split(" ")[0]
    parsed = parse_expiry(datepart)
    return parsed if parsed is not None else date.today()


# ---------------------------------------------------------------------------
# Flex Query helpers
# ---------------------------------------------------------------------------


def _norm_key(key: str) -> str:
    return "".join(ch for ch in (key or "").lower() if ch.isalnum())


def _flex_is_option(asset_class: str, norm: dict[str, str]) -> bool:
    cat = (asset_class or "").lower()
    if cat in {"opt", "fop"} or "option" in cat:
        return True
    return bool(norm.get("putcall") or norm.get("strike"))


def _flex_option(
    norm: dict[str, str],
    symbol: str,
    qty: float,
    warnings: list[ParseWarning],
) -> Optional[OptionContract]:
    underlying = (norm.get("underlyingsymbol") or "").upper()
    strike = _num(norm.get("strike"))
    expiry = parse_expiry(norm.get("expiry", "") or norm.get("expirydate", ""))
    pc = (norm.get("putcall") or "").upper()
    right = OptionRight.CALL if pc.startswith("C") else OptionRight.PUT if pc.startswith("P") else None

    if not (underlying and strike is not None and expiry is not None and right is not None):
        parsed = parse_occ_symbol(symbol) or parse_option_description(norm.get("description", ""))
        if parsed is None:
            warnings.append(
                ParseWarning(
                    code="unresolved_option",
                    message="Flex option row missing strike/expiry/right and unresolvable; skipped (not guessed).",
                    context=symbol or underlying,
                )
            )
            return None
        underlying, right, strike, expiry = parsed.underlying, parsed.right, parsed.strike, parsed.expiry

    mult = _num(norm.get("multiplier")) or 100
    return OptionContract(
        underlying=underlying,
        right=right,
        strike=strike,
        expiry=expiry,
        multiplier=int(mult),
        quantity=qty,
        average_cost=_num(norm.get("costbasisprice") or norm.get("costprice")),
        market_value=_num(norm.get("positionvalue") or norm.get("value")),
        occ_symbol=symbol if parse_occ_symbol(symbol) else None,
    )


def _flex_trade_row(norm: dict[str, str]) -> Optional[TradeLot]:
    symbol = (norm.get("symbol") or norm.get("underlyingsymbol") or "").upper()
    qty = _num(norm.get("quantity"))
    price = _num(norm.get("tradeprice") or norm.get("price"))
    if not symbol or qty is None or price is None:
        return None
    buysell = (norm.get("buysell") or "").upper()
    if buysell == "SELL" and qty > 0:
        qty = -qty
    trade_dt = parse_expiry(norm.get("tradedate", "") or norm.get("date", "")) or date.today()
    is_option = _flex_is_option(norm.get("assetclass", ""), norm)
    return TradeLot(
        symbol=symbol,
        asset_kind="option" if is_option else "stock",
        trade_date=trade_dt,
        quantity=qty,
        price=price,
        fees=abs(_num(norm.get("ibcommission") or norm.get("commission")) or 0.0),
    )


# Register both IBKR shapes. Activity is registered first: its can_parse is
# stricter (requires Header/Data), so a flat Flex CSV falls through to the Flex
# parser deterministically.
register_parser(IbkrActivityStatementParser())
register_parser(IbkrFlexQueryParser())
