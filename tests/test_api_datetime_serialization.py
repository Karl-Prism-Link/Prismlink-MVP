from datetime import UTC, datetime
from uuid import uuid4

from apps.api.schemas import CallOut, TranscriptMessageOut


def test_naive_database_utc_timestamp_is_explicitly_utc_in_api_json() -> None:
    timestamp = datetime(2026, 11, 25, 22, 2, 16)
    call = CallOut(
        id=uuid4(),
        external_call_id="test-call",
        caller_number="0220000000",
        called_number="0900000000",
        status="completed",
        outcome="failed_fallback",
        started_at=timestamp,
        ended_at=None,
        duration_seconds=0,
    )

    payload = call.model_dump(mode="json")

    assert payload["started_at"] == "2026-11-25T22:02:16Z"


def test_aware_timestamp_is_normalised_to_utc_in_api_json() -> None:
    timestamp = datetime.fromisoformat("2026-11-26T11:02:16+13:00")
    message = TranscriptMessageOut(
        speaker="caller",
        text="Hello",
        sequence_number=1,
        created_at=timestamp,
    )

    payload = message.model_dump(mode="json")

    assert payload["created_at"] == "2026-11-25T22:02:16Z"


def test_utc_serialization_does_not_change_the_instant() -> None:
    timestamp = datetime(2026, 11, 25, 22, 2, 16, tzinfo=UTC)
    call = CallOut(
        id=uuid4(),
        external_call_id="test-call",
        caller_number=None,
        called_number=None,
        status="completed",
        outcome="completed",
        started_at=timestamp,
        ended_at=None,
        duration_seconds=None,
    )

    assert call.model_dump(mode="json")["started_at"] == "2026-11-25T22:02:16Z"
