from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher


def _clean(value: str | None) -> str:
    return " ".join(str(value or "").strip().split())


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).casefold()).strip()


_ROUTING_PATTERNS = (
    re.compile(
        r"\b(?:speak|talk|chat)\s+(?:to|with)\s+([a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2})\b", re.I
    ),
    re.compile(
        r"\bput\s+me\s+(?:through|on)\s+to\s+([a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2})\b", re.I
    ),
    re.compile(
        r"\b(?:can\s+i\s+(?:get|have)|is)\s+([a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2})\s+(?:there|available)\b",
        re.I,
    ),
)

_TRAILING_POLITE = re.compile(r"\b(?:please|thanks|thank\s+you|now|today)\b.*$", re.I)
_MESSAGE_REQUEST_RE = re.compile(
    r"\b(?:leave|take|send|give)\s+(?:me\s+)?(?:a\s+)?message\b|"
    r"\b(?:leave|take|send)\s+(?:a\s+)?message\s+(?:for|to)\b|"
    r"\bcan\s+you\s+(?:leave|take)\s+(?:a\s+)?message\b|"
    r"\bi(?:'d|\s+would)\s+like\s+to\s+leave\s+(?:a\s+)?message\b",
    re.I,
)

_CALLBACK_REQUEST_RE = re.compile(
    r"\b(?:can|could|would)\s+you\s+(?:get|ask|have)\s+(?:them|the\s+salon|someone)\s+to\s+(?:call|ring|phone)\b|"
    r"\b(?:get|ask|have)\s+(?:them|the\s+salon|someone)\s+to\s+(?:call|ring|phone)\b|"
    r"\b(?:call|ring|phone)\s+me\s+back\b",
    re.I,
)

_MESSAGE_CORRECTION_PATTERNS = (
    re.compile(r"\b(?:the\s+)?message\s+(?:should\s+)?(?:just\s+)?say\s+(.+)$", re.I),
    re.compile(r"\bchange\s+(?:the\s+)?message\s+to\s+(.+)$", re.I),
    re.compile(r"\bmake\s+(?:the\s+)?message\s+(?:say\s+)?(.+)$", re.I),
)


def requested_person_from_speech(text: str | None) -> str | None:
    """Extract the person named in a direct call-routing request.

    This is intentionally conservative. It only extracts a target when the caller
    uses a routing phrase such as ``speak to Jo`` or ``put me through to Alex``.
    """

    value = _clean(text)
    if not value:
        return None
    for pattern in _ROUTING_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        candidate = _TRAILING_POLITE.sub("", match.group(1)).strip(" .,!?")
        words = candidate.split()
        if 1 <= len(words) <= 3:
            return " ".join(word.capitalize() for word in words)
    return None


def match_configured_staff_name(requested: str | None, staff_names: Iterable[str]) -> str | None:
    """Resolve a requested person to configured active staff without guessing."""

    target = _norm(requested)
    if not target:
        return None
    names = [str(name).strip() for name in staff_names if str(name).strip()]
    exact = [name for name in names if _norm(name) == target]
    if len(exact) == 1:
        return exact[0]

    # Allow a small ASR spelling wobble only when there is one clearly better staff
    # candidate. Never use fuzzy matching for very short targets such as "Jo".
    if len(target) < 4:
        return None
    ranked = sorted(
        ((SequenceMatcher(None, target, _norm(name)).ratio(), name) for name in names),
        reverse=True,
    )
    if not ranked:
        return None
    best_score, best_name = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score >= 0.88 and best_score - second_score >= 0.08:
        return best_name
    return None


def is_routing_request(text: str | None) -> bool:
    return requested_person_from_speech(text) is not None


def is_message_request(text: str | None) -> bool:
    return bool(_MESSAGE_REQUEST_RE.search(_clean(text)))


def is_callback_request(text: str | None) -> bool:
    """Detect a caller asking the salon to call/ring them back.

    This is distinct from a staff-routing request. In ``get them to ring Georgie``
    the named person is the callback contact, not the recipient of the message.
    """

    return bool(_CALLBACK_REQUEST_RE.search(_clean(text)))


