"""Database operations shared by the agent API and the dashboard."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from bson import ObjectId

from .models import TaskCreate
from .security import generate_agent_token, hash_token
from .utils import utc_now

logger = logging.getLogger("agent_playground.services")

AGENT_NAME_PATTERN = re.compile(r"^[^\W_][\w .()&+'-]{0,49}$", re.UNICODE)


class InvalidAgentName(ValueError):
    pass


def normalize_agent_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    if not name:
        raise InvalidAgentName("Agent name cannot be empty")
    if len(name) > 50:
        raise InvalidAgentName("Agent name must be at most 50 characters")
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise InvalidAgentName(
            "Agent name may contain letters, digits, spaces, dots, dashes, "
            "underscores, parentheses and apostrophes, and must start with a letter "
            "or digit"
        )
    return name


async def create_connection(
    database: Any,
    agent_name: str,
    ttl_hours: int,
    seed_tasks: bool,
) -> tuple[str, dict[str, Any]]:
    """Create a connection, store only the token hash, and seed example tasks."""

    token = generate_agent_token()
    now = utc_now()
    document = {
        "agent_name": agent_name,
        "token_hash": hash_token(token),
        "created_at": now,
        "expires_at": now + timedelta(hours=ttl_hours),
        "revoked_at": None,
    }
    result = await database.connections.insert_one(document)
    document["_id"] = result.inserted_id

    if seed_tasks:
        try:
            await _seed_example_tasks(database, result.inserted_id, now)
        except Exception as exc:
            # A seeding problem must not invalidate an otherwise usable connection.
            logger.warning("Could not seed example tasks: %s", exc)

    return token, document


async def _seed_example_tasks(database: Any, connection_id: ObjectId, now: Any) -> None:
    examples = [
        {
            "title": "Connect your first agent",
            "description": (
                "Run POST /connect/{name}, copy the token and send it as "
                "Authorization: Bearer <token>."
            ),
            "priority": "high",
            "status": "pending",
            "created_at": now - timedelta(hours=2),
            "updated_at": now - timedelta(hours=2),
        },
        {
            "title": "Inspect the task API",
            "description": (
                "Try GET /api/tasks, then create a task and update its status. "
                "Watch this dashboard update in real time."
            ),
            "priority": "medium",
            "status": "in_progress",
            "created_at": now - timedelta(minutes=90),
            "updated_at": now - timedelta(minutes=70),
        },
        {
            "title": "Open the interactive docs",
            "description": "Explore every endpoint and schema at /docs.",
            "priority": "low",
            "status": "completed",
            "created_at": now - timedelta(minutes=60),
            "updated_at": now - timedelta(minutes=45),
        },
    ]
    documents = [
        {**example, "connection_id": connection_id, "seeded": True}
        for example in examples
    ]
    await database.tasks.insert_many(documents)


async def get_connection(database: Any, connection_id: ObjectId) -> dict[str, Any] | None:
    return await database.connections.find_one({"_id": connection_id})


async def list_active_connections(database: Any) -> list[dict[str, Any]]:
    now = utc_now()
    cursor = (
        database.connections.find(
            {"revoked_at": None, "expires_at": {"$gt": now}},
            {"token_hash": 0},
        )
        .sort("created_at", -1)
        .limit(200)
    )
    return await cursor.to_list(length=200)


async def revoke_connection(database: Any, connection_id: ObjectId) -> dict[str, Any] | None:
    await database.connections.update_one(
        {"_id": connection_id, "revoked_at": None},
        {"$set": {"revoked_at": utc_now()}},
    )
    return await get_connection(database, connection_id)


# ---------------------------------------------------------------------------
# Tasks — every query is scoped by the authenticated connection id.
# ---------------------------------------------------------------------------


async def list_tasks(database: Any, connection_id: ObjectId) -> list[dict[str, Any]]:
    cursor = (
        database.tasks.find({"connection_id": connection_id})
        .sort("created_at", -1)
        .limit(500)
    )
    return await cursor.to_list(length=500)


async def get_task(
    database: Any, connection_id: ObjectId, task_id: ObjectId
) -> dict[str, Any] | None:
    return await database.tasks.find_one(
        {"_id": task_id, "connection_id": connection_id}
    )


async def create_task(
    database: Any, connection_id: ObjectId, payload: TaskCreate | dict[str, Any]
) -> dict[str, Any]:
    data = payload.model_dump() if isinstance(payload, TaskCreate) else dict(payload)
    now = utc_now()
    document = {
        "connection_id": connection_id,
        "title": data["title"],
        "description": data.get("description", ""),
        "priority": data.get("priority", "medium"),
        "status": data.get("status", "pending"),
        "created_at": now,
        "updated_at": now,
    }
    result = await database.tasks.insert_one(document)
    document["_id"] = result.inserted_id
    return document


async def update_task(
    database: Any,
    connection_id: ObjectId,
    task_id: ObjectId,
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    updates = {key: value for key, value in changes.items() if value is not None}
    if not updates:
        return await get_task(database, connection_id, task_id)
    updates["updated_at"] = utc_now()
    result = await database.tasks.update_one(
        {"_id": task_id, "connection_id": connection_id},
        {"$set": updates},
    )
    if result.matched_count == 0:
        return None
    return await get_task(database, connection_id, task_id)


async def delete_task(
    database: Any, connection_id: ObjectId, task_id: ObjectId
) -> bool:
    result = await database.tasks.delete_one(
        {"_id": task_id, "connection_id": connection_id}
    )
    return result.deleted_count == 1


async def list_activity(
    database: Any,
    *,
    connection_id: ObjectId | None = None,
    success: bool | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if connection_id is not None:
        query["connection_id"] = connection_id
    if success is not None:
        query["success"] = success
    cursor = database.activity.find(query).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)
