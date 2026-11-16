"""Activity logging: field coverage, sanitization, filters and failures."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from app.middleware.activity import build_request_summary


def _entries(client, query: str = "") -> list[dict]:
    response = client.get(f"/api/dashboard/activity{query}")
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def test_activity_records_connect_and_task_operations(client, dashboard, agent, database):
    connection, headers = agent("Audited")

    created = client.post(
        "/api/tasks",
        headers=headers,
        json={"title": "Audit me", "priority": "high"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    listed = client.get("/api/tasks", headers=headers)
    assert listed.status_code == 200
    fetched = client.get(f"/api/tasks/{task_id}", headers=headers)
    assert fetched.status_code == 200
    me = client.get("/api/me", headers=headers)
    assert me.status_code == 200

    entries = _entries(client, "?limit=200")
    assert entries, "connect and task calls should be logged"

    by_path = {}
    for entry in entries:
        by_path.setdefault((entry["method"], entry["path"]), []).append(entry)

    connect_entry = by_path[("POST", "/connect/Audited")][0]
    assert connect_entry["agent_name"] == "Audited"
    assert connect_entry["connection_id"] == connection["connection_id"]
    assert connect_entry["status_code"] == 201
    assert connect_entry["success"] is True
    assert connect_entry["request_summary"] == ""

    create_entry = by_path[("POST", "/api/tasks")][0]
    assert create_entry["agent_name"] == "Audited"
    assert create_entry["connection_id"] == connection["connection_id"]
    assert create_entry["status_code"] == 201
    assert create_entry["success"] is True
    assert create_entry["duration_ms"] >= 0
    assert "Audit me" in create_entry["request_summary"]
    assert "high" in create_entry["request_summary"]
    assert create_entry["timestamp"]

    list_entry = by_path[("GET", "/api/tasks")][0]
    assert list_entry["success"] is True
    assert list_entry["status_code"] == 200

    me_entry = by_path[("GET", "/api/me")][0]
    assert me_entry["agent_name"] == "Audited"
    assert me_entry["success"] is True

    # The raw token must never be persisted anywhere in the activity document.
    raw_documents = list(database.raw_activity.find({}))
    serialized = json.dumps(raw_documents, default=str)
    assert connection["token"] not in serialized
    assert headers["Authorization"] not in serialized
    assert task_id in serialized  # paths are fine to record


def test_failed_operations_are_logged(client, dashboard, agent):
    connection, headers = agent("Failure")

    missing = client.get(
        "/api/tasks/0123456789abcdef01234567", headers=headers
    )
    assert missing.status_code == 404

    invalid = client.get(
        "/api/tasks", headers={"Authorization": "Bearer invalid-token"}
    )
    assert invalid.status_code == 401

    entries = _entries(client, "?limit=200")

    not_found = [
        entry
        for entry in entries
        if entry["status_code"] == 404 and entry["path"].startswith("/api/tasks/")
    ]
    assert not_found
    assert not_found[0]["success"] is False
    assert not_found[0]["connection_id"] == connection["connection_id"]
    assert not_found[0]["agent_name"] == "Failure"

    unauthenticated = [
        entry
        for entry in entries
        if entry["status_code"] == 401 and entry["agent_name"] == "unauthenticated"
    ]
    assert unauthenticated
    assert unauthenticated[0]["connection_id"] is None
    assert unauthenticated[0]["success"] is False


def test_validation_failures_are_attributed_to_the_agent(client, dashboard, agent):
    connection, headers = agent("ValidationLog")

    response = client.post(
        "/api/tasks", headers=headers, json={"title": "x", "priority": "urgent"}
    )
    assert response.status_code == 422

    entries = _entries(client, f"?agent_id={connection['connection_id']}&success=false")
    failed_validation = [
        entry
        for entry in entries
        if entry["method"] == "POST"
        and entry["path"] == "/api/tasks"
        and entry["status_code"] == 422
    ]
    assert failed_validation
    assert failed_validation[0]["agent_name"] == "ValidationLog"
    assert "urgent" in failed_validation[0]["request_summary"]


def test_activity_filters_by_agent_and_result(client, dashboard, agent):
    alice, alice_headers = agent("Alice")
    bob, bob_headers = agent("Bob")

    client.post("/api/tasks", headers=alice_headers, json={"title": "Alice work"})
    client.post("/api/tasks", headers=bob_headers, json={"title": "Bob work"})
    client.get("/api/tasks/0123456789abcdef01234567", headers=alice_headers)

    alice_success = _entries(client, f"?agent_id={alice['connection_id']}&success=true")
    assert alice_success
    assert all(entry["connection_id"] == alice["connection_id"] for entry in alice_success)
    assert all(entry["success"] is True for entry in alice_success)

    alice_failures = _entries(
        client, f"?agent_id={alice['connection_id']}&success=false"
    )
    assert alice_failures
    assert all(entry["success"] is False for entry in alice_failures)
    assert all(entry["connection_id"] == alice["connection_id"] for entry in alice_failures)

    bob_entries = _entries(client, f"?agent_id={bob['connection_id']}")
    assert bob_entries
    assert all(entry["connection_id"] == bob["connection_id"] for entry in bob_entries)
    assert not any("Alice work" in entry["request_summary"] for entry in bob_entries)


def test_activity_limit_and_invalid_agent_id(client, dashboard, connect):
    data, _ = connect("LimitCheck")
    headers = {"Authorization": f"Bearer {data['token']}"}
    client.get("/api/tasks", headers=headers)
    client.post("/api/tasks", headers=headers, json={"title": "One more"})

    assert _entries(client, "?limit=1")
    assert len(_entries(client, "?limit=1")) == 1

    response = client.get("/api/dashboard/activity?agent_id=not-an-id")
    assert response.status_code == 400

    response = client.get("/api/dashboard/activity?limit=999")
    assert response.status_code == 422


def test_request_summary_redacts_secrets():
    scope = {
        "path": "/api/tasks",
        "query_string": urlencode(
            {"token": "super-secret-query", "agent_id": "abc"}
        ).encode(),
    }
    body = json.dumps(
        {
            "title": "Hello",
            "password": "hunter2",
            "nested": {"api_key": "top-secret", "authorization": "Bearer abc"},
        }
    ).encode()

    summary = build_request_summary(scope, body)

    assert "super-secret-query" not in summary
    assert "hunter2" not in summary
    assert "top-secret" not in summary
    assert "Bearer abc" not in summary
    assert "[redacted]" in summary
    assert "agent_id=abc" in summary
    assert "Hello" in summary


def test_request_summary_handles_non_json_and_empty_bodies():
    scope = {"path": "/api/tasks", "query_string": b""}
    assert build_request_summary(scope, b"") == ""
    assert build_request_summary(scope, b"not json") == "<non-JSON body>"
