from services.service_names import service_name_from_speech
from services.voice.contact import extract_phone_digit_fragment, extract_spelled_name
from services.voice.session import LiveCallSession
from services.voice.speech_cleanup import spoken_segment_key


def _bare_session() -> LiveCallSession:
    session = object.__new__(LiveCallSession)
    session._prepared_booking_fingerprint = None
    session._prepared_booking_payload = None
    session._booking_phone_digits = ""
    session._booking_phone_capture_active = False
    session._booking_spelled_name = None
    return session


def test_means_alias_resolves_to_mens_cut():
    assert service_name_from_speech("Means") == "Men's Cut"
    assert service_name_from_speech("I want a means cut") == "Men's Cut"
    assert service_name_from_speech("a cut") is None


def test_phone_fragment_can_collect_short_digit_turns_but_ignores_time():
    assert extract_phone_digit_fragment("O two one") == "021"
    assert extract_phone_digit_fragment("three four five six") == "3456"
    assert extract_phone_digit_fragment("09:00") is None
    assert extract_phone_digit_fragment("Wednesday 12 August") is None


def test_booking_phone_buffer_replaces_with_complete_fresh_attempt():
    session = _bare_session()
    session.ingest_booking_phone_fragment("021")
    assert session.booking_phone_digits == "021"
    session.ingest_booking_phone_fragment("123456")
    assert session.booking_phone_digits == "021123456"
    session.ingest_booking_phone_fragment("0219876543")
    assert session.booking_phone_digits == "0219876543"


def test_explicit_spelled_name_is_recovered():
    assert extract_spelled_name("My name is spelled k a r l") == "Karl"
    assert extract_spelled_name("k a r l") == "Karl"
    assert extract_spelled_name("my name is Kyle") is None


def test_prepared_booking_payload_is_locked_with_fingerprint():
    session = _bare_session()
    payload = {"customer_name": "Karl", "customer_phone": "0211234567"}
    session.prepare_booking("fp", payload)
    assert session.booking_is_prepared("fp") is True
    assert session.prepared_booking_payload == payload
    copy = session.prepared_booking_payload
    assert copy is not None
    copy["customer_name"] = "Changed"
    assert session.prepared_booking_payload["customer_name"] == "Karl"


def test_spelled_name_invalidates_stale_prepared_booking():
    session = _bare_session()
    # Voice state is needed by set_booking_spelled_name.
    from services.voice.session import VoiceConversationState

    session.voice_state = VoiceConversationState()
    session.prepare_booking("fp", {"customer_name": "Kyle"})
    session.set_booking_spelled_name("Karl")
    assert session.booking_spelled_name == "Karl"
    assert session.voice_state.customer_name == "Karl"
    assert session.prepared_booking_payload is None


def test_filler_prefixed_duplicate_question_has_same_key():
    assert spoken_segment_key("When would you like the cut?") == spoken_segment_key(
        "Sure, when would you like the cut?"
    )
