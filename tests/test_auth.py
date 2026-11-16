"""Authentication rules: 401s, cookie auth, revocation and expiry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from tests.conftest import connect_agent


def test_task_endpoints_require_authentication(client):
    cases = [
        ("GET", "/api/tasks"),
        ("GET", "/api/tasks/0123456789abcdef01234567"),
        ("POST", "/api/tasks"),
        ("PATCH", "/api/tasks/0123456789abcdef01234567"),
        ("DELETE", "/api/tasks/0123456789abcdef01234567"),
        ("POST", "/api/tasks/0123456789abcdef01234567/complete"),
    ]
    for method, path in cases:
        response = client.request(method, path)
        assert response.status_code == 401, (method, path, response.text)
        assert response.headers.get("www-authenticate") == "Bearer"


def test_invalid_bearer_does_not_fall_back_to_valid_cookie(client, connect):
    connect("CookieOwner")
    assert client.get("/api/tasks").status_code == 200
    response = client.get(
        "/api/tasks", headers={"Authorization": "Bearer definitely-not-valid"}
    )
    assert response.status_code == 401


def test_agent_cookie_authenticates_browser_sessions(client, connect):
    data, _ = connect("BrowserAgent")
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["connection_id"] == data["connection_id"]

    created = client.post("/api/tasks", json={"title": "From the cookie"})
    assert created.status_code == 201


def test_expired_token_is_rejected(client, agent, database):
    data, headers = agent("Expired")
    database.raw_connections.update_one(
        {"_id": ObjectId(data["connection_id"])},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}},
    )
    response = client.get("/api/tasks", headers=headers)
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_revoked_token_is_rejected(client, agent):
    data, headers = agent("Revoked")
    assert client.post("/disconnect", headers=headers).status_code == 200
    assert client.get("/api/tasks", headers=headers).status_code == 401


def test_unknown_token_is_rejected(client):
    response = client.get("/api/tasks", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid, expired, or revoked token"


def test_dashboard_session_is_not_an_agent_token(client, dashboard):
    # Logged into the dashboard, but no agent connection in this browser.
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_dashboard_requires_its_own_session(client, agent):
    _, headers = agent("DashboardNoAccess")
    response = client.get("/api/dashboard/agents", headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "Dashboard authentication required"


def test_dashboard_does_not_expose_tokens(client, dashboard):
    data, _ = connect_agent(client, "Secretive")
    agents = client.get("/api/dashboard/agents").json()["agents"]
    serialized = str(agents)
    assert data["token"] not in serialized
    assert "token_hash" not in serialized
