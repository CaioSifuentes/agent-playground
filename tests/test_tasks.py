"""Task CRUD, validation and status codes."""

from __future__ import annotations


def test_list_seeded_tasks(client, agent):
    _, headers = agent("Lister")
    response = client.get("/api/tasks", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert len(body["tasks"]) == 3
    assert all(
        set(task)
        == {
            "id",
            "title",
            "description",
            "priority",
            "status",
            "created_at",
            "updated_at",
        }
        for task in body["tasks"]
    )


def test_create_get_update_complete_delete_flow(client, agent):
    _, headers = agent("Crud")

    created = client.post(
        "/api/tasks",
        headers=headers,
        json={
            "title": "  Ship the release  ",
            "description": "Cut the tag and announce it.",
            "priority": "high",
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["title"] == "Ship the release"
    assert task["status"] == "pending"
    assert task["priority"] == "high"
    assert task["id"]
    assert created.headers["location"] == f"/api/tasks/{task['id']}"

    fetched = client.get(f"/api/tasks/{task['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Ship the release"

    patched = client.patch(
        f"/api/tasks/{task['id']}",
        headers=headers,
        json={"status": "in_progress", "priority": "low"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "in_progress"
    assert patched.json()["priority"] == "low"
    assert patched.json()["updated_at"] >= task["updated_at"]

    completed = client.post(f"/api/tasks/{task['id']}/complete", headers=headers)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    listed = client.get("/api/tasks", headers=headers).json()
    assert listed["count"] == 4
    assert listed["tasks"][0]["id"] == task["id"]

    deleted = client.delete(f"/api/tasks/{task['id']}", headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(f"/api/tasks/{task['id']}", headers=headers).status_code == 404
    assert client.delete(f"/api/tasks/{task['id']}", headers=headers).status_code == 404


def test_complete_is_idempotent(client, agent):
    _, headers = agent("Completionist")
    tasks = client.get("/api/tasks", headers=headers).json()["tasks"]
    task_id = tasks[0]["id"]

    first = client.post(f"/api/tasks/{task_id}/complete", headers=headers)
    second = client.post(f"/api/tasks/{task_id}/complete", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "completed"


def test_patch_requires_at_least_one_field(client, agent):
    _, headers = agent("EmptyPatch")
    task_id = client.get("/api/tasks", headers=headers).json()["tasks"][0]["id"]

    response = client.patch(f"/api/tasks/{task_id}", headers=headers, json={})
    assert response.status_code == 400
    assert "at least one field" in response.json()["detail"]


def test_patch_rejects_nulls_and_unknown_fields(client, agent):
    _, headers = agent("BadPatch")
    task_id = client.get("/api/tasks", headers=headers).json()["tasks"][0]["id"]

    null_response = client.patch(
        f"/api/tasks/{task_id}", headers=headers, json={"title": None}
    )
    assert null_response.status_code == 400

    extra_response = client.patch(
        f"/api/tasks/{task_id}", headers=headers, json={"owner": "someone-else"}
    )
    assert extra_response.status_code == 422


def test_validation_errors_return_422(client, agent):
    _, headers = agent("Validator")

    missing_title = client.post("/api/tasks", headers=headers, json={"priority": "high"})
    assert missing_title.status_code == 422

    blank_title = client.post("/api/tasks", headers=headers, json={"title": "   "})
    assert blank_title.status_code == 422

    bad_priority = client.post(
        "/api/tasks", headers=headers, json={"title": "x", "priority": "urgent"}
    )
    assert bad_priority.status_code == 422

    bad_status = client.post(
        "/api/tasks", headers=headers, json={"title": "x", "status": "done"}
    )
    assert bad_status.status_code == 422

    too_long = client.post(
        "/api/tasks", headers=headers, json={"title": "x" * 201}
    )
    assert too_long.status_code == 422

    extra = client.post(
        "/api/tasks", headers=headers, json={"title": "x", "connection_id": "abc"}
    )
    assert extra.status_code == 422


def test_invalid_and_missing_task_ids_return_404(client, agent):
    _, headers = agent("NotFound")

    assert client.get("/api/tasks/not-an-object-id", headers=headers).status_code == 404
    assert (
        client.get("/api/tasks/0123456789abcdef01234567", headers=headers).status_code
        == 404
    )
    assert (
        client.patch(
            "/api/tasks/not-an-object-id", headers=headers, json={"title": "x"}
        ).status_code
        == 404
    )
    assert (
        client.delete("/api/tasks/0123456789abcdef01234567", headers=headers).status_code
        == 404
    )
    assert (
        client.post(
            "/api/tasks/0123456789abcdef01234567/complete", headers=headers
        ).status_code
        == 404
    )


def test_task_status_and_priority_can_be_changed_inline(client, agent):
    _, headers = agent("Inline")
    task = client.get("/api/tasks", headers=headers).json()["tasks"][0]

    for status in ["in_progress", "completed", "pending"]:
        response = client.patch(
            f"/api/tasks/{task['id']}", headers=headers, json={"status": status}
        )
        assert response.status_code == 200
        assert response.json()["status"] == status

    for priority in ["low", "high", "medium"]:
        response = client.patch(
            f"/api/tasks/{task['id']}", headers=headers, json={"priority": priority}
        )
        assert response.status_code == 200
        assert response.json()["priority"] == priority
