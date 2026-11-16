"""Strict agent isolation: one token can never touch another agent's tasks."""

from __future__ import annotations

from bson import ObjectId


def test_tasks_are_isolated_between_agents(client, agent):
    _, alice_headers = agent("Alice")
    _, bob_headers = agent("Bob")

    alice_task = client.post(
        "/api/tasks", headers=alice_headers, json={"title": "Alice only"}
    ).json()
    bob_task = client.post(
        "/api/tasks", headers=bob_headers, json={"title": "Bob only"}
    ).json()

    alice_titles = {
        task["title"]
        for task in client.get("/api/tasks", headers=alice_headers).json()["tasks"]
    }
    bob_titles = {
        task["title"]
        for task in client.get("/api/tasks", headers=bob_headers).json()["tasks"]
    }

    assert "Alice only" in alice_titles
    assert "Alice only" not in bob_titles
    assert "Bob only" in bob_titles
    assert "Bob only" not in alice_titles

    # Cross-read is a 404, not a leak.
    response = client.get(f"/api/tasks/{bob_task['id']}", headers=alice_headers)
    assert response.status_code == 404
    assert "Bob only" not in response.text

    # Cross-write attempts are also 404 and leave Bob's task untouched.
    assert (
        client.patch(
            f"/api/tasks/{bob_task['id']}",
            headers=alice_headers,
            json={"title": "hijacked"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/tasks/{bob_task['id']}/complete", headers=alice_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/tasks/{bob_task['id']}", headers=alice_headers).status_code
        == 404
    )

    still_bob = client.get(f"/api/tasks/{bob_task['id']}", headers=bob_headers)
    assert still_bob.status_code == 200
    assert still_bob.json()["title"] == "Bob only"
    assert still_bob.json()["status"] == "pending"

    # Alice's own task remains owned by Alice.
    still_alice = client.get(f"/api/tasks/{alice_task['id']}", headers=bob_headers)
    assert still_alice.status_code == 404


def test_disconnecting_one_agent_does_not_affect_another(client, agent, database):
    alice, alice_headers = agent("Alice")
    _, bob_headers = agent("Bob")

    assert client.post("/disconnect", headers=alice_headers).status_code == 200

    assert client.get("/api/tasks", headers=alice_headers).status_code == 401
    assert client.get("/api/tasks", headers=bob_headers).status_code == 200
    assert (
        database.raw_tasks.count_documents(
            {"connection_id": ObjectId(alice["connection_id"])}
        )
        == 3
    )


def test_agent_id_cannot_be_supplied_by_the_client(client, agent):
    alice, alice_headers = agent("Alice")
    _, bob_headers = agent("Bob")

    # There is no query/body parameter that accepts an owner id.
    response = client.get(
        f"/api/tasks?connection_id={alice['connection_id']}", headers=bob_headers
    )
    assert response.status_code == 200
    titles = {task["title"] for task in response.json()["tasks"]}
    assert "Alice only" not in titles

    body_with_owner = client.post(
        "/api/tasks",
        headers=bob_headers,
        json={"title": "Should fail", "connection_id": alice["connection_id"]},
    )
    assert body_with_owner.status_code == 422

    # Bob's task list is still only Bob's seeded tasks.
    assert client.get("/api/tasks", headers=bob_headers).json()["count"] == 3
