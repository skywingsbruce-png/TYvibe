"""Broker statement parser interface + registry.

Add a broker by subclassing :class:`BrokerStatementParser`, implementing
``can_parse`` and ``parse``, and registering it via :func:`register_parser`.
The registry drives auto-detection: :func:`get_parser_for` returns the first
registered parser whose ``can_parse`` accepts the raw text.

Parsers return a :class:`ParseResult`: a de-identified
:class:`~src.options_studio.models.PortfolioSnapshot` plus a list of structured
:class:`ParseWarning` objects. **Data a parser cannot understand is reported as
a warning and skipped — never guessed.** A :class:`ParseError` is reserved for
"this is not my format / there is nothing to parse", not for individual bad
rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.options_studio.models import PortfolioSnapshot


class ParseError(ValueError):
    """Raised when a statement cannot be parsed into a snapshot at all.

    Carries a human-readable, path-free message. Parsers must never embed a
    filesystem path or the raw account number in the message.
    """


@dataclass(frozen=True)
class ParseWarning:
    """A structured, path-free warning about data that was skipped.

    Attributes:
        code: Stable machine code (e.g. ``"unresolved_option"``,
            ``"unsupported_section"``, ``"bad_number"``).
        message: Human-readable explanation.
        context: Short, non-sensitive locator (e.g. a symbol or section name).
            Never a filesystem path or account number.
    """

    code: str
    message: str
    context: str = ""

    def to_dict(self) -> dict[str, str]:
        """Serialize to a JSON-safe dict."""
        return {"code": self.code, "message": self.message, "context": self.context}


@dataclass(frozen=True)
class ParseResult:
    """A parse outcome: the snapshot plus any structured warnings."""

    snapshot: PortfolioSnapshot
    warnings: tuple[ParseWarning, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
        return {
            "snapshot": self.snapshot.to_dict(),
            "warnings": [w.to_dict() for w in self.warnings],
        }


class BrokerStatementParser(ABC):
    """Abstract adapter turning one broker's export into a parse result.

    Attributes:
        format_label: Stable, path-free label of the format this parser handles
            (e.g. ``"ibkr_activity_csv"``). Surfaced as
            :attr:`PortfolioSnapshot.source_label`.
    """

    format_label: str = ""

    @abstractmethod
    def can_parse(self, text: str) -> bool:
        """Return whether this parser recognizes ``text`` as its format.

        Must be cheap and side-effect free (typically a header/marker sniff).
        """

    @abstractmethod
    def parse(self, text: str, *, account_label: str = "acct-1") -> ParseResult:
        """Parse raw statement text into a de-identified result.

        Args:
            text: Raw CSV/statement content.
            account_label: Opaque anonymized account label to stamp on the
                snapshot. The real broker account number MUST NOT be surfaced on
                the returned snapshot.

        Returns:
            A :class:`ParseResult` (snapshot + structured warnings).

        Raises:
            ParseError: If the text is not valid for this format at all.
        """


_REGISTRY: list[BrokerStatementParser] = []


def register_parser(parser: BrokerStatementParser) -> None:
    """Register a parser instance for auto-detection (idempotent by type)."""
    if any(type(p) is type(parser) for p in _REGISTRY):
        return
    _REGISTRY.append(parser)


def list_parsers() -> list[BrokerStatementParser]:
    """Return the registered parsers in registration order."""
    return list(_REGISTRY)


def strip_bom(text: str) -> str:
    """Remove a leading UTF-8 BOM.

    IBKR (and Excel round-trips) can prepend a BOM. Left in place it would
    corrupt the first CSV header cell and silently drop that section — the exact
    "quietly incomplete" failure this project must never produce.
    """
    return text.lstrip("﻿")


def get_parser_for(text: str) -> BrokerStatementParser:
    """Return the first registered parser that recognizes ``text``.

    Raises:
        ParseError: If no registered parser accepts the text.
    """
    text = strip_bom(text)
    for parser in _REGISTRY:
        try:
            if parser.can_parse(text):
                return parser
        except Exception:  # noqa: BLE001 - a broken sniffer must not block others
            continue
    raise ParseError(
        "Unrecognized statement format. Supported: "
        + ", ".join(p.format_label for p in _REGISTRY)
        + "."
    )


def parse_statement(text: str, *, account_label: str = "acct-1") -> ParseResult:
    """Auto-detect the format and parse ``text`` into a :class:`ParseResult`."""
    text = strip_bom(text)
    return get_parser_for(text).parse(text, account_label=account_label)
