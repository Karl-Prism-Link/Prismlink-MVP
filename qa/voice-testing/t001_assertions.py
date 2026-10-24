from __future__ import annotations

import re

T001_EXPECTED = {
    "business": "Luxe Hair & Beauty",
    "category": "Call handling",
    "priority": "Critical",
}


def normalize_speech(text: str) -> str:
    value = text.casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9+ ]+", " ", value)
    return " ".join(value.split())


def assess_t001(transcript: str) -> dict[str, object]:
    normalized = normalize_speech(transcript)
    greeting = bool(re.search(r"\b(hello|hi|good morning|good afternoon|good evening|thanks for calling)\b", normalized))
    business = "luxe hair and beauty" in normalized or "luxe hair beauty" in normalized
    ai_disclosure = bool(re.search(r"\b(ai|a i|artificial intelligence)\b", normalized))
    offer_help = any(
        phrase in normalized
        for phrase in (
            "how can i help",
            "how may i help",
            "how can i assist",
            "how may i assist",
            "what can i do for you",
            "what can i help you with",
        )
    )
    concise = len(normalized.split()) <= 45

    # Automated speech recognition can verify the required greeting elements.
    # It cannot prove that every unrecognized factual claim is false, so claims
    # outside the brief greeting are sent for review instead of being passed.
    risky_claim = bool(
        re.search(
            r"\b(open|closed|hours|price|prices|dollar|appointment|book|booking|"
            r"available|availability|service|services|staff|sarah|emma|chloe|priya)\b|\$|\b\d+\b",
            normalized,
        )
    )
    checks = {
        "greeting": greeting,
        "business_identified": business,
        "ai_disclosed": ai_disclosure,
        "offer_to_help": offer_help,
        "concise": concise,
        "no_obvious_unverified_claim": not risky_claim,
    }
    if not transcript.strip():
        status = "FAIL"
        reason = "No speech was transcribed from the receptionist."
    elif not all(checks.values()) and risky_claim and all(
        checks[k] for k in ("greeting", "business_identified", "ai_disclosed", "offer_to_help", "concise")
    ):
        status = "NEEDS_REVIEW"
        reason = "Required greeting elements were detected, but extra factual claims need review."
    elif all(checks.values()):
        status = "PASS"
        reason = "All machine-checkable T001 greeting criteria passed."
    else:
        status = "FAIL"
        failed = [name for name, passed in checks.items() if not passed]
        reason = "Failed checks: " + ", ".join(failed)
    return {"status": status, "reason": reason, "checks": checks, "transcript": transcript}
