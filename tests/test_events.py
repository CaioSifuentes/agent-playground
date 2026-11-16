"""SSE broker and event publication."""

from __future__ import annotations

import pytest

from app.events import EventBroker
from tests.conftest import connect_agent


def test_api_operations_publish_events(client, app):
    broker: EventBroker = app.state.broker
    queue = broker.subscribe()
    try:
        data, _ = connect_agent(client, "Eventful")
        headers = {"Authorization": f"Bearer {data['token']}"}

        created = client.post(
            "/api/tasks", headers=headers, json={"title": "Broadcast me"}
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        client.patch(
            f"/api/tasks/{task_id}", headers=headers, json={"status": "in_progress"}
        )
        client.delete(f"/api/tasks/{task_id}", headers=headers)

        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())

        types = [message["type"] for message in messages]
        assert "agent.connected" in types
        assert "task.created" in types
        assert "task.updated" in types
        assert "task.deleted" in types
        assert "activity.created" in types

        created_event = next(
            message for message in messages if message["type"] == "task.created"
        )
        assert created_event["payload"]["connection_id"] == data["connection_id"]
        assert created_event["payload"]["task"]["title"] == "Broadcast me"

        deleted_event = next(
            message for message in messages if message["type"] == "task.deleted"
        )
        assert deleted_event["payload"]["task_id"] == task_id
    finally:
        broker.unsubscribe(queue)


def test_disconnect_publishes_event(client, app, agent):
    connection, headers = agent("Bye")
    broker: EventBroker = app.state.broker
    queue = broker.subscribe()
    try:
        assert client.post("/disconnect", headers=headers).status_code == 200
        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())
        types = [message["type"] for message in messages]
        assert "agent.disconnected" in types
        event = next(
            message for message in messages if message["type"] == "agent.disconnected"
        )
        assert event["payload"]["connection_id"] == connection["connection_id"]
    finally:
        broker.unsubscribe(queue)


def test_sse_endpoint_requires_dashboard_session(client):
    assert client.get("/api/dashboard/events").status_code == 401


@pytest.mark.asyncio
async def test_broker_fans_out_and_drops_oldest_for_slow_clients():
    broker = EventBroker(queue_size=2)
    first = broker.subscribe()
    second = broker.subscribe()

    for index in range(4):
        await broker.publish("task.created", {"index": index})

    assert broker.subscriber_count == 2
    for queue in (first, second):
        assert queue.qsize() == 2
        values = [(await queue.get())["payload"]["index"] for _ in range(2)]
        assert values == [2, 3]

    broker.unsubscribe(first)
    broker.unsubscribe(second)
    assert broker.subscriber_count == 0
