from __future__ import annotations

import re
from collections.abc import Iterable

_APOSTROPHES = ("'", "’")


def _variants(term: str) -> list[str]:
    """Return conservative STT prompt variants for a configured salon term."""
    cleaned = re.sub(r"\s+", " ", term).strip()
    if not cleaned:
        return []

    variants = [cleaned]
    # Callers often say service names without an audible possessive/apostrophe.
    # Supplying both forms helps Deepgram Nova-3's keyterm prompting distinguish
    # phrases such as "men's cut" from acoustically similar general speech.
    no_apostrophe = cleaned
    for mark in _APOSTROPHES:
        no_apostrophe = no_apostrophe.replace(mark, "")
    if no_apostrophe != cleaned:
        variants.append(no_apostrophe)
    return variants


def build_stt_keyterms(
    salon_name: str,
    service_names: Iterable[str],
    staff_names: Iterable[str],
    *,
    max_terms: int = 50,
) -> list[str]:
    """Build a deduplicated, tenant-specific Deepgram keyterm list.

    Only configured salon vocabulary is included. This keeps recognition bias
    narrow and avoids globally forcing ordinary words into unrelated transcripts.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    for candidate in [salon_name, *service_names, *staff_names]:
        for variant in _variants(candidate):
            key = variant.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(variant)
            if len(ordered) >= max_terms:
                return ordered

    return ordered


def build_stt_keywords(
    salon_name: str,
    service_names: Iterable[str],
    staff_names: Iterable[str],
    *,
    boost: float = 1.2,
    max_keywords: int = 50,
) -> list[str]:
    """Build conservative Nova-2 keyword boosts from configured salon vocabulary.

    Deepgram's Nova-2 Keywords feature works best with individual words rather
    than phrases. Keep the boost deliberately low to reduce false positives.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    terms = build_stt_keyterms(salon_name, service_names, staff_names, max_terms=max_keywords)
    for term in terms:
        # Preserve apostrophes inside a token (e.g. Men's), but drop surrounding
        # punctuation and split multi-word service names into individual keywords.
        for token in re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", term):
            if len(token) < 3:
                continue
            key = token.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(f"{token}:{boost:g}")
            if len(ordered) >= max_keywords:
                return ordered

    return ordered


def stt_vocabulary_settings(
    model: str,
    salon_name: str,
    service_names: Iterable[str],
    staff_names: Iterable[str],
) -> tuple[str | None, list[str] | None, list[str] | None]:
    """Return (mode, keyterm, keywords) compatible with the Deepgram model.

    Nova-3 uses Keyterm Prompting. Nova-2/Nova-1/Enhanced/Base use Keywords.
    Unsupported/unknown model families receive no vocabulary hint rather than
    risking a rejected WebSocket handshake.
    """
    normalized = model.strip().casefold()
    services = list(service_names)
    staff = list(staff_names)

    if normalized.startswith("nova-3"):
        terms = build_stt_keyterms(salon_name, services, staff)
        return ("keyterm", terms or None, None)

    keyword_families = ("nova-2", "nova-1", "nova-general", "nova", "enhanced", "base")
    if normalized.startswith(keyword_families):
        keywords = build_stt_keywords(salon_name, services, staff)
        return ("keywords", None, keywords or None)

    return (None, None, None)
