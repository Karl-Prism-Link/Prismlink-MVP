from __future__ import annotations

import re


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


_CANONICAL_ALIASES: dict[str, set[str]] = {
    "Men's Cut": {
        "mens cut",
        "mens cuts",
        "men cut",
        "mens haircut",
        "men haircut",
        "means cut",
        "means haircut",
        "means",
        "mens",
    },
    "Women's Cut": {
        "womens cut",
        "womens cuts",
        "woman cut",
        "womans cut",
        "womens haircut",
        "woman haircut",
    },
    "Blow Wave": {
        "blow wave",
        "blowwave",
        "blow dry",
        "blowdry",
    },
    "Colour": {
        "colour",
        "color",
        "hair colour",
        "hair color",
    },
}

# Include the canonical labels themselves in their own alias sets.
for _canonical, _aliases in _CANONICAL_ALIASES.items():
    _aliases.add(_key(_canonical))


def canonical_service_name(value: str) -> str:
    """Return a conservative canonical label for common salon service variants.

    Unknown/custom service names are preserved (apart from whitespace trimming), so
    salon-specific branding is never rewritten by a generic title-casing rule.
    """
    cleaned = " ".join(value.split()).strip()
    target = _key(cleaned)
    for canonical, aliases in _CANONICAL_ALIASES.items():
        if target in aliases:
            return canonical
    return cleaned


def service_match_terms(value: str) -> set[str]:
    """Normalized terms accepted when matching a configured service by voice."""
    canonical = canonical_service_name(value)
    terms = {_key(value), _key(canonical)}
    aliases = _CANONICAL_ALIASES.get(canonical)
    if aliases:
        terms.update(aliases)
    return {term for term in terms if term}


def service_name_from_speech(text: str) -> str | None:
    """Return one unambiguous canonical service alias mentioned in caller speech.

    This is intentionally conservative and only matches published canonical aliases.
    Generic words such as ``cut`` are not aliases, so they remain ambiguous.
    """
    target = _key(text)
    if not target:
        return None
    padded = f" {target} "
    matches: list[str] = []
    for canonical, aliases in _CANONICAL_ALIASES.items():
        for alias in aliases:
            if f" {alias} " in padded:
                matches.append(canonical)
                break
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


_STT_CONFUSION_ALIASES: dict[str, set[str]] = {
    "Men's Cut": {
        "beans cut",
        "bean's cut",
        "bean cut",
        "meme's cut",
        "memes cut",
        "meme cut",
        "mean cut",
        "mean cat",
        "men's cat",
        "mens cat",
        "mean tea cup",
        "mean tea cut",
        "means he cant",
        "means he can't",
    },
}


def configured_service_name_from_speech(
    text: str, configured_names: list[str] | tuple[str, ...]
) -> str | None:
    """Resolve one configured service from noisy caller speech.

    Exact configured service wording wins first, including custom salon services. Only
    after that do we apply a small set of known STT confusions, and only when the
    corresponding canonical service is actually configured for this tenant.
    """
    target = _key(text)
    if not target:
        return None
    padded = f" {target} "
    configured = [
        (name, _key(name), canonical_service_name(name))
        for name in configured_names
        if str(name).strip()
    ]

    direct = [name for name, key, _canonical in configured if key and f" {key} " in padded]
    direct_unique = list(dict.fromkeys(direct))
    if len(direct_unique) == 1:
        return direct_unique[0]

    alias_matches: list[str] = []
    for name, _key_name, canonical in configured:
        terms = set(service_match_terms(name))
        terms.update(_key(term) for term in _STT_CONFUSION_ALIASES.get(canonical, set()))
        if any(f" {term} " in padded for term in terms if term):
            alias_matches.append(name)
    unique = list(dict.fromkeys(alias_matches))
    return unique[0] if len(unique) == 1 else None