def message_caller_name_from_speech(text: str | None) -> str | None:
    """Extract a callback/caller name without treating it as a staff recipient."""

    value = _clean(text)
    if not value:
        return None

    # ``Get them to ring Georgie at 021...`` / ``Call Georgie on 021...``
    match = re.search(
        r"\b(?:call|ring|phone)\s+([a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2})\s+(?:at|on)\b",
        value,
        re.I,
    )
    if match:
        candidate = _TRAILING_POLITE.sub("", match.group(1)).strip(" .,!?")
        if candidate.casefold() not in {"me", "them", "someone", "the salon"}:
            return " ".join(word.capitalize() for word in candidate.split())

    # Explicit correction/identification while a message is being prepared.
    match = re.search(
        r"\b(?:i\s+am|i'm|im|this\s+is)\s+([a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2})(?:[,.!?]|$)",
        value,
        re.I,
    )
    if match:
        candidate = match.group(1).strip(" .,!?")
        if candidate:
            return " ".join(word.capitalize() for word in candidate.split())
    return None


def callback_message_from_speech(text: str | None, *, caller_name: str | None = None) -> str | None:
    """Build a concise callback message from explicit caller wording.

    The function only normalises the caller's stated callback intent. It does not
    infer a staff recipient or invent an appointment reason.
    """

    value = _clean(text)
    if not value or not is_callback_request(value):
        return None

    match = re.search(
        r"\b(?:call|ring|phone)\s+([a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2})(?:\s+(?:at|on)\b|[,.!?]|$)",
        value,
        re.I,
    )
    if match:
        candidate = match.group(1).strip(" .,!?")
        if candidate.casefold() == "me":
            if caller_name:
                return f"Please call {caller_name} back."
            return "Please call me back."
        if candidate.casefold() not in {"them", "someone", "the salon"}:
            name = " ".join(word.capitalize() for word in candidate.split())
            return f"Please call {name} back."

    if caller_name:
        return f"Please call {caller_name} back."
    return "Please call me back."


def extract_message_correction(text: str | None) -> str | None:
    """Extract replacement message wording from an explicit correction."""

    value = _clean(text)
    if not value:
        return None
    for pattern in _MESSAGE_CORRECTION_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        replacement = match.group(1).strip(" .")
        if len(replacement) >= 2:
            return replacement[:500]
    return None


def message_target_from_speech(text: str | None, staff_names: Iterable[str]) -> str | None:
    """Return a configured staff target from ``message for Jo`` style requests."""

    value = _clean(text)
    if not value:
        return None
    match = re.search(
        r"\bmessage\s+(?:for|to)\s+([a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2})(?:\s+(?:saying|that)\b|[,.!?]|$)",
        value,
        re.I,
    )
    if not match:
        return None
    requested = " ".join(word.capitalize() for word in match.group(1).split())
    return match_configured_staff_name(requested, staff_names)


def extract_inline_message(text: str | None) -> str | None:
    """Extract message content only when the caller explicitly includes it inline."""

    value = _clean(text)
    if not value:
        return None

    # "Leave a message for Jo saying I'll be late."
    match = re.search(r"\b(?:saying|say|that)\s+(.+)$", value, re.I)
    if match and is_message_request(value):
        message = match.group(1).strip(" .")
        if len(message) >= 2:
            return message

    # "Tell Jo to call me back." / "Tell Jo I'm running late."
    match = re.search(r"\btell\s+[a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,2}\s+(.+)$", value, re.I)
    if match:
        message = match.group(1).strip(" .")
        if len(message) >= 2:
            return message
    return None


def routing_fallback_speech(target: str | None, *, matched_staff: bool) -> str:
    """Caller-facing fallback while direct transfer is not available."""

    if matched_staff and target:
        return f"I can't connect you directly right now, but I can take a message for {target}. Would you like to leave one?"
    if target:
        return f"I can't find {target} as a staff member here. I can still take a message for the salon. Would you like to leave one?"
    return "I can't connect a call directly right now, but I can take a message for the salon. Would you like to leave one?"
