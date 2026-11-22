from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.schemas import TenantPatch
from services.voice.salon_enquiries import price_speech


def test_tenant_patch_rejects_explicit_null_for_currency_and_gst():
    with pytest.raises(ValidationError):
        TenantPatch(currency=None)

    with pytest.raises(ValidationError):
        TenantPatch(prices_include_gst=None)


def test_price_speech_includes_gst_status_without_recalculating_price():
    assert (
        price_speech("Men's Cut", 4500, "NZD", prices_include_gst=True)
        == "The Men's Cut is 45 dollars NZD including GST."
    )
    assert (
        price_speech("Men's Cut", 4500, "NZD", prices_include_gst=False)
        == "The Men's Cut is 45 dollars NZD."
    )


def test_voice_price_tool_result_exposes_human_units_and_gst(client):
    from database.repositories import TenantRepository
    from database.session import DBSession
    from services.voice.session import LiveCallSession
    from services.voice.tools import VoiceToolset

    from tests.test_mvp import add_service, register

    token = register(
        client,
        name="Price Tool Salon",
        slug="price-tool-salon",
        email="price-tool@example.com",
    )
    profile = client.patch(
        "/api/v1/salon",
        headers={"Authorization": f"Bearer {token}"},
        json={"currency": "NZD", "prices_include_gst": True},
    )
    assert profile.status_code == 200, profile.text
    service = add_service(client, token, "Men's Cut", 30)
    updated = client.patch(
        f"/api/v1/salon/services/{service['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"price": 4500},
    )
    assert updated.status_code == 200, updated.text

    db = DBSession()
    try:
        tenant = __import__("asyncio").run(TenantRepository(db).by_slug("price-tool-salon"))
        assert tenant is not None
        live = LiveCallSession(
            db,
            tenant_id=tenant.id,
            external_call_id="price-tool-regression",
            caller_number="0270000001",
            called_number=tenant.phone_number,
        )
        tools = VoiceToolset(db, tenant, live)
        result = __import__("asyncio").run(tools.get_salon_information("How much is a men's cut?"))
        item = next(row for row in result["services"] if row["name"] == "Men's Cut")
        assert item["price_cents"] == 4500
        assert item["price"] == "45.00 NZD"
        assert item["prices_include_gst"] is True
    finally:
        __import__("asyncio").run(db.close())
