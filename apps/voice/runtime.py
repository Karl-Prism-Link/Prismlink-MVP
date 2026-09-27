from dataclasses import dataclass


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float


class SalonIntentClassifier:
    """Deterministic fallback classifier for the runnable MVP.

    Replace or augment with provider-backed tool-calling in the live voice runtime while
    keeping the same bounded intent set.
    """

    def classify(self, text: str) -> IntentResult:
        value = text.lower()
        if any(word in value for word in ("cancel", "cancellation")):
            return IntentResult("cancellation", 0.94)
        if any(word in value for word in ("reschedule", "move my", "change my appointment")):
            return IntentResult("reschedule", 0.93)
        if any(word in value for word in ("book", "appointment", "available", "availability")):
            return IntentResult("booking", 0.90)
        if any(
            word in value
            for word in ("hours", "open", "close", "where are you", "address", "services")
        ):
            return IntentResult("enquiry", 0.87)
        if any(word in value for word in ("message", "call me back", "callback")):
            return IntentResult("message", 0.86)
        return IntentResult("unclear", 0.45)
