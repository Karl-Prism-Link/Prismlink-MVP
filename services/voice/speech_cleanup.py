from __future__ import annotations

import re


def spoken_segment_key(text: str) -> str:
    """Normalize spoken output and collapse semantically duplicate receptionist questions."""
    value = re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()
    if not value:
        return ""
    # Providers sometimes emit ``Sure, <question>`` immediately after the same
    # question without the filler. Treat those as the same spoken segment.
    value = re.sub(r"^(?:sure thing|sure|okay|ok|great|got it|alright)\s+", "", value).strip()

    weekday = next(
        (
            day
            for day in (
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            )
            if day in value
        ),
        "",
    )
    if re.search(
        r"\b(?:what|which|preferred|suitable|prefer|works|time|when)\b", value
    ) and re.search(
        r"\b(?:what time|preferred time|suitable time|time .* works|when would|what .* time|do you have a preferred time)\b",
        value,
    ):
        return f"ask_time:{weekday or 'unspecified'}"
    if re.search(
        r"\b(?:your name|name please|what s your name|may i have your name|could i have your name)\b",
        value,
    ):
        return "ask_name"
    if re.search(
        r"\b(?:phone number|number you d like us to use|number you used to book)\b", value
    ):
        return "ask_phone"
    if re.search(
        r"\b(?:which service|exact service|which cut|women s cut or men s cut|did you mean .* cut)\b",
        value,
    ):
        return "ask_service"
    return value


def is_spoken_question(text: str) -> bool:
    """Best-effort question detection for one-question-per-turn voice output."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if raw.endswith("?"):
        return True
    value = re.sub(r"[^a-z0-9]+", " ", raw.casefold()).strip()
    return bool(
        re.match(
            r"^(?:what|when|where|which|who|how|why|can|could|would|will|do|does|did|is|are|shall|may|have you|did you)",
            value,
        )
    )


def limit_spoken_questions(text: str) -> str:
    """Keep at most the first spoken question in one generated text segment.

    Some providers occasionally stream several receptionist questions into one
    aggregated frame (for example name + phone + a repeated phone request). The
    downstream one-question-per-turn suppressor cannot help when all of those
    questions are inside the same frame, so trim after the first question mark.
    Any statement that precedes that first question is retained.
    """
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return ""
    first = value.find("?")
    if first == -1:
        return value
    return value[: first + 1].strip()


def deterministic_post_action_social_reply(text: str | None) -> str | None:
    """Return a no-LLM closing reply for common post-action social turns."""
    value = re.sub(r"[^a-z]+", " ", str(text or "").casefold()).strip()
    if not value:
        return None
    if value in {
        "thank you",
        "thanks",
        "thanks a lot",
        "thank you very much",
        "cheers",
        "cool thank you",
        "cool thanks",
    }:
        return "You're welcome. Have a great day."
    if value in {"bye", "goodbye", "see you", "have a good day", "have a great day"}:
        return "Goodbye. Have a great day."
    if value in {"cool", "great", "awesome", "okay", "ok", "sounds good"}:
        return "You're welcome."
    return None


def reschedule_success_speech(result: dict) -> str:
    """Deterministic post-reschedule speech from authoritative tool output."""
    service = str(result.get("service") or "appointment").strip()
    spoken_start = str(
        result.get("spoken_start") or result.get("requested_spoken_start") or ""
    ).strip()
    if spoken_start:
        return f"Your {service} appointment has been moved to {spoken_start}."
    return f"Your {service} appointment has been rescheduled."


def cancellation_success_speech(result: dict) -> str:
    """Deterministic post-cancellation speech from authoritative tool output."""
    service = str(result.get("service") or "appointment").strip()
    spoken_start = str(result.get("spoken_start") or "").strip()
    if spoken_start:
        return f"Your {service} appointment on {spoken_start} has been cancelled."
    return f"Your {service} appointment has been cancelled."


def booking_confirmation_speech(result: dict) -> str:
    """Deterministic readback of an already prepared booking before confirmation."""
    service = str(result.get("service") or "appointment").strip()
    spoken_start = str(result.get("spoken_start") or "").strip()
    name = str(result.get("customer_name") or "").strip()
    spoken_phone = str(result.get("spoken_phone") or "").strip()
    staff = str(result.get("staff") or "").strip()

    details = service
    if spoken_start:
        details += f" on {spoken_start}"
    if staff:
        details += f" with {staff}"
    if name:
        details += f" for {name}"
    if spoken_phone:
        details += f". Phone {spoken_phone}"
    return f"To confirm: {details}. Is that correct?"


def booking_success_speech(result: dict) -> str:
    """Deterministic post-booking speech from authoritative tool output."""
    service = str(result.get("service") or "appointment").strip()
    spoken_start = str(result.get("spoken_start") or "").strip()
    if spoken_start:
        return f"Your {service} is booked for {spoken_start}."
    return f"Your {service} appointment is booked."


def mutation_failure_speech(result: dict, *, action: str) -> str:
    """Safe deterministic wording when a prepared mutation cannot be completed."""
    code = str(result.get("code") or "").upper()
    if code in {"APPOINTMENT_CONFLICT", "TIME_UNAVAILABLE", "CALENDAR_CONFLICT"}:
        return "That time is no longer available. Let's choose another time."
    if code in {
        "CONFIRMATION_REQUIRED",
        "BOOKING_PRECHECK_REQUIRED",
        "RESCHEDULE_PRECHECK_REQUIRED",
        "CANCEL_PRECHECK_REQUIRED",
    }:
        return f"I couldn't safely {action} that yet. Let's confirm the details again."
    return f"I couldn't safely {action} that right now. Please confirm the details with me again."
