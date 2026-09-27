from services.voice.session import LiveCallSession


def test_recent_caller_transcripts_property_exists():
    assert isinstance(LiveCallSession.recent_caller_transcripts, property)


def test_prepared_action_flags_are_explicit():
    session = object.__new__(LiveCallSession)
    session._prepared_booking_fingerprint = None
    session._prepared_booking_payload = None
    session._prepared_reschedule_fingerprint = None
    session._prepared_cancel_fingerprint = None

    assert session.has_prepared_booking is False
    assert session.has_prepared_reschedule is False
    assert session.has_prepared_cancel is False

    session.prepare_booking("b1", {"fingerprint": "b1"})
    session.prepare_reschedule("r1")
    session.prepare_cancel("c1")

    assert session.has_prepared_booking is True
    assert session.has_prepared_reschedule is True
    assert session.has_prepared_cancel is True
