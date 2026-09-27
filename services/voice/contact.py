from __future__ import annotations

import re
from dataclasses import dataclass

_DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "one": "1",
    "won": "1",
    "two": "2",
    "to": "2",
    "too": "2",
    "three": "3",
    "four": "4",
    "for": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "ate": "8",
    "nine": "9",
}

_DIGIT_NAMES = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}


@dataclass(frozen=True)
class PhoneCapture:
    digits: str
    spoken: str
    raw_digits: str
    from_international_prefix: bool = False


def _normalise_storage_digits(raw_digits: str, *, international: bool) -> tuple[str, bool]:
    digits = re.sub(r"\D", "", raw_digits)
    converted = False
    if digits.startswith("0064") and len(digits) > 4:
        digits = "0" + digits[4:]
        converted = True
    elif (
        digits.startswith("64")
        and len(digits) >= 10
        and (international or not digits.startswith("640"))
    ):
        digits = "0" + digits[2:]
        converted = True
    return digits, converted


def speak_phone_digits(digits: str) -> str:
    """Render a phone number as explicit individual digits for TTS readback."""
    clean = re.sub(r"\D", "", digits)
    return " ".join(_DIGIT_NAMES[digit] for digit in clean)


def _digit_runs(text: str) -> list[tuple[str, bool]]:
    tokens = re.findall(r"\+|\d+|[a-zA-Z]+", text.casefold())
    runs: list[tuple[str, bool]] = []
    current: list[str] = []
    international = False
    multiplier = 1

    def flush() -> None:
        nonlocal current, international, multiplier
        if current:
            runs.append(("".join(current), international))
        current = []
        international = False
        multiplier = 1

    for token in tokens:
        if token == "+":
            if current:
                flush()
            international = True
            continue
        if token in {"double", "triple"}:
            multiplier = 2 if token == "double" else 3
            continue

        if token.isdigit():
            digit_chunk = token
        else:
            digit_chunk = _DIGIT_WORDS.get(token, "")

        if digit_chunk:
            if multiplier > 1 and len(digit_chunk) == 1:
                digit_chunk *= multiplier
            multiplier = 1
            current.extend(digit_chunk)
            continue

        flush()

    flush()
    return runs


def extract_phone_from_speech(
    text: str, *, min_digits: int = 7, max_digits: int = 12
) -> PhoneCapture | None:
    """Extract the longest plausible contiguous phone-number run from caller speech.

    The function only returns digits that are present in the transcript. It never pads,
    guesses, or invents a missing digit.
    """
    candidates: list[PhoneCapture] = []
    for raw_digits, international in _digit_runs(text):
        digits, converted = _normalise_storage_digits(raw_digits, international=international)
        if min_digits <= len(digits) <= max_digits:
            candidates.append(
                PhoneCapture(
                    digits=digits,
                    spoken=speak_phone_digits(digits),
                    raw_digits=raw_digits,
                    from_international_prefix=converted,
                )
            )
    if not candidates:
        return None
    # Prefer the longest run; if equal, prefer the most recent run in the utterance.
    return sorted(enumerate(candidates), key=lambda item: (len(item[1].digits), item[0]))[-1][1]


def phone_from_recent_transcripts(transcripts: tuple[str, ...] | list[str]) -> PhoneCapture | None:
    for text in reversed(transcripts):
        capture = extract_phone_from_speech(text)
        if capture is not None:
            return capture
    return None


def extract_phone_digit_fragment(text: str, *, max_digits: int = 12) -> str | None:
    """Extract a short caller-spoken digit fragment for an active phone capture.

    Unlike :func:`extract_phone_from_speech`, this can return fewer than seven digits
    so a number may be collected across short turns. Obvious date/time fragments are
    ignored to avoid turning ``09:00`` or ``Monday 17`` into a phone number.
    """
    value = text.casefold().strip()
    if not value:
        return None
    if re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|january|february|march|april|may|june|july|august|september|october|november|december)\b",
        value,
    ):
        return None
    if re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", value):
        return None

    raw = "".join(run for run, _international in _digit_runs(value))
    if not raw:
        return None
    international = value.lstrip().startswith("+") or bool(
        re.search(r"\bplus\s+six\s+four\b", value)
    )
    digits, _converted = _normalise_storage_digits(raw, international=international)
    if 1 <= len(digits) <= max_digits:
        return digits
    return None


