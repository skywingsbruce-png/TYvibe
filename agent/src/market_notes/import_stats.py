"""De-identified import diagnostics for Market Notes.

Produces per-file and aggregate stats for a real import run WITHOUT leaking the
full filesystem path, account, author names, channel names, or message text.
Authors and channels are reduced to stable de-identified references
(``a-<hash>`` / ``c-<hash>``) with counts only.
"""

from __future__ import annotations

import hashlib
import json

from src.market_notes import storage as _storage
from src.market_notes.importers import import_file
from src.market_notes.importers.base import ImportError_
from src.market_notes.models import RawMessage
from src.market_notes.timeutil import parse_timestamp


def _deid(value: str | None, prefix: str) -> str | None:
    if not value:
        return None
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def _discord_raw(text: str) -> tuple[int, str | None]:
    """Return (raw_message_count, channel_name) for a Discord export."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return 0, None
    messages = data.get("messages") if isinstance(data, dict) else None
    raw = len(messages) if isinstance(messages, list) else 0
    channel = None
    ch = data.get("channel") if isinstance(data, dict) else None
    if isinstance(ch, dict):
        channel = ch.get("name")
    return raw, (str(channel) if channel else None)


def _file_stats(name: str, text: str, messages: list[RawMessage], kind: str) -> dict:
    structured = len(messages)
    parseable = sum(1 for m in messages if parse_timestamp(m.timestamp) is not None)

    if kind == "discord_json":
        raw, channel = _discord_raw(text)
    else:
        raw, channel = structured, None

    skipped = max(0, raw - structured)
    skip_reasons = {"empty_or_attachment_only": skipped} if skipped else {}

    return {
        "file": name,  # bare filename only (never a full path)
        "kind": kind,
        "raw_messages": raw,
        "structured_notes": structured,
        "parseable_timestamps": parseable,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "channel_ref": _deid(channel, "c"),
        "distinct_authors": len({m.author for m in messages if m.author}),
    }


def build_import_stats() -> dict:
    """Return de-identified per-file + aggregate import diagnostics."""
    files: list[dict] = []
    errors: list[dict[str, str]] = []
    author_counts: dict[str, int] = {}
    channel_refs: set[str] = set()
    totals = {"files": 0, "raw_messages": 0, "structured_notes": 0, "parseable_timestamps": 0, "skipped": 0}

    for path in _storage.list_source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            messages, kind = import_file(path)
        except ImportError_ as exc:
            errors.append({"file": path.name, "message": str(exc)})
            continue
        stats = _file_stats(path.name, text, messages, kind)
        files.append(stats)
        totals["files"] += 1
        totals["raw_messages"] += stats["raw_messages"]
        totals["structured_notes"] += stats["structured_notes"]
        totals["parseable_timestamps"] += stats["parseable_timestamps"]
        totals["skipped"] += stats["skipped"]
        if stats["channel_ref"]:
            channel_refs.add(stats["channel_ref"])
        for m in messages:
            if m.author:
                ref = _deid(m.author, "a")
                if ref:
                    author_counts[ref] = author_counts.get(ref, 0) + 1

    per_author = sorted(
        ({"author_ref": ref, "messages": count} for ref, count in author_counts.items()),
        key=lambda x: (-x["messages"], x["author_ref"]),
    )
    return {
        "files": files,
        "errors": errors,
        "totals": totals,
        "authors": {"distinct": len(author_counts), "per_author": per_author},
        "channels": {"distinct": len(channel_refs), "refs": sorted(channel_refs)},
    }
