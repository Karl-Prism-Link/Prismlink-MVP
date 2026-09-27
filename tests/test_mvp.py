from datetime import UTC, datetime, timedelta


def register(client, *, name: str, slug: str, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "salon_name": name,
            "slug": slug,
            "email": email,
            "password": "StrongPass123!",
            "timezone": "Pacific/Auckland",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def add_service(client, token: str, name: str = "Cut", duration: int = 60):
    response = client.post(
        "/api/v1/salon/services",
        headers=auth(token),
        json={"name": name, "duration_minutes": duration},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_trial_profile_and_service_setup(client):
    token = register(client, name="Alpha Salon", slug="alpha-salon", email="alpha@example.com")
    profile = client.get("/api/v1/salon", headers=auth(token))
    assert profile.status_code == 200
    assert profile.json()["status"] == "trial"
    assert profile.json()["name"] == "Alpha Salon"

    service = add_service(client, token)
    assert service["duration_minutes"] == 60


def test_booking_requires_confirmation_and_is_idempotent(client):
    token = register(client, name="Beta Salon", slug="beta-salon", email="beta@example.com")
    service = add_service(client, token, "Colour", 90)
    start = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()
    body = {
        "customer_name": "Jamie",
        "customer_phone": "0210000000",
        "service_id": service["id"],
        "starts_at": start,
        "idempotency_key": "booking-idem-001",
        "confirmed": False,
        "source": "voice",
    }
    denied = client.post("/api/v1/appointments", headers=auth(token), json=body)
    assert denied.status_code == 409

    body["confirmed"] = True
    first = client.post("/api/v1/appointments", headers=auth(token), json=body)
    second = client.post("/api/v1/appointments", headers=auth(token), json=body)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_tenant_isolation_hides_other_salons_appointment(client):
    token_a = register(client, name="Gamma Salon", slug="gamma-salon", email="gamma@example.com")
    token_b = register(client, name="Delta Salon", slug="delta-salon", email="delta@example.com")
    service = add_service(client, token_b, "Blow wave", 45)
    start = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0).isoformat()
    created = client.post(
        "/api/v1/appointments",
        headers=auth(token_b),
        json={
            "customer_name": "Taylor",
            "customer_phone": "0211111111",
            "service_id": service["id"],
            "starts_at": start,
            "idempotency_key": "tenant-safe-001",
            "confirmed": True,
            "source": "voice",
        },
    )
    assert created.status_code == 201
    other_read = client.get(f"/api/v1/appointments/{created.json()['id']}", headers=auth(token_a))
    assert other_read.status_code == 404


def test_simulated_call_books_and_generates_summary(client):
    token = register(client, name="Echo Salon", slug="echo-salon", email="echo@example.com")
    service = add_service(client, token, "Trim", 30)
    start = (datetime.now(UTC) + timedelta(days=4)).replace(microsecond=0).isoformat()
    response = client.post(
        "/api/v1/calls/simulate",
        headers=auth(token),
        json={
            "caller_number": "0212222222",
            "utterance": "I'd like to book an appointment for a trim",
            "service_id": service["id"],
            "starts_at": start,
            "customer_name": "Morgan",
            "customer_phone": "0212222222",
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["call"]["outcome"] == "booked"
    assert data["appointment"] is not None
    assert data["call"]["summary"]["follow_up_required"] is False


def test_google_oauth_start_requires_authenticated_tenant(client, monkeypatch):
    from apps.api.routes import calendar as calendar_routes

    token = register(
        client,
        name="Foxtrot Salon",
        slug="foxtrot-salon",
        email="foxtrot@example.com",
    )
    captured = {}

    def fake_url(**kwargs):
        captured.update(kwargs)
        return "https://accounts.google.com/example"

    monkeypatch.setattr(calendar_routes, "build_authorization_url", fake_url)
    response = client.get(
        "/api/v1/calendar/google/connect?external_calendar_id=primary",
        headers=auth(token),
    )
    assert response.status_code == 200
    assert response.json()["authorization_url"] == "https://accounts.google.com/example"
    assert captured["external_calendar_id"] == "primary"


def test_local_google_credential_store_encrypts_payload(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    import services.credentials as credentials_module

    monkeypatch.setattr(
        credentials_module,
        "get_settings",
        lambda: SimpleNamespace(
            credential_store_path=str(tmp_path),
            credential_encryption_key="credential-store-test-secret",
            jwt_secret_key="unused-fallback-secret",
        ),
    )
    store = credentials_module.LocalEncryptedCredentialStore()
    tenant_id = uuid4()
    payload = {"access_token": "access-secret", "refresh_token": "refresh-secret"}
    ref = store.put(tenant_id, payload)

    encrypted_file = tmp_path / "google" / f"{tenant_id}.json.enc"
    raw = encrypted_file.read_bytes()
    assert b"access-secret" not in raw
    assert b"refresh-secret" not in raw
    assert store.get(ref) == payload

    store.delete(ref)
    assert not encrypted_file.exists()


async def test_voice_toolset_enforces_confirmation_and_persists_live_call(client):
    from database.repositories import AppointmentRepository, CallRepository, TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Voice Salon",
        slug="voice-salon",
        email="voice@example.com",
    )
    add_service(client, token, "Haircut", 45)

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("voice-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-test-001",
            caller_number="0213333333",
            called_number=tenant.phone_number,
        )
        await live.start()
        await live.append_transcript("caller", "I'd like a haircut")
        await live.append_transcript(
            "caller", "Jane Doe, zero two one three three three three three three three"
        )
        tools = VoiceToolset(db, tenant, live)
        start = (datetime.now(UTC) + timedelta(days=5)).replace(microsecond=0).isoformat()

        prepared = await tools.book_appointment(
            "Haircut",
            start,
            "Jane Doe",
            "0213333333",
            False,
        )
        assert prepared["ok"] is True
        assert prepared["booked"] is False
        assert prepared["ready_to_confirm"] is True
        assert prepared["customer_phone"] == "0213333333"
        assert await AppointmentRepository(db, tenant.id).list() == []

        await live.append_transcript(
            "assistant",
            f"Haircut at {prepared['spoken_start']}, Jane Doe, phone {prepared['spoken_phone']}. Is that correct?",
        )
        await live.append_transcript("caller", "Yes")
        created = await tools.book_appointment(
            "Haircut",
            start,
            "Jane Doe",
            "0213333333",
            True,
        )
        assert created["ok"] is True
        assert created["status"] == "booked"
        await live.append_transcript("assistant", "Your appointment is confirmed.")
        await live.finish()

        stored = await CallRepository(db, tenant.id).get(live.call_id)
        assert stored is not None
        assert stored.outcome == "booked"
        summary = await CallRepository(db, tenant.id).summary(live.call_id)
        assert summary is not None
        assert summary.follow_up_required is False
    finally:
        await db.close()


def test_voice_status_does_not_expose_secrets(client, monkeypatch):
    from types import SimpleNamespace

    from apps.api.routes import voice as voice_routes

    token = register(
        client,
        name="Status Salon",
        slug="status-salon",
        email="status@example.com",
    )
    monkeypatch.setattr(
        voice_routes,
        "settings",
        SimpleNamespace(
            deepgram_api_key="secret-deepgram",
            llm_provider="openrouter",
            groq_api_key="secret-groq",
            openrouter_api_key="secret-openrouter",
            voice_tenant_slug="status-salon",
            groq_model="llama-test",
            openrouter_model="openrouter/free",
        ),
    )
    response = client.get("/api/v1/voice/status", headers=auth(token))
    assert response.status_code == 200
    data = response.json()
    assert data["browser_voice_ready"] is True
    assert data["llm_provider"] == "openrouter"
    assert data["llm_model"] == "openrouter/free"
    assert data["llm_configured"] is True
    assert "secret-deepgram" not in response.text
    assert "secret-groq" not in response.text
    assert "secret-openrouter" not in response.text


def test_business_hours_can_be_configured_and_read(client):
    token = register(
        client,
        name="Hours Salon",
        slug="hours-salon",
        email="hours@example.com",
    )
    payload = [
        {"day_of_week": 0, "opens_at": "09:00", "closes_at": "17:30", "is_closed": False},
        {"day_of_week": 1, "opens_at": "10:00", "closes_at": "18:00", "is_closed": False},
        {"day_of_week": 2, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 3, "opens_at": "09:00", "closes_at": "17:30", "is_closed": False},
        {"day_of_week": 4, "opens_at": "09:00", "closes_at": "17:30", "is_closed": False},
        {"day_of_week": 5, "opens_at": "09:00", "closes_at": "14:00", "is_closed": False},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    saved = client.put("/api/v1/salon/hours", headers=auth(token), json=payload)
    assert saved.status_code == 200, saved.text
    rows = saved.json()
    assert len(rows) == 7
    assert rows[0]["opens_at"] == "09:00:00"
    assert rows[2]["is_closed"] is True

    loaded = client.get("/api/v1/salon/hours", headers=auth(token))
    assert loaded.status_code == 200
    assert loaded.json() == rows


async def test_voice_availability_distinguishes_calendar_conflict_from_closed(client, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Reason Salon",
        slug="reason-salon",
        email="reason@example.com",
    )
    add_service(client, token, "Haircut", 45)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    saved = client.put("/api/v1/salon/hours", headers=auth(token), json=hours)
    assert saved.status_code == 200, saved.text

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("reason-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-reason-test",
            caller_number="0214444444",
            called_number=tenant.phone_number,
        )
        tools = VoiceToolset(db, tenant, live)

        tz = ZoneInfo("Pacific/Auckland")
        now_local = datetime.now(tz)
        days_until_monday = (7 - now_local.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        monday_2pm = (now_local + timedelta(days=days_until_monday)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )

        async def busy_calendar(*, starts_at, service_id, staff_member_id=None):
            return False, starts_at + timedelta(minutes=45)

        monkeypatch.setattr(tools.booking, "availability", busy_calendar)
        busy = await tools.check_availability("Haircut", monday_2pm.isoformat())
        assert busy["available"] is False
        assert busy["calendar_available"] is False
        assert busy["within_business_hours"] is True
        assert busy["availability_reason"] == "calendar_conflict"
        assert busy["business_hours_for_day"]["opens_at"] == "09:00"
        assert busy["business_hours_for_day"]["closes_at"] == "17:00"

        monday_6pm = monday_2pm.replace(hour=18)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=45)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)
        closed = await tools.check_availability("Haircut", monday_6pm.isoformat())
        assert closed["available"] is False
        assert closed["calendar_available"] is True
        assert closed["within_business_hours"] is False
        assert closed["availability_reason"] == "outside_business_hours"
    finally:
        await db.close()


async def test_voice_datetime_guard_corrects_weekday_from_caller_phrase(client):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Date Guard Salon",
        slug="date-guard-salon",
        email="dateguard@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    assert client.put("/api/v1/salon/hours", headers=auth(token), json=hours).status_code == 200

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("date-guard-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-date-guard-test",
            caller_number="0270000000",
            called_number=tenant.phone_number,
        )
        tools = VoiceToolset(db, tenant, live)
        tz = ZoneInfo("Pacific/Auckland")
        now_local = datetime.now(tz)
        days_until_friday = (4 - now_local.weekday()) % 7 or 7
        friday = (now_local + timedelta(days=days_until_friday)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        result = await tools.check_availability(
            "Men's Cut", friday.isoformat(), caller_time_phrase="Monday at 10 am"
        )
        assert result["ok"] is True
        assert result["datetime_source"] == "caller_phrase"
        assert result["weekday"] == "Monday"
        assert result["local_time"] == "10:00"
    finally:
        await db.close()


async def test_voice_datetime_guard_rejects_am_pm_mismatch(client):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Meridiem Guard Salon",
        slug="meridiem-guard-salon",
        email="meridiem@example.com",
    )
    add_service(client, token, "Haircut", 30)

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("meridiem-guard-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-meridiem-guard-test",
            caller_number="0270000001",
            called_number=tenant.phone_number,
        )
        tools = VoiceToolset(db, tenant, live)
        tz = ZoneInfo("Pacific/Auckland")
        two_am = (datetime.now(tz) + timedelta(days=2)).replace(
            hour=2, minute=0, second=0, microsecond=0
        )
        result = await tools.check_availability(
            "Haircut", two_am.isoformat(), caller_time_phrase="at 2 pm"
        )
        assert result["ok"] is False
        assert result["code"] == "TIME_MERIDIEM_MISMATCH"
        assert result["local_time"] == "02:00"
    finally:
        await db.close()


async def test_voice_backend_resolves_next_weekday_without_llm_calendar_math(client, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Resolver Salon",
        slug="resolver-salon",
        email="resolver@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    assert client.put("/api/v1/salon/hours", headers=auth(token), json=hours).status_code == 200

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("resolver-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-resolver-test",
            caller_number="0270000002",
            called_number=tenant.phone_number,
        )
        tools = VoiceToolset(db, tenant, live)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=30)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)

        # Deliberately give the tool a wrong LLM-generated ISO value. The original
        # caller phrase must win and be resolved deterministically by the backend.
        wrong_iso = (
            (datetime.now(ZoneInfo("Pacific/Auckland")) + timedelta(days=3))
            .replace(hour=2, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        result = await tools.check_availability(
            "Men's Cut",
            wrong_iso,
            caller_time_phrase="next Monday at 2 pm",
        )
        assert result["ok"] is True
        assert result["datetime_source"] == "caller_phrase"
        assert result["weekday"] == "Monday"
        assert result["local_time"] == "14:00"
        assert result["available"] is True

        tz = ZoneInfo("Pacific/Auckland")
        now_local = datetime.now(tz)
        days_until_monday = (0 - now_local.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        expected = (now_local + timedelta(days=days_until_monday)).date().isoformat()
        assert result["local_date"] == expected
    finally:
        await db.close()


async def test_voice_backend_rejects_explicit_weekday_date_contradiction(client):
    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Contradiction Salon",
        slug="contradiction-salon",
        email="contradiction@example.com",
    )
    add_service(client, token, "Men's Cut", 30)

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("contradiction-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-contradiction-test",
            caller_number="0270000003",
            called_number=tenant.phone_number,
        )
        tools = VoiceToolset(db, tenant, live)
        result = await tools.check_availability(
            "Men's Cut",
            "2026-08-15T14:00",
            caller_time_phrase="Monday 15 August 2026 at 2 pm",
        )
        assert result["ok"] is False
        assert result["code"] == "DATE_WEEKDAY_MISMATCH"
        assert result["weekday"] == "Saturday"
        assert result["local_date"] == "2026-08-15"
    finally:
        await db.close()


async def test_voice_datetime_recovers_latest_caller_phrase_when_llm_omits_it(client, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Transcript Resolver Salon",
        slug="transcript-resolver-salon",
        email="transcriptresolver@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    assert client.put("/api/v1/salon/hours", headers=auth(token), json=hours).status_code == 200

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("transcript-resolver-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-transcript-resolver-test",
            caller_number="0270000004",
            called_number=tenant.phone_number,
        )
        await live.start()
        await live.append_transcript("caller", "2PM, Monday.")
        tools = VoiceToolset(db, tenant, live)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=30)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)

        # Simulate a small LLM omitting caller_time_phrase and guessing a bad ISO date.
        result = await tools.check_availability(
            "Men's Cut",
            "2026-08-15T14:00",
            caller_time_phrase=None,
        )
        assert result["ok"] is True
        assert result["datetime_source"] == "caller_phrase"
        assert result["caller_time_phrase"] == "2PM, Monday."
        assert result["weekday"] == "Monday"
        assert result["local_time"] == "14:00"

        tz = ZoneInfo("Pacific/Auckland")
        now_local = datetime.now(tz)
        days_until_monday = (0 - now_local.weekday()) % 7
        if days_until_monday == 0 and now_local.hour >= 14:
            days_until_monday = 7
        expected = (now_local + timedelta(days=days_until_monday)).date().isoformat()
        assert result["local_date"] == expected
    finally:
        await db.close()


async def test_voice_datetime_composes_day_and_spoken_time_across_turns(client, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Multi Turn Time Salon",
        slug="multi-turn-time-salon",
        email="multiturn@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    assert client.put("/api/v1/salon/hours", headers=auth(token), json=hours).status_code == 200

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("multi-turn-time-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-multi-turn-time-test",
            caller_number="0270000005",
            called_number=tenant.phone_number,
        )
        await live.start()
        await live.append_transcript("caller", "Can I have a means cut for Monday?")
        await live.append_transcript("caller", "Two PM.")
        tools = VoiceToolset(db, tenant, live)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=30)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)

        # The LLM guesses a Saturday ISO value and passes only the latest time fragment.
        # The backend must combine Monday from the previous caller turn with Two PM.
        result = await tools.check_availability(
            "means cut",
            "2026-08-15T14:00",
            caller_time_phrase="Two PM.",
        )
        assert result["ok"] is True
        assert result["datetime_source"] == "caller_phrase"
        assert result["weekday"] == "Monday"
        assert result["local_time"] == "14:00"
        assert "Monday" in result["caller_time_phrase"]
        assert "Two PM" in result["caller_time_phrase"]
        assert result["service"] == "Men's Cut"

        tz = ZoneInfo("Pacific/Auckland")
        now_local = datetime.now(tz)
        days_until_monday = (0 - now_local.weekday()) % 7
        if days_until_monday == 0 and now_local.hour >= 14:
            days_until_monday = 7
        expected = (now_local + timedelta(days=days_until_monday)).date().isoformat()
        assert result["local_date"] == expected
    finally:
        await db.close()


async def test_voice_service_fuzzy_match_is_conservative(client):
    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Service Fuzzy Salon",
        slug="service-fuzzy-salon",
        email="servicefuzzy@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    add_service(client, token, "Women's Cut", 45)

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("service-fuzzy-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-service-fuzzy-test",
            caller_number="0270000006",
            called_number=tenant.phone_number,
        )
        tools = VoiceToolset(db, tenant, live)

        resolved, choices = await tools._resolve_service("means cut")
        assert resolved is not None
        assert resolved.name == "Men's Cut"
        assert choices is None

        unresolved, choices = await tools._resolve_service("a cut")
        assert unresolved is None
        assert "Men's Cut" in choices
        assert "Women's Cut" in choices
    finally:
        await db.close()


async def test_voice_bare_oclock_infers_unique_meridiem_from_business_hours(client, monkeypatch):
    from datetime import timedelta

    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Meridiem Salon",
        slug="meridiem-inference-salon",
        email="meridiem-inference@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    assert client.put("/api/v1/salon/hours", headers=auth(token), json=hours).status_code == 200

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("meridiem-inference-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-meridiem-test",
            caller_number="0270000006",
            called_number=tenant.phone_number,
        )
        await live.start()
        await live.append_transcript("caller", "Can I book a men's cut for next Monday?")
        await live.append_transcript("caller", "Two o'clock.")
        tools = VoiceToolset(db, tenant, live)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=30)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)
        result = await tools.check_availability(
            "Men's Cut",
            "2026-08-17T02:00",
            caller_time_phrase="Two o'clock.",
        )
        assert result["ok"] is True
        assert result["weekday"] == "Monday"
        assert result["local_time"] == "14:00"
        assert result["meridiem_inferred_from_business_hours"] is True
        assert result["datetime_source"] == "caller_phrase_business_hours_inferred"
    finally:
        await db.close()


