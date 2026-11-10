from __future__ import annotations

import re


_HEALTH_SIGNAL = re.compile(
    r"\b(?:cut\s+(?:my\s+)?(?:finger|hand|self)|injur(?:y|ed)|"
    r"bleed(?:ing)?|burn(?:ed|t)?\s+(?:myself|my\s+hand|my\s+skin)|"
    r"rash|infection|swollen|swelling|pain|sore|symptom|illness|sick|"
    r"medication|medicine|tablet|first aid|medical advice|what should i do)\b",
    re.IGNORECASE,
)
_SALON_INTENT = re.compile(
    r"\b(?:salon|appointment|book(?:ing)?|reschedul|cancel|haircut|hair|"
    r"colour|color|blow ?wave|opening hours|service|staff|message|callback|"
    r"call back|speak to|talk to)\b",
    re.IGNORECASE,
)
_IMMEDIATE_EMERGENCY = re.compile(
    r"\b(?:not breathing|can't breathe|cannot breathe|unconscious|"
    r"chest pain|severe bleeding|won't stop bleeding|cannot stop bleeding|"
    r"overdose|life threatening|life-threatening)\b",
    re.IGNORECASE,
)


def is_health_only_request(text: str | None) -> bool:
    """Identify a health-related caller turn that is not also a salon request."""
    value = str(text or "").strip()
    return bool(value and _HEALTH_SIGNAL.search(value) and not _SALON_INTENT.search(value))


def is_immediate_emergency_language(text: str | None) -> bool:
    """Recognise explicit emergency language without trying to triage symptoms."""
    return bool(_IMMEDIATE_EMERGENCY.search(str(text or "")))
