"""Dashboard login, read-only views and separate-session rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from tests.conftest import connect_agent


def test_dashboard_login_session_and_logout(client):
    assert client.get("/api/dashboard/session").json() == {"authenticated": False}
    assert client.get("/api/dashboard/agents").status_code == 401

    wrong = client.post(
        "/api/dashboard/login", json={"password": "not-the-password"}
    )
    assert wrong.status_code == 401
    assert "set-cookie" not in wrong.headers

    login = client.post(
        "/api/dashboard/login", json={"password": "test-dashboard-password"}
    )
    assert login.status_code == 200
    assert login.json() == {"authenticated": True}
    cookie = login.headers["set-cookie"].lower()
    assert "dashboard_session=" in cookie
    assert "httponly" in cookie

    assert client.get("/api/dashboard/session").json() == {"authenticated": True}
    assert client.get("/api/dashboard/agents").status_code == 200

    logout = client.post("/api/dashboard/logout")
    assert logout.status_code == 200
    assert logout.json() == {"authenticated": False}
    assert client.get("/api/dashboard/session").json() == {"authenticated": False}
    assert client.get("/api/dashboard/agents").status_code == 401


def test_dashboard_lists_only_active_agents(client, dashboard, connect, database):
    first, _ = connect("First")
    second, _ = connect("Second")

    database.raw_connections.update_one(
        {"_id": ObjectId(second["connection_id"])},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}},
    )

    response = client.get("/api/dashboard/agents")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["agents"][0]["connection_id"] == first["connection_id"]
    assert body["agents"][0]["agent_name"] == "First"
    assert "token" not in body["agents"][0]
    assert "token_hash" not in body["agents"][0]


def test_dashboard_can_read_each_agents_tasks(client, dashboard, agent):
    first, _ = agent("Alpha")
    second, _ = agent("Beta")

    client.post("/api/tasks", headers={"Authorization": f"Bearer {first['token']}"}, json={"title": "Alpha task"})
    client.post("/api/tasks", headers={"Authorization": f"Bearer {second['token']}"}, json={"title": "Beta task"})

    alpha = client.get(f"/api/dashboard/agents/{first['connection_id']}/tasks")
    beta = client.get(f"/api/dashboard/agents/{second['connection_id']}/tasks")

    assert alpha.status_code == 200
    assert beta.status_code == 200
    alpha_titles = {task["title"] for task in alpha.json()["tasks"]}
    beta_titles = {task["title"] for task in beta.json()["tasks"]}
    assert "Alpha task" in alpha_titles
    assert "Alpha task" not in beta_titles
    assert "Beta task" in beta_titles
    assert "Beta task" not in alpha_titles

    assert alpha.json()["count"] == 4
    assert beta.json()["count"] == 4


def test_dashboard_unknown_agent_and_invalid_id(client, dashboard):
    assert client.get("/api/dashboard/agents/not-an-id/tasks").status_code == 404
    assert (
        client.get("/api/dashboard/agents/0123456789abcdef01234567/tasks").status_code
        == 404
    )


def test_dashboard_can_disconnect_any_agent(client, dashboard, agent, database):
    data, headers = agent("ExternalToDisconnect")

    response = client.post(
        f"/api/dashboard/agents/{data['connection_id']}/disconnect"
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "disconnected"
    assert response.json()["connection_id"] == data["connection_id"]

    # Token is revoked immediately, but the task environment is preserved.
    assert client.get("/api/tasks", headers=headers).status_code == 401
    assert (
        database.raw_tasks.count_documents(
            {"connection_id": ObjectId(data["connection_id"])}
        )
        == 3
    )

    agents = client.get("/api/dashboard/agents").json()["agents"]
    assert data["connection_id"] not in {
        agent["connection_id"] for agent in agents
    }


def test_dashboard_disconnect_unknown_agent(client, dashboard):
    response = client.post(
        "/api/dashboard/agents/0123456789abcdef01234567/disconnect"
    )
    assert response.status_code == 404


def test_agent_token_cannot_disconnect_via_dashboard(client, agent):
    data, headers = agent("NoDashboardAccess")
    response = client.post(
        f"/api/dashboard/agents/{data['connection_id']}/disconnect",
        headers=headers,
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Dashboard authentication required"
    assert client.get("/api/me", headers=headers).status_code == 200


def test_dashboard_has_no_task_mutation_routes(client, dashboard, connect):
    data, _ = connect("ReadOnly")
    task = client.get(f"/api/dashboard/agents/{data['connection_id']}/tasks").json()[
        "tasks"
    ][0]

    # There are no dashboard mutation routes; task writes require an agent token.
    assert (
        client.post(
            f"/api/dashboard/agents/{data['connection_id']}/tasks",
            json={"title": "nope"},
        ).status_code
        == 405
    )
    assert (
        client.patch(
            f"/api/dashboard/agents/{data['connection_id']}/tasks/{task['id']}",
            json={"title": "nope"},
        ).status_code
        in {404, 405}
    )


def test_dashboard_cannot_be_reached_with_agent_token(client):
    data, _ = connect_agent(client, "TokenOnly")
    response = client.get(
        "/api/dashboard/agents",
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert response.status_code == 401
