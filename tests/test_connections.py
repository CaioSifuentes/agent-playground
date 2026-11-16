"""Connection lifecycle, token storage and seeding."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from tests.conftest import connect_agent


def test_connect_returns_token_expiry_and_cookie(client, connect, database):
    data, response = connect("MyAssistant")

    assert response.status_code == 201
    assert data["agent_name"] == "MyAssistant"
    assert data["connection_id"]
    assert len(data["token"]) >= 40
    expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    expected = datetime.now(timezone.utc) + timedelta(hours=24)
    assert abs((expires - expected).total_seconds()) < 30

    set_cookie = response.headers["set-cookie"].lower()
    assert "agent_token=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie

    raw = database.raw_connections.find_one({"_id": ObjectId(data["connection_id"])})
    assert raw is not None
    assert "token" not in raw
    assert raw["token_hash"] == hashlib.sha256(data["token"].encode()).hexdigest()
    assert raw["revoked_at"] is None


def test_connect_seeds_example_tasks(client, agent):
    _, headers = agent("Seeder")
    response = client.get("/api/tasks", headers=headers)
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert len(tasks) == 3
    assert {task["priority"] for task in tasks} == {"low", "medium", "high"}
    assert {task["status"] for task in tasks} == {
        "pending",
        "in_progress",
        "completed",
    }
    for task in tasks:
        assert task["id"]
        assert task["created_at"]
        assert task["updated_at"]


def test_same_name_creates_independent_connections(client):
    first, _ = connect_agent(client, "Twin")
    second, _ = connect_agent(client, "Twin")

    assert first["connection_id"] != second["connection_id"]
    assert first["token"] != second["token"]

    first_headers = {"Authorization": f"Bearer {first['token']}"}
    second_headers = {"Authorization": f"Bearer {second['token']}"}

    created = client.post(
        "/api/tasks",
        headers=first_headers,
        json={"title": "Only for the first twin"},
    )
    assert created.status_code == 201

    first_tasks = client.get("/api/tasks", headers=first_headers).json()["tasks"]
    second_tasks = client.get("/api/tasks", headers=second_headers).json()["tasks"]
    assert "Only for the first twin" in {task["title"] for task in first_tasks}
    assert "Only for the first twin" not in {task["title"] for task in second_tasks}

    # The browser cookie belongs to the most recent connection.
    me = client.get("/api/me").json()
    assert me["connection_id"] == second["connection_id"]


def test_disconnect_revokes_token_preserves_tasks_and_clears_cookie(
    client, connect, database
):
    data, _ = connect("Leaver")
    headers = {"Authorization": f"Bearer {data['token']}"}
    tasks_before = client.get("/api/tasks", headers=headers).json()["tasks"]
    assert len(tasks_before) == 3

    disconnect = client.post("/disconnect", headers=headers)
    assert disconnect.status_code == 200
    assert disconnect.json()["status"] == "disconnected"
    assert disconnect.json()["connection_id"] == data["connection_id"]
    assert "agent_token=" in disconnect.headers["set-cookie"].lower()

    assert client.get("/api/tasks", headers=headers).status_code == 401
    assert client.get("/api/me", headers=headers).status_code == 401

    # Tasks must survive the connection revocation.
    assert database.raw_tasks.count_documents(
        {"connection_id": ObjectId(data["connection_id"])}
    ) == 3
    raw = database.raw_connections.find_one({"_id": ObjectId(data["connection_id"])})
    assert raw["revoked_at"] is not None


def test_disconnect_using_cookie_clears_it(client, connect):
    connect("CookieAgent")
    assert client.get("/api/tasks").status_code == 200

    response = client.post("/disconnect")
    assert response.status_code == 200
    assert "agent_token=" in response.headers["set-cookie"].lower()
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.get("/api/tasks").status_code == 401


def test_invalid_agent_names_are_rejected(client):
    for name in ["<script>", "  ", "a/b"]:
        response = client.post(f"/connect/{name}")
        assert response.status_code in {400, 404}, (name, response.text)


def test_expired_connection_is_hidden_and_rejected(client, agent, database, dashboard):
    data, headers = agent("Expiring")
    database.raw_connections.update_one(
        {"_id": ObjectId(data["connection_id"])},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}},
    )

    assert client.get("/api/tasks", headers=headers).status_code == 401
    agents = client.get("/api/dashboard/agents").json()["agents"]
    assert data["connection_id"] not in {agent["connection_id"] for agent in agents}