def extract_spelled_name(text: str) -> str | None:
    """Recover an explicitly letter-spelled caller name such as ``K a r l``.

    STT often prefixes a phonetic guess before the caller spells the correction, for
    example ``Cow. K a r l.``. In that case the trailing isolated-letter run is
    authoritative. Ordinary prose is never converted into a guessed name.
    """
    value = text.casefold().strip()
    if not value:
        return None
    tokens = re.findall(r"[a-z]+", value)
    cue_index = None
    for i, token in enumerate(tokens):
        if token in {"spell", "spelled", "spelt", "spelling"}:
            cue_index = i + 1
            break

    if cue_index is not None:
        letters = [token for token in tokens[cue_index:] if len(token) == 1]
        if len(letters) < 2:
            return None
    else:
        # Prefer a trailing run of isolated letters. This handles transcripts such as
        # ``Kyle. K a r l`` without treating the earlier STT guess as authoritative.
        letters = []
        for token in reversed(tokens):
            if len(token) != 1:
                break
            letters.append(token)
        letters.reverse()
        if len(letters) < 3:
            # If the entire utterance is letters, retain the original behaviour.
            if tokens and all(len(token) == 1 for token in tokens) and len(tokens) >= 3:
                letters = tokens
            else:
                return None

    name = "".join(letters)
    if not (2 <= len(name) <= 40):
        return None
    return name.title()


def extract_booking_name_candidate(text: str) -> str | None:
    """Extract a conservative caller name while the booking flow is awaiting a name.

    This parser is deliberately context-gated by the runtime. It accepts a short plain
    name or an explicit ``my name is ...`` response, but rejects dates, numbers, yes/no
    replies, and other obvious booking instructions.
    """
    spelled = extract_spelled_name(text)
    if spelled:
        return spelled

    value = " ".join(str(text or "").strip().split())
    if not value or re.search(r"\d", value):
        return None
    lowered = value.casefold().strip(" .,!?")
    if lowered in {"yes", "yep", "yeah", "no", "nope", "nah", "okay", "ok"}:
        return None
    if re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|"
        r"phone|number|book|booking|appointment|cut|colour|color|blow wave)\b",
        lowered,
    ):
        return None

    candidate = re.sub(
        r"^(?:my name is|name is|i am|i'm|im|this is|it's|its)\s+", "", lowered
    ).strip()
    words = re.findall(r"[a-z]+(?:['-][a-z]+)?", candidate, re.I)
    if not (1 <= len(words) <= 4):
        return None
    if (
        " ".join(words).casefold()
        != re.sub(r"[^a-z'-]+", " ", candidate, flags=re.I).strip().casefold()
    ):
        return None
    return " ".join(word.capitalize() for word in words)


def is_explicit_affirmation(text: str | None) -> bool:
    if not text:
        return False
    value = re.sub(r"[^a-z]+", " ", text.casefold()).strip()
    if not value or re.search(r"\b(no|not|wrong|incorrect)\b", value):
        return False
    return bool(
        re.search(
            r"\b(yes|yep|yeah|correct|confirm|confirmed|right|sounds good|please do|that is correct|thats correct)\b",
            value,
        )
    )


def is_explicit_rejection(text: str | None) -> bool:
    """Return True only for a clear negative/rejection utterance."""
    if not text:
        return False
    value = re.sub(r"[^a-z]+", " ", text.casefold()).strip()
    if not value:
        return False
    if is_explicit_affirmation(value):
        return False
    return bool(
        re.search(
            r"\b(no|nope|nah|wrong|incorrect|not that one|different one|another one)\b",
            value,
        )
    )


