"""Public pages, health and OpenAPI documentation."""

from __future__ import annotations


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_frontend_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Agent Playground" in response.text
    assert "/static/app.js" in response.text
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_openapi_documents_the_api(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()

    assert spec["info"]["title"] == "Agent Playground"
    paths = spec["paths"]
    for path in [
        "/connect/{name}",
        "/disconnect",
        "/api/me",
        "/api/tasks",
        "/api/tasks/{task_id}",
        "/api/tasks/{task_id}/complete",
        "/api/dashboard/agents",
        "/api/dashboard/agents/{agent_id}/tasks",
        "/api/dashboard/agents/{agent_id}/disconnect",
        "/api/dashboard/activity",
        "/api/dashboard/events",
        "/health",
    ]:
        assert path in paths

    schemes = spec["components"]["securitySchemes"]
    assert "AgentToken" in schemes
    assert schemes["AgentToken"]["type"] == "http"
    assert schemes["AgentToken"]["scheme"] == "bearer"
    assert "DashboardSession" in schemes

    create_task = paths["/api/tasks"]["post"]
    assert create_task["responses"]["201"]["description"]
    assert "401" in create_task["responses"]
    assert "422" in create_task["responses"]

    connect = paths["/connect/{name}"]["post"]
    connect_schema = connect["responses"]["201"]["content"]["application/json"]["schema"]
    assert connect_schema.get("$ref", "").endswith("/ConnectResponse")
    assert "token" in spec["components"]["schemas"]["ConnectResponse"]["properties"]

    tags = {tag["name"] for tag in spec["tags"]}
    assert tags == {"Authentication", "Tasks", "Dashboard", "Public"}
    assert spec["info"]["description"]


def test_docs_pages_load(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
