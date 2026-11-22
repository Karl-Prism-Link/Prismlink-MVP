from tests.test_mvp import auth, register


def test_salon_business_profile_fields_round_trip(client):
    token = register(
        client, name="Luxe Hair & Beauty", slug="luxe-profile", email="owner@luxe.example"
    )
    headers = auth(token)
    payload = {
        "phone_number": "09 555 0100",
        "address": "123 Queen Street, Auckland 1010",
        "contact_email": "owner@example.com",
        "timezone": "Pacific/Auckland",
        "currency": "NZD",
        "prices_include_gst": True,
        "ai_disclosure": "You are speaking with the salon’s AI receptionist.",
        "walk_ins_policy": "Accepted when capacity permits; appointments are recommended.",
        "parking_info": "Paid street parking and a public car park nearby.",
        "accessibility_info": "Step-free front entrance. Ask staff about treatment-room access.",
    }
    saved = client.patch("/api/v1/salon", headers=headers, json=payload)
    assert saved.status_code == 200, saved.text
    data = saved.json()
    for key, value in payload.items():
        assert data[key] == value
    fetched = client.get("/api/v1/salon", headers=headers)
    assert fetched.status_code == 200
    for key, value in payload.items():
        assert fetched.json()[key] == value