def is_management_phone_unavailable(text: str | None) -> bool:
    """Detect a clear statement that the caller cannot provide the booking phone.

    This is intentionally conservative. It is used only to stop the receptionist from
    repeatedly asking for the same identity factor and to offer a staff follow-up path.
    It never weakens appointment ownership checks.
    """
    if not text:
        return False
    value = re.sub(r"[^a-z0-9']+", " ", text.casefold()).strip()
    if not value:
        return False
    return bool(
        re.search(
            r"\b("
            r"i (?:do not|don't|dont) (?:know|remember|have) (?:it|the number|that number)|"
            r"i (?:can not|can't|cant|cannot) remember (?:it|the number|that number)|"
            r"i (?:forgot|forgotten) (?:it|the number|that number)|"
            r"i no longer have (?:it|the number|that number)|"
            r"not sure (?:what )?(?:it|the number|that number) (?:is|was)|"
            r"can't remember|cant remember|cannot remember|don't remember|dont remember"
            r")\b",
            value,
        )
    )


def is_management_phone_recovery_statement(text: str | None) -> bool:
    """Return True when a caller explicitly re-identifies a number as the booking phone.

    After the caller has said they cannot remember the booking number, a later plain
    phone number may be only a callback number. Requiring explicit recovery language
    prevents that callback number from silently becoming appointment identity proof.
    """
    if not text or extract_phone_from_speech(text) is None:
        return False
    value = re.sub(r"[^a-z0-9']+", " ", text.casefold()).strip()
    return bool(
        re.search(
            r"\b("
            r"(?:actually )?i remember(?: it| now)?|"
            r"i found (?:it|the number)|"
            r"(?:the|my) booking (?:phone|number) (?:is|was)|"
            r"the number i used (?:to book|for the booking) (?:is|was)|"
            r"booked under (?:the number )?|"
            r"appointment (?:phone|number) (?:is|was)"
            r")\b",
            value,
        )
    )


_WEEKDAY_DISPLAY = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
    "today": "today",
    "tomorrow": "tomorrow",
}


def extract_booking_day_reference(text: str) -> str | None:
    """Return a simple caller-spoken day reference without calculating a date."""
    value = str(text or "").casefold()
    match = re.search(
        r"\b(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow)\b",
        value,
    )
    if not match:
        return None
    token = match.group(1)
    display = _WEEKDAY_DISPLAY[token]
    return (
        f"next {display}"
        if match.group(0).startswith("next ") and token not in {"today", "tomorrow"}
        else display
    )


def has_explicit_booking_time(text: str) -> bool:
    """Detect whether caller speech contains a usable clock-time expression."""
    value = str(text or "").casefold()
    if re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", value):
        return True
    if re.search(r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b", value):
        return True
    if re.search(r"\b\d{1,2}\s+o['’ ]?clock\b", value):
        return True
    if re.search(
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:a\.?m\.?|p\.?m\.?|o['’ ]?clock)\b",
        value,
    ):
        return True
    if re.search(r"\b(?:noon|midday|midnight)\b", value):
        return True
    return False


def recent_booking_datetime_phrase(transcripts: tuple[str, ...] | list[str]) -> str | None:
    """Recover a bounded caller-spoken booking day/time phrase across recent turns.

    This is a routing aid only; the authoritative date calculation still happens in
    the deterministic booking tool. Prefer one original utterance containing both a
    day/date and clock time. If the caller split them across turns, concatenate the
    two original transcript fragments without inventing a date or meridiem.
    """
    recent = [" ".join(str(text or "").split()).strip() for text in list(transcripts)[-6:]]
    recent = [text for text in recent if text]
    if not recent:
        return None

    for text in reversed(recent):
        if extract_booking_day_reference(text) and has_explicit_booking_time(text):
            return text

    day_text = next(
        (text for text in reversed(recent) if extract_booking_day_reference(text)), None
    )
    time_text = next((text for text in reversed(recent) if has_explicit_booking_time(text)), None)
    if day_text and time_text:
        if day_text == time_text:
            return day_text
        return f"{day_text} {time_text}"
    return None
