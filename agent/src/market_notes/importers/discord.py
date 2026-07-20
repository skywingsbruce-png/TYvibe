"""DiscordChatExporter JSON importer.

Handles the JSON produced by DiscordChatExporter: a top-level object with a
``messages`` array whose entries carry ``author`` (object), ``content``, ``id``,
and ``timestamp``.
"""

from __future__ import annotations

import json

from src.market_notes.importers.base import NoteImporter
from src.market_notes.models import RawMessage


class DiscordJsonImporter(NoteImporter):
    """Importer for DiscordChatExporter JSON exports."""

    kind = "discord_json"

    def can_import(self, filename: str, text: str) -> bool:
        if not filename.lower().endswith(".json"):
            return False
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return False
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list) or not messages:
            return False
        first = messages[0]
        # Discord entries carry an author OBJECT and a content string.
        return isinstance(first, dict) and isinstance(first.get("author"), dict) and "content" in first

    def import_messages(self, text: str, source_file: str) -> list[RawMessage]:
        data = json.loads(text)
        out: list[RawMessage] = []
        for msg in data.get("messages", []):
            if not isinstance(msg, dict):
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue  # skip empty / attachment-only messages
            author_obj = msg.get("author") or {}
            author = author_obj.get("nickname") or author_obj.get("name") or None
            timestamp = msg.get("timestamp") or None
            msg_id = msg.get("id") or "?"
            out.append(
                RawMessage(
                    author=str(author) if author else None,
                    timestamp=str(timestamp) if timestamp else None,
                    text=content,
                    source_file=source_file,
                    source_ref=f"discord:msg={msg_id}",
                )
            )
        return out
