from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import deque
from collections.abc import Sequence
from typing import Any

STATE_MARKER = "PRISM_LINK_STATE:"


def parse_retry_delay_seconds(error_text: str, *, default: float = 1.0) -> float:
    """Extract Groq retry timing, preserving provider waits up to 30 seconds."""
    text = error_text or ""
    milliseconds = re.search(r"(?:retry-after-ms|try again in)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)ms", text, re.I)
    if milliseconds:
        return min(30.0, max(0.25, float(milliseconds.group(1)) / 1000.0))
    seconds = re.search(r"(?:retry-after|try again in)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)s?", text, re.I)
    if seconds:
        return min(30.0, max(0.25, float(seconds.group(1))))
    return min(30.0, max(0.25, default))


class ModelCapacityExceeded(RuntimeError):
    """Raised when a live turn cannot safely fit inside the provider TPM window."""


class RollingModelTokenBudget:
    """Process-wide per-model rolling token admission control for live voice calls.

    Reservations include estimated prompt tokens and the requested completion cap.
    A small safety margin is kept below the provider's published TPM ceiling.
    """

    def __init__(
        self,
        *,
        tokens_per_minute: int = 7000,
        window_seconds: float = 60.0,
        max_wait_seconds: float = 1.5,
    ) -> None:
        self.tokens_per_minute = max(1, int(tokens_per_minute))
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_wait_seconds = max(0.0, float(max_wait_seconds))
        self._usage: dict[str, deque[tuple[float, int]]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def estimate_request_tokens(params: dict[str, Any]) -> int:
        payload = {
            "messages": params.get("messages") or [],
            "tools": params.get("tools") or [],
        }
        serialized = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        prompt_estimate = math.ceil(len(serialized) / 3.2)
        completion_cap = params.get("max_completion_tokens") or params.get("max_tokens") or 256
        return max(1, prompt_estimate + int(completion_cap))

    async def reserve(self, model: str, estimated_tokens: int) -> float:
        """Reserve a rolling-window budget or fail quickly into safe voice fallback."""
        tokens = max(1, int(estimated_tokens))
        if tokens > self.tokens_per_minute:
            raise ModelCapacityExceeded(
                f"429 rate_limit_exceeded: estimated request {tokens} exceeds local "
                f"{self.tokens_per_minute} TPM safety budget for {model}"
            )
        started = time.monotonic()
        deadline = started + self.max_wait_seconds
        while True:
            async with self._lock:
                now = time.monotonic()
                entries = self._usage.setdefault(model, deque())
                while entries and now - entries[0][0] >= self.window_seconds:
                    entries.popleft()
                used = sum(value for _, value in entries)
                if used + tokens <= self.tokens_per_minute:
                    entries.append((now, tokens))
                    return now - started
                wait_for_capacity = max(0.0, entries[0][0] + self.window_seconds - now)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or wait_for_capacity > remaining:
                raise ModelCapacityExceeded(
                    f"429 rate_limit_exceeded: local {model} token budget is temporarily exhausted"
                )
            await asyncio.sleep(min(wait_for_capacity, remaining))


def is_rate_limit_error(error_text: str) -> bool:
    text = (error_text or "").lower()
    return "429" in text or "rate limit" in text or "rate_limit_exceeded" in text


def is_generic_booking_request(text: str) -> bool:
    """Recognise clear booking-intent wording without relying on the LLM.

    This intentionally covers common conversational/STT variants such as
    ``I book in an appointment?`` (a frequent rendering of ``book an
    appointment``) while avoiding appointment-management phrases such as
    cancel/reschedule. The function only establishes booking intent; it never
    authorises a consequential action.
    """
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return False

    lowered = value.casefold()
    if re.search(r"\b(?:cancel|resched(?:ule|uling)?|move|change)\b", lowered):
        return False

    patterns = (
        r"\b(?:can|could|would)\s+i\s+(?:please\s+)?(?:book|make)\b",
        r"\b(?:book|make|need|want|get)\s+(?:me\s+)?(?:in\s+)?(?:an?\s+)?appointment\b",
        r"\bi\s+book\s+(?:in\s+)?(?:an?\s+)?appointment\b",
        r"\bi\s+(?:need|want|would\s+like)\s+(?:to\s+)?(?:book|make)\b",
        r"\bbook\s+me\s+in\b",
        r"\b(?:book|make)\s+(?:an?\s+)?booking\b",
    )
    return any(re.search(pattern, lowered, re.I) for pattern in patterns)


def select_latest_caller_text(
    context_latest_user: str | None,
    recent_caller_transcripts: Sequence[str],
) -> str:
    """Choose the newest caller utterance across Pipecat event/frame ordering.

    In live WebRTC runs the ``on_user_turn_stopped`` callback can update the call
    session before a subsequently delivered ``LLMContextFrame`` has caught up. If
    that frame still ends with the previous caller turn, trusting it would regress
    the session (for example, treating ``K a r l`` as if the caller had said the
    prior ``Cow`` again). Conversely, when the context contains a brand-new caller
    turn that has not reached the event callback yet, that context turn should win.
    """
    context_text = " ".join(str(context_latest_user or "").split()).strip()
    recent = [" ".join(str(text or "").split()).strip() for text in recent_caller_transcripts]
    recent = [text for text in recent if text]
    if not recent:
        return context_text
    session_latest = recent[-1]
    if not context_text or context_text == session_latest:
        return session_latest
    if context_text in recent[:-1]:
        return session_latest
    return context_text


def compact_context_messages(
    messages: Sequence[dict[str, Any]],
    *,
    max_user_turns: int,
    state_summary: str | None = None,
) -> list[dict[str, Any]]:
    """Keep bounded recent dialogue while preserving complete tool exchanges.

    The cut point is always a user message, so assistant tool-call + tool-result pairs
    after that user turn remain together. Static developer messages are retained; the
    dynamic PRISM_LINK_STATE message is replaced rather than accumulated.
    """
    max_user_turns = max(1, int(max_user_turns))
    clean = [dict(message) for message in messages]
    static_developer = [
        message
        for message in clean
        if message.get("role") == "developer"
        and not str(message.get("content") or "").startswith(STATE_MARKER)
    ]
    dialogue = [message for message in clean if message.get("role") != "developer"]
    user_indexes = [i for i, message in enumerate(dialogue) if message.get("role") == "user"]
    if len(user_indexes) > max_user_turns:
        dialogue = dialogue[user_indexes[-max_user_turns] :]

    output = static_developer
    if state_summary:
        output.append({"role": "developer", "content": f"{STATE_MARKER} {state_summary}"})
    output.extend(dialogue)
    return output
