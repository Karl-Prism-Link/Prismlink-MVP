from database.repositories import MessageRepository, TenantRepository
from database.session import DBSession
from services.voice.session import LiveCallSession
from services.voice.tools import VoiceToolset


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


def test_message_inbox_status_and_transcript_are_tenant_scoped(client):
    token = register(
        client,
        name="Inbox Salon",
        slug="inbox-salon",
        email="inbox@example.com",
    )
    other = register(
        client,
        name="Other Inbox",
        slug="other-inbox",
        email="other-inbox@example.com",
    )

    simulated = client.post(
        "/api/v1/calls/simulate",
        headers=auth(token),
        json={
            "caller_number": "0215555555",
            "utterance": "Please take a message and call me back",
            "customer_name": "Tom",
            "customer_phone": "0215555555",
        },
    )
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["call"]["outcome"] == "message_taken"

    inbox = client.get("/api/v1/messages", headers=auth(token))
    assert inbox.status_code == 200, inbox.text
    rows = inbox.json()
    assert len(rows) == 1
    message = rows[0]
    assert message["customer_name"] == "Tom"
    assert message["callback_phone"] == "0215555555"
    assert message["status"] == "new"

    transcript = client.get(f"/api/v1/messages/{message['id']}/transcript", headers=auth(token))
    assert transcript.status_code == 200, transcript.text
    assert [row["speaker"] for row in transcript.json()] == ["caller", "assistant"]

    updated = client.patch(
        f"/api/v1/messages/{message['id']}",
        headers=auth(token),
        json={"status": "contacted"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "contacted"

    hidden = client.get(f"/api/v1/messages/{message['id']}/transcript", headers=auth(other))
    assert hidden.status_code == 404


def test_message_status_rejects_unknown_state(client):
    token = register(
        client,
        name="Message State Salon",
        slug="message-state-salon",
        email="message-state@example.com",
    )
    simulated = client.post(
        "/api/v1/calls/simulate",
        headers=auth(token),
        json={
            "caller_number": "0216666666",
            "utterance": "Please take a message",
            "customer_name": "Alex",
            "customer_phone": "0216666666",
        },
    )
    assert simulated.status_code == 200
    message_id = client.get("/api/v1/messages", headers=auth(token)).json()[0]["id"]
    rejected = client.patch(
        f"/api/v1/messages/{message_id}",
        headers=auth(token),
        json={"status": "deleted"},
    )
    assert rejected.status_code == 422


async def test_live_take_message_persists_inbox_record(client):
    register(
        client,
        name="Live Message Salon",
        slug="live-message-salon",
        email="live-message@example.com",
    )
    db = DBSession()
    try:
        tenant = await TenantRepository(db).by_slug("live-message-salon")
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="live-message-001",
            caller_number="0217777777",
            called_number=tenant.phone_number,
        )
        await live.start()
        await live.append_transcript("caller", "I cannot remember the booking number")
        tools = VoiceToolset(db, tenant, live)
        prepared = await tools.take_message(
            "Please call me about cancelling my appointment",
            "0218888888",
            "Tom",
            False,
        )
        assert prepared["ok"] is True
        assert prepared["ready_to_confirm"] is True
        assert await MessageRepository(db, tenant.id).list() == []

        await live.append_transcript("caller", "Yes")
        result = await tools.take_message("", "", None, True)
        assert result["ok"] is True
        assert result["submitted"] is True
        assert result["message_status"] == "new"

        rows = await MessageRepository(db, tenant.id).list()
        assert len(rows) == 1
        assert rows[0].customer_name == "Tom"
        assert rows[0].callback_phone == "0218888888"
        assert rows[0].message_text == "Please call me about cancelling my appointment"
        assert rows[0].status == "new"
    finally:
        await db.close()
