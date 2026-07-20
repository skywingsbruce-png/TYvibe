"""Importer interface + registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.market_notes.models import RawMessage


class ImportError_(ValueError):
    """Raised when a file cannot be imported by any registered importer."""


class NoteImporter(ABC):
    """Abstract adapter from one source format to raw messages.

    Attributes:
        kind: Stable, path-free label (e.g. ``"discord_json"``).
    """

    kind: str = ""

    @abstractmethod
    def can_import(self, filename: str, text: str) -> bool:
        """Return whether this importer recognizes the file (cheap sniff)."""

    @abstractmethod
    def import_messages(self, text: str, source_file: str) -> list[RawMessage]:
        """Parse raw text into messages. Missing fields are ``None``, not guessed."""


_REGISTRY: list[NoteImporter] = []


def register_importer(importer: NoteImporter) -> None:
    """Register an importer (idempotent by type)."""
    if any(type(i) is type(importer) for i in _REGISTRY):
        return
    _REGISTRY.append(importer)


def list_importers() -> list[NoteImporter]:
    return list(_REGISTRY)


def import_file(path: Path) -> tuple[list[RawMessage], str]:
    """Import a single file; return ``(messages, kind)``.

    Raises:
        ImportError_: If no registered importer recognizes the file.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    filename = path.name
    for importer in _REGISTRY:
        try:
            if importer.can_import(filename, text):
                return importer.import_messages(text, filename), importer.kind
        except Exception:  # noqa: BLE001 - a broken sniff must not block others
            continue
    raise ImportError_(f"No importer recognized {filename!r}.")


# Register built-in importers (import for side effect).
def _register_builtins() -> None:
    from src.market_notes.importers.discord import DiscordJsonImporter
    from src.market_notes.importers.manual import ManualNotesImporter
    from src.market_notes.importers.telegram import TelegramImporter

    register_importer(DiscordJsonImporter())
    register_importer(TelegramImporter())
    register_importer(ManualNotesImporter())  # fallback for .txt/.md


_register_builtins()
