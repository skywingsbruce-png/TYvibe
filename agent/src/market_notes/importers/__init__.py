"""Note importers: one adapter per source format.

Add a source by subclassing :class:`NoteImporter`. Detection is by file
extension plus a cheap content sniff. All importers yield
:class:`~src.market_notes.models.RawMessage` objects with ``None`` for any field
the source does not provide — never a guessed value.
"""

from __future__ import annotations

from src.market_notes.importers.base import ImportError_, import_file, list_importers

__all__ = ["ImportError_", "import_file", "list_importers"]
