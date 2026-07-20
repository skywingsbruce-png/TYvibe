"""Optional LLM interpretation layer for Market Notes.

**Default OFF.** Import, structuring, conclusions, and holdings linkage all work
with no LLM and no network. When explicitly enabled (``MARKET_NOTES_LLM_ENABLED``
truthy *and* a provider configured in the local ``.env``), the LLM may only
rephrase/synthesize the user's own notes — it must never compute prices, yields,
Greeks, or risk, and it must never emit a trade instruction.

Every LLM conclusion must cite real note ids; a conclusion with no valid citation
(or produced from invalid JSON) is **rejected** as a formal conclusion and, at
most, surfaced as "needs manual review". API keys come only from ``.env`` and are
never logged, tested against, embedded, or returned to the page.
"""

from __future__ import annotations

import json
import logging
import os

from src.market_notes.models import Citation, Conclusion, MarketNote

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on", "enabled"}

_SYSTEM_PROMPT = (
    "You are an opinion-audit summarizer. You ONLY rephrase and synthesize the "
    "provided trading-chat notes. You MUST NOT compute or invent any price, "
    "yield, return, Greek, implied volatility, or risk number, and you MUST NOT "
    "give any trade instruction (no buy/sell/add/trim). Return STRICT JSON: "
    '{"conclusions": [{"text": "...", "note_ids": ["n1234abcd", ...]}]}. Every '
    "conclusion MUST reference at least one note_id from the input. If opinions "
    "conflict, say they conflict; do not force one direction."
)


#: Privacy states surfaced in the API payload.
PRIVACY_OFFLINE = "offline"
PRIVACY_ENABLED_WITH_CONSENT = "enabled_with_remote_consent"
PRIVACY_ENABLED_MISSING_CONSENT = "enabled_missing_consent"


def _flag_on() -> bool:
    return os.getenv("MARKET_NOTES_LLM_ENABLED", "").strip().lower() in _TRUTHY


def _remote_consent() -> bool:
    return os.getenv("MARKET_NOTES_REMOTE_LLM_CONSENT", "").strip().lower() in _TRUTHY


def _provider_configured() -> bool:
    model = os.getenv("LANGCHAIN_MODEL_NAME", "") or os.getenv("OPENAI_MODEL", "")
    return bool(model.strip())


def llm_enabled() -> bool:
    """Return whether the LLM feature flag is on (independent of consent)."""
    return _flag_on()


def remote_llm_allowed() -> bool:
    """Return whether content may be sent to a remote LLM.

    Requires ALL of: the feature flag, explicit remote consent, and a configured
    provider/model. Missing any one keeps the feature fully offline.
    """
    return _flag_on() and _remote_consent() and _provider_configured()


def privacy_status() -> str:
    """Return the explicit LLM privacy state for the API/UI.

    * ``offline`` — the flag is off; notes are processed only on this machine.
    * ``enabled_with_remote_consent`` — flag + consent + provider all set;
      excerpts WILL be sent to the configured remote LLM.
    * ``enabled_missing_consent`` — flag on but consent (or provider) missing;
      the feature degrades to offline and sends nothing.
    """
    if not _flag_on():
        return PRIVACY_OFFLINE
    if _remote_consent() and _provider_configured():
        return PRIVACY_ENABLED_WITH_CONSENT
    return PRIVACY_ENABLED_MISSING_CONSENT


def validate_llm_conclusions(raw: str, notes_by_id: dict[str, MarketNote]) -> list[Conclusion]:
    """Validate a raw LLM response into cited conclusions.

    * Invalid JSON => no conclusions are accepted (returns ``[]``).
    * A conclusion citing >=1 real note id => accepted (``needs_review=False``).
    * A conclusion with no valid citation => kept only as ``needs_review=True``
      ("needs manual review"), never as a formal conclusion.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Market Notes LLM returned invalid JSON; rejecting all conclusions.")
        return []

    items = data.get("conclusions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    out: list[Conclusion] = []
    counter = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        raw_ids = item.get("note_ids")
        if not isinstance(raw_ids, list):
            raw_ids = [c.get("note_id") for c in item.get("citations", []) if isinstance(c, dict)]
        valid = [str(i) for i in raw_ids if str(i) in notes_by_id]
        counter += 1
        if valid:
            citations = tuple(
                Citation(
                    note_id=nid,
                    excerpt=notes_by_id[nid].excerpt[:240],
                    source_file=notes_by_id[nid].source_file,
                    source_ref=notes_by_id[nid].source_ref,
                )
                for nid in valid
            )
            out.append(
                Conclusion(
                    conclusion_id=f"l{counter}",
                    text=text,
                    origin="llm",
                    needs_review=False,
                    citations=citations,
                )
            )
        else:
            out.append(
                Conclusion(
                    conclusion_id=f"l{counter}",
                    text=text,
                    origin="llm",
                    needs_review=True,
                    citations=(),
                )
            )
    return out


def generate_llm_conclusions(notes: list[MarketNote]) -> list[Conclusion]:
    """Generate LLM conclusions when enabled; otherwise return ``[]``.

    Fully defensive: any provider/import/network error degrades to ``[]`` so the
    page keeps working on the deterministic path. Content is sent to a remote
    model ONLY when :func:`remote_llm_allowed` (flag + consent + provider).
    """
    # Hard gate: without explicit remote consent, never touch build_llm/invoke.
    if not remote_llm_allowed() or not notes:
        return []
    try:
        from src.providers.llm import build_llm
    except Exception:  # noqa: BLE001 - no provider stack available
        return []

    notes_by_id = {n.note_id: n for n in notes}
    payload = [{"note_id": n.note_id, "excerpt": n.excerpt[:240]} for n in notes]
    user_prompt = (
        "Notes (JSON): "
        + json.dumps(payload, ensure_ascii=False)
        + "\nReturn the JSON object described in the system instructions."
    )
    try:
        llm = build_llm()
        response = llm.invoke([("system", _SYSTEM_PROMPT), ("user", user_prompt)])
        raw = getattr(response, "content", "") if response is not None else ""
        if isinstance(raw, list):  # some providers return content parts
            raw = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in raw)
    except Exception:  # noqa: BLE001 - never let the LLM path break the page
        # Deliberately NO exception detail / provider message / raw notes in the
        # log — privacy first. Just note the fallback occurred.
        logger.warning("Market Notes LLM call failed; using deterministic conclusions only.")
        return []
    return validate_llm_conclusions(str(raw), notes_by_id)
