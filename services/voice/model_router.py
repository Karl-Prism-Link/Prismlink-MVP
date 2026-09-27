from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ModelTier(StrEnum):
    FAST = "fast"
    REASONING = "reasoning"


@dataclass(frozen=True)
class RoutingDecision:
    tier: ModelTier
    reason: str
    use_tools: bool = True


_SIMPLE_SOCIAL_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|cheers|cool|great|awesome|okay|ok|bye|goodbye|"
    r"have a good day|have a great day)[.! ]*$",
    re.I,
)
_DATE_TIME_RE = re.compile(
    r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|\d{1,2}:\d{2}|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
    re.I,
)
_CORRECTION_RE = re.compile(
    r"\b(?:actually|instead|correction|correct that|change that|not that|wrong|i meant|"
    r"rather|different|another|resched|cancel|move my appointment|change my appointment)\b",
    re.I,
)
_MULTI_SERVICE_RE = re.compile(
    r"\b(?:and|plus|both|as well as|with)\b.*\b(?:cut|colour|color|blow|treatment|service)\b",
    re.I,
)
_CONTACT_CAPTURE_RE = re.compile(
    r"(?:\b(?:my name is|name is|phone|number|call me|spell|spelled|spelt)\b|"
    r"^(?:[a-z]\s+){2,}[a-z][.! ]*$|(?:\b(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine)\b.*){3,})",
    re.I,
)
_ENQUIRY_RE = re.compile(
    r"\b(?:open|opening|close|closing|hours|price|cost|service|services|staff|address|parking|"
    r"how long|duration|do you offer|what do you offer)\b",
    re.I,
)


def _clean(text: str | None) -> str:
    return " ".join(str(text or "").split()).strip()


def route_voice_turn(
    *,
    intent: str,
    service: str | None,
    starts_at: str | None,
    last_tool: str | None,
    last_tool_ok: bool | None,
    confirmed: bool,
    latest_user_text: str | None,
    after_tool_result: bool = False,
) -> RoutingDecision:
    """Choose the cheapest safe LLM tier for one voice turn.

    Correctness-critical mutations remain in deterministic tools. This router only
    chooses which model should interpret or phrase the current conversational turn.
    """

    text = _clean(latest_user_text)
    lowered = text.casefold()
    intent = (intent or "unknown").casefold()

    # A tool continuation should not be allowed to call another tool automatically.
    # This keeps one consequential tool step per caller turn and materially shrinks
    # the second completion prompt after a tool result.
    if after_tool_result:
        if last_tool_ok is False:
            return RoutingDecision(ModelTier.REASONING, "tool_failure_recovery", use_tools=False)
        if intent in {"cancel", "reschedule", "appointment_management"}:
            return RoutingDecision(ModelTier.REASONING, "management_tool_readback", use_tools=False)
        return RoutingDecision(ModelTier.FAST, "tool_result_readback", use_tools=False)

    if confirmed and _SIMPLE_SOCIAL_RE.match(text):
        return RoutingDecision(ModelTier.FAST, "post_action_social", use_tools=False)

    if _SIMPLE_SOCIAL_RE.match(text):
        return RoutingDecision(ModelTier.FAST, "simple_social", use_tools=False)

    if intent in {"cancel", "reschedule", "appointment_management"}:
        return RoutingDecision(ModelTier.REASONING, "appointment_management", use_tools=True)

    if intent in {"message", "routing"}:
        return RoutingDecision(ModelTier.FAST, "message_or_routing", use_tools=True)

    if _CORRECTION_RE.search(lowered):
        return RoutingDecision(ModelTier.REASONING, "correction_or_recovery", use_tools=True)

    if _MULTI_SERVICE_RE.search(lowered):
        return RoutingDecision(ModelTier.REASONING, "multi_service_request", use_tools=True)

    if last_tool_ok is False:
        return RoutingDecision(ModelTier.REASONING, "previous_tool_failure", use_tools=True)

    if intent == "booking":
        # Date/time interpretation is where we have seen the largest model error rate.
        # The backend still resolves the date deterministically, but use the stronger
        # model to preserve the caller phrase and choose the correct tool arguments.
        if _DATE_TIME_RE.search(lowered):
            return RoutingDecision(ModelTier.REASONING, "booking_date_time", use_tools=True)

        # Once service + time are already authoritative, collecting a name/phone or
        # asking the next short booking question is a low-reasoning conversational turn.
        if (
            service
            and starts_at
            and (_CONTACT_CAPTURE_RE.search(lowered) or len(text.split()) <= 8)
        ):
            return RoutingDecision(ModelTier.FAST, "booking_contact_capture", use_tools=True)

        # A deterministically normalized service reply (e.g. "means cut" -> Men's Cut)
        # can use the fast model to ask for the missing date/time.
        if (
            service
            and not starts_at
            and len(text.split()) <= 6
            and not _DATE_TIME_RE.search(lowered)
        ):
            return RoutingDecision(ModelTier.FAST, "booking_service_resolved", use_tools=True)

        return RoutingDecision(ModelTier.REASONING, "booking_logic", use_tools=True)

    if _ENQUIRY_RE.search(lowered):
        return RoutingDecision(ModelTier.FAST, "salon_enquiry", use_tools=True)

    # Default conversational turns to the fast tier. Any subsequent tool failure or
    # correction automatically promotes the next turn to the reasoning tier.
    return RoutingDecision(ModelTier.FAST, "default_conversation", use_tools=True)


def confirmation_fastpath_action(
    *,
    latest_user_text: str | None,
    has_prepared_booking: bool,
    has_prepared_reschedule: bool,
    has_prepared_cancel: bool,
    has_prepared_message: bool = False,
    is_affirmation: bool,
    is_rejection: bool,
) -> str | None:
    """Return a deterministic confirmation action for a short bare yes/no turn.

    Mixed utterances such as "no, make it four" intentionally go back to the LLM so
    the correction is interpreted rather than discarded.
    """

    text = _clean(latest_user_text)
    value = re.sub(r"[^a-z]+", " ", text.casefold()).strip()
    if not value or not (is_affirmation or is_rejection):
        return None

    bare_affirmations = {
        "yes",
        "yep",
        "yeah",
        "correct",
        "right",
        "confirm",
        "confirmed",
        "sounds good",
        "please do",
        "that is correct",
        "thats correct",
        "that s correct",
    }
    bare_rejections = {
        "no",
        "nope",
        "nah",
        "incorrect",
        "wrong",
        "that is wrong",
        "thats wrong",
        "that s wrong",
        "not correct",
    }
    if is_affirmation and value not in bare_affirmations:
        return None
    if is_rejection and value not in bare_rejections:
        return None

    prepared = [
        ("booking", has_prepared_booking),
        ("reschedule", has_prepared_reschedule),
        ("cancel", has_prepared_cancel),
        ("message", has_prepared_message),
    ]
    active = [name for name, enabled in prepared if enabled]
    if len(active) != 1:
        return None
    suffix = "confirm" if is_affirmation else "reject"
    return f"{active[0]}_{suffix}"
