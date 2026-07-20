"""Deterministic conclusion + conflict building from structured notes.

Every conclusion cites the specific note(s) it draws from. Contradictory
opinions on a symbol surface as a :class:`Conflict` and a conflict-flavored
conclusion — never a forced single direction. No LLM is involved here.
"""

from __future__ import annotations

from collections import defaultdict

from src.market_notes.models import Citation, Conclusion, Conflict, Direction, MarketNote

_EXCERPT_CAP = 240


def _citation(note: MarketNote) -> Citation:
    return Citation(
        note_id=note.note_id,
        excerpt=note.excerpt[:_EXCERPT_CAP],
        source_file=note.source_file,
        source_ref=note.source_ref,
    )


def build_conflicts(notes: list[MarketNote]) -> list[Conflict]:
    """Return symbols carrying contradictory (bullish AND bearish) opinions."""
    by_symbol: dict[str, list[MarketNote]] = defaultdict(list)
    for note in notes:
        for sym in note.symbols:
            by_symbol[sym].append(note)

    conflicts: list[Conflict] = []
    for symbol in sorted(by_symbol):
        symbol_notes = by_symbol[symbol]
        dirs = {n.direction for n in symbol_notes}
        if Direction.BULLISH in dirs and Direction.BEARISH in dirs:
            conflicts.append(
                Conflict(
                    symbol=symbol,
                    directions=tuple(sorted(d.value for d in dirs if d != Direction.UNKNOWN)),
                    note_ids=tuple(n.note_id for n in symbol_notes),
                )
            )
    return conflicts


def build_conclusions(notes: list[MarketNote]) -> list[Conclusion]:
    """Build deterministic, always-cited conclusions from the notes."""
    by_symbol: dict[str, list[MarketNote]] = defaultdict(list)
    for note in notes:
        for sym in note.symbols:
            by_symbol[sym].append(note)

    conclusions: list[Conclusion] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"c{counter}"

    for symbol in sorted(by_symbol):
        symbol_notes = by_symbol[symbol]
        dirs = {n.direction for n in symbol_notes}
        cites = tuple(_citation(n) for n in symbol_notes)

        if Direction.BULLISH in dirs and Direction.BEARISH in dirs:
            conclusions.append(
                Conclusion(
                    conclusion_id=next_id(),
                    text=f"Opinions on {symbol} CONFLICT (bullish and bearish present) — not resolving to one direction.",
                    origin="deterministic",
                    needs_review=False,
                    citations=cites,
                )
            )
            continue

        stance = next((d for d in (Direction.BEARISH, Direction.BULLISH, Direction.NEUTRAL, Direction.CONDITIONAL) if d in dirs), None)
        if stance is not None:
            n = len(symbol_notes)
            conclusions.append(
                Conclusion(
                    conclusion_id=next_id(),
                    text=f"Short-term opinion on {symbol} leans {stance.value} across {n} note(s).",
                    origin="deterministic",
                    needs_review=False,
                    citations=cites,
                )
            )

    # Echo explicit invalidation conditions as their own cited conclusions.
    for note in notes:
        if note.invalidation_condition:
            sym = note.symbols[0] if note.symbols else "the underlying"
            conclusions.append(
                Conclusion(
                    conclusion_id=next_id(),
                    text=f"Stated invalidation for {sym}: \"{note.invalidation_condition}\" — until then the opposite move is not treated as confirmation.",
                    origin="deterministic",
                    needs_review=False,
                    citations=(_citation(note),),
                )
            )

    return conclusions
