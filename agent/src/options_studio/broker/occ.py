"""OCC option-symbol and IBKR option-description parsing helpers.

These functions are deterministic string parsers. They return ``None`` on any
input they cannot confidently parse — they never guess a strike, expiry, or
right. Ambiguity must surface upstream as an unparsed contract, not a fabricated
one.
"""

from __future__ import annotations

import re
from datetime import date
from typing import NamedTuple

from src.options_studio.models import OptionRight


class ParsedOption(NamedTuple):
    """Structured option identity extracted from a symbol or description."""

    underlying: str
    right: OptionRight
    strike: float
    expiry: date


# Canonical 21-char OCC symbol: 6-char root (space-padded), YYMMDD, C/P, then
# 8-digit strike in thousandths. We also accept the common compact IBKR form
# with a single space and no padding: ``AAPL 240119C00190000``.
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z][A-Z0-9.\-]{0,5})\s*"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])"
    r"(?P<strike>\d{8})$"
)

# Human IBKR description, e.g. ``AAPL 19JAN24 190 C`` or ``AAPL 16JAN26 200.0 P``.
_DESC_RE = re.compile(
    r"^(?P<root>[A-Z][A-Z0-9.\-]{0,5})\s+"
    r"(?P<day>\d{1,2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})\s+"
    r"(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<cp>[CP])(?:ALL|UT)?$"
)

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _right_from_cp(cp: str) -> OptionRight:
    return OptionRight.CALL if cp.upper() == "C" else OptionRight.PUT


def parse_occ_symbol(symbol: str) -> ParsedOption | None:
    """Parse a canonical or compact OCC option symbol.

    Returns ``None`` when ``symbol`` is not an OCC option symbol.
    """
    if not symbol:
        return None
    compact = symbol.strip().upper().replace(" ", "")
    match = _OCC_RE.match(compact)
    if not match:
        return None
    try:
        expiry = date(2000 + int(match["yy"]), int(match["mm"]), int(match["dd"]))
    except ValueError:
        return None
    strike = int(match["strike"]) / 1000.0
    return ParsedOption(
        underlying=match["root"],
        right=_right_from_cp(match["cp"]),
        strike=strike,
        expiry=expiry,
    )


def parse_option_description(description: str) -> ParsedOption | None:
    """Parse an IBKR human option description like ``AAPL 19JAN24 190 C``.

    Returns ``None`` when the description is not an option description.
    """
    if not description:
        return None
    text = " ".join(description.strip().upper().split())
    match = _DESC_RE.match(text)
    if not match:
        return None
    mon = _MONTHS.get(match["mon"])
    if mon is None:
        return None
    try:
        expiry = date(2000 + int(match["yy"]), mon, int(match["day"]))
    except ValueError:
        return None
    return ParsedOption(
        underlying=match["root"],
        right=_right_from_cp(match["cp"]),
        strike=float(match["strike"]),
        expiry=expiry,
    )


def parse_expiry(raw: str) -> date | None:
    """Parse an IBKR expiry cell (``YYYYMMDD``, ``YYYY-MM-DD``, ``DDMONYY``)."""
    if not raw:
        return None
    text = raw.strip().upper()
    # YYYYMMDD
    if re.fullmatch(r"\d{8}", text):
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    # YYYY-MM-DD or YYYY/MM/DD
    m = re.fullmatch(r"(\d{4})[-/](\d{2})[-/](\d{2})", text)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    # DDMONYY (e.g. 19JAN24)
    m = re.fullmatch(r"(\d{1,2})([A-Z]{3})(\d{2})", text)
    if m:
        mon = _MONTHS.get(m[2])
        if mon is None:
            return None
        try:
            return date(2000 + int(m[3]), mon, int(m[1]))
        except ValueError:
            return None
    return None
