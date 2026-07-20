"""Telegram importer: JSON export or a plain-text chat paste.

* JSON: Telegram Desktop export (``messages`` entries with ``from`` + ``text``).
* Text: ``[optional time] Author: message`` lines, with wrapped continuation
  lines folded into the previous message.
"""

from __future__ import annotations

import json
import re

from src.market_notes.importers.base import NoteImporter
from src.market_notes.models import RawMessage

# "[2026-07-18 10:00] Alice: text"  or  "Alice: text"
_LINE_RE = re.compile(r"^\s*(?:\[(?P<ts>[^\]]+)\]\s*)?(?P<author>[^:\n]{1,40}?):\s+(?P<text>.+?)\s*$")


def _flatten_text(value: object) -> str:
    """Telegram ``text`` may be a string or a list of string/entity parts."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


class TelegramImporter(NoteImporter):
    """Importer for Telegram JSON exports and plain-text chat pastes."""

    kind = "telegram"

    def can_import(self, filename: str, text: str) -> bool:
        lower = filename.lower()
        if lower.endswith(".json"):
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return False
            messages = data.get("messages") if isinstance(data, dict) else None
            if not isinstance(messages, list) or not messages:
                return False
            first = messages[0]
            # Telegram entries have a scalar ``from`` (not a Discord author dict).
            return isinstance(first, dict) and ("from" in first or "from_id" in first)
        if lower.endswith(".txt"):
            for line in text.splitlines():
                if line.strip():
                    return _LINE_RE.match(line) is not None
        return False

    def import_messages(self, text: str, source_file: str) -> list[RawMessage]:
        if source_file.lower().endswith(".json"):
            return self._import_json(text, source_file)
        return self._import_text(text, source_file)

    def _import_json(self, text: str, source_file: str) -> list[RawMessage]:
        data = json.loads(text)
        out: list[RawMessage] = []
        for msg in data.get("messages", []):
            if not isinstance(msg, dict):
                continue
            body = _flatten_text(msg.get("text")).strip()
            if not body:
                continue
            author = msg.get("from")
            out.append(
                RawMessage(
                    author=str(author) if author else None,
                    timestamp=str(msg.get("date")) if msg.get("date") else None,
                    text=body,
                    source_file=source_file,
                    source_ref=f"telegram:id={msg.get('id', '?')}",
                )
            )
        return out

    def _import_text(self, text: str, source_file: str) -> list[RawMessage]:
        out: list[RawMessage] = []
        current: dict | None = None
        start_line = 0
        for i, raw in enumerate(text.splitlines(), start=1):
            match = _LINE_RE.match(raw)
            if match:
                if current is not None:
                    out.append(self._finalize(current, source_file, start_line))
                current = {
                    "author": match["author"].strip() or None,
                    "timestamp": (match["ts"].strip() if match["ts"] else None),
                    "text": match["text"].strip(),
                }
                start_line = i
            elif current is not None and raw.strip():
                current["text"] += " " + raw.strip()
        if current is not None:
            out.append(self._finalize(current, source_file, start_line))
        return out

    @staticmethod
    def _finalize(current: dict, source_file: str, line: int) -> RawMessage:
        return RawMessage(
            author=current["author"],
            timestamp=current["timestamp"],
            text=current["text"],
            source_file=source_file,
            source_ref=f"line:{line}",
        )
