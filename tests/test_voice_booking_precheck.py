from services.voice.session import LiveCallSession


def test_live_call_session_tracks_prepared_booking_fingerprint():
    # Exercise the stateful guard without needing a database call.
    session = object.__new__(LiveCallSession)
    session._prepared_booking_fingerprint = None
    session.prepare_booking("abc")
    assert session.booking_is_prepared("abc") is True
    assert session.booking_is_prepared("different") is False
    session.clear_prepared_booking()
    assert session.booking_is_prepared("abc") is False


def test_live_call_session_tracks_prepared_reschedule_fingerprint():
    session = object.__new__(LiveCallSession)
    session._prepared_reschedule_fingerprint = None
    session.prepare_reschedule("move-abc")
    assert session.reschedule_is_prepared("move-abc") is True
    assert session.reschedule_is_prepared("different") is False
    session.clear_prepared_reschedule()
    assert session.reschedule_is_prepared("move-abc") is False
