from __future__ import annotations


def _register(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "salon_name": "Dashboard QA Salon",
            "slug": "dashboard-qa-salon",
            "email": "dashboard-qa@example.com",
            "password": "StrongPass123!",
            "timezone": "Pacific/Auckland",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_dashboard_routes_serve_live_and_preview_pages(client):
    live = client.get("/dashboard")
    preview = client.get("/dashboard-preview")
    login_dashboard = client.get("/dashboard-new")

    assert live.status_code == 200
    assert "Live salon dashboard" in live.text
    assert "Create this real appointment" in live.text
    assert preview.status_code == 200
    assert "sample activity" in preview.text
    assert login_dashboard.status_code == 200
    assert "Customer dashboard" in login_dashboard.text


def test_dashboard_service_price_and_archive_restore(client):
    headers = _register(client)
    created = client.post(
        "/api/v1/salon/services",
        headers=headers,
        json={"name": "Cut and finish", "duration_minutes": 60, "price": 7500},
    )
    assert created.status_code == 201, created.text
    service = created.json()
    assert service["price"] == 7500

    edited = client.patch(
        f"/api/v1/salon/services/{service['id']}",
        headers=headers,
        json={"name": "Cut and style", "price": 8250},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Cut and style"
    assert edited.json()["price"] == 8250
    assert edited.json()["duration_minutes"] == 60
    assert edited.json()["is_active"] is True

    archived = client.delete(f"/api/v1/salon/services/{service['id']}", headers=headers)
    assert archived.status_code == 204
    restored = client.patch(
        f"/api/v1/salon/services/{service['id']}",
        headers=headers,
        json={"is_active": True},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_active"] is True
