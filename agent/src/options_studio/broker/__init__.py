"""Broker-statement parsing for the Options Risk Studio.

The :class:`~src.options_studio.broker.base.BrokerStatementParser` interface is
the extension point: one implementation per broker export format. IBKR is the
first (and, for Phase 1, only) implementation, covering a minimal-but-usable
subset of the Activity Statement CSV and Flex Query CSV shapes.

Parsing is strictly read-only and produces a de-identified
:class:`~src.options_studio.models.PortfolioSnapshot` plus structured warnings.
Raw files are the caller's responsibility to keep under ``data/private/`` (see
:mod:`src.options_studio.storage`); nothing here writes them anywhere or sends
them off the machine.
"""

from __future__ import annotations

# Importing the ibkr module registers its parsers as a side effect.
from src.options_studio.broker import ibkr as _ibkr  # noqa: F401
from src.options_studio.broker.base import (
    BrokerStatementParser,
    ParseError,
    ParseResult,
    ParseWarning,
    get_parser_for,
    list_parsers,
    parse_statement,
    register_parser,
)

__all__ = [
    "BrokerStatementParser",
    "ParseError",
    "ParseResult",
    "ParseWarning",
    "get_parser_for",
    "list_parsers",
    "parse_statement",
    "register_parser",
]
