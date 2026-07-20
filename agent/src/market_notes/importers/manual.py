"""Manual-excerpt importer (fallback for .md / .txt).

Blocks are separated by a ``---`` line (or by blank lines when no ``---`` is
present). Optional leading ``key: value`` header lines (``author``, ``time`` /
``date``) become metadata; everything else is the excerpt. Absent headers stay
``None`` — never guessed.
"""

from __future__ import annotations

import re

from src.market_notes.importers.base import NoteImporter
from src.market_notes.models import RawMessage

_HEADER_RE = re.compile(r"^\s*(?P<key>author|time|date)\s*:\s*(?P<val>.+?)\s*$", re.IGNORECASE)


class ManualNotesImporter(NoteImporter):
    """Importer for hand-written note files."""

    kind = "manual"

    def can_import(self, filename: str, text: str) -> bool:
        lower = filename.lower()
        return lower.endswith(".md") or lower.endswith(".txt")

    def import_messages(self, text: str, source_file: str) -> list[RawMessage]:
        blocks = self._split_blocks(text)
        out: list[RawMessage] = []
        for idx, block in enumerate(blocks, start=1):
            author, timestamp, body = self._parse_block(block)
            if not body.strip():
                continue
            out.append(
                RawMessage(
                    author=author,
                    timestamp=timestamp,
                    text=body.strip(),
                    source_file=source_file,
                    source_ref=f"block:{idx}",
                )
            )
        return out

    @staticmethod
    def _split_blocks(text: str) -> list[str]:
        if re.search(r"^\s*---\s*$", text, re.MULTILINE):
            return re.split(r"^\s*---\s*$", text, flags=re.MULTILINE)
        # No explicit separators -> split on blank-line paragraph breaks.
        return re.split(r"\n\s*\n", text)

    @staticmethod
    def _parse_block(block: str) -> tuple[str | None, str | None, str]:
        author: str | None = None
        timestamp: str | None = None
        body_lines: list[str] = []
        header_zone = True
        for line in block.splitlines():
            match = _HEADER_RE.match(line) if header_zone else None
            if match:
                key = match["key"].lower()
                if key == "author":
                    author = match["val"]
                else:  # time / date
                    timestamp = match["val"]
                continue
            if line.strip() == "" and header_zone and not body_lines:
                continue  # allow blank line between headers and body
            header_zone = False
            body_lines.append(line)
        return author, timestamp, "\n".join(body_lines)