async def test_voice_numeric_02_00_transcript_is_resolved_by_business_hours(client, monkeypatch):
    """ASR may emit '02:00' for spoken 'two o'clock'; do not force 2 AM."""
    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Numeric Clock Salon",
        slug="numeric-clock-salon",
        email="numeric-clock@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    assert client.put("/api/v1/salon/hours", headers=auth(token), json=hours).status_code == 200

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("numeric-clock-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-numeric-clock-test",
            caller_number="0270000016",
            called_number=tenant.phone_number,
        )
        await live.start()
        await live.append_transcript("caller", "Can I book a men's cut for Monday, 02:00?")
        tools = VoiceToolset(db, tenant, live)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=30)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)
        result = await tools.check_availability(
            "Men's Cut",
            "2026-08-17T02:00",
            caller_time_phrase="Monday, 02:00",
        )
        assert result["ok"] is True
        assert result["weekday"] == "Monday"
        assert result["local_time"] == "14:00"
        assert result["within_business_hours"] is True
        assert result["meridiem_inferred_from_business_hours"] is True
        assert result["datetime_source"] == "caller_phrase_business_hours_inferred"
    finally:
        await db.close()


async def test_voice_24_hour_13_00_remains_unambiguous(client, monkeypatch):
    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    token = register(
        client,
        name="Twenty Four Hour Salon",
        slug="twenty-four-hour-salon",
        email="twenty-four-hour@example.com",
    )
    add_service(client, token, "Men's Cut", 30)
    hours = [
        {"day_of_week": day, "opens_at": "09:00", "closes_at": "17:00", "is_closed": False}
        for day in range(5)
    ] + [
        {"day_of_week": 5, "opens_at": None, "closes_at": None, "is_closed": True},
        {"day_of_week": 6, "opens_at": None, "closes_at": None, "is_closed": True},
    ]
    assert client.put("/api/v1/salon/hours", headers=auth(token), json=hours).status_code == 200

    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("twenty-four-hour-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="voice-24h-clock-test",
            caller_number="0270000017",
            called_number=tenant.phone_number,
        )
        await live.start()
        tools = VoiceToolset(db, tenant, live)

        async def free_calendar(*, starts_at, service_id, staff_member_id=None):
            return True, starts_at + timedelta(minutes=30)

        monkeypatch.setattr(tools.booking, "availability", free_calendar)
        result = await tools.check_availability(
            "Men's Cut",
            "2026-08-17T13:00",
            caller_time_phrase="Monday, 13:00",
        )
        assert result["ok"] is True
        assert result["local_time"] == "13:00"
        assert result["within_business_hours"] is True
        assert result["meridiem_inferred_from_business_hours"] is False
    finally:
        await db.close()


def test_service_name_cleanup_endpoint_is_conservative(client):
    token = register(
        client,
        name="Naming Salon",
        slug="naming-salon",
        email="naming@example.com",
    )
    common = add_service(client, token, "mens Cuts", 30)
    custom = add_service(client, token, "Balayage Deluxe", 120)
    assert common["name"] == "Men's Cut"
    assert custom["name"] == "Balayage Deluxe"

    response = client.post("/api/v1/salon/services/normalise-names", headers=auth(token))
    assert response.status_code == 200
    names = {row["name"] for row in response.json()}
    assert "Men's Cut" in names
    assert "Balayage Deluxe" in names
