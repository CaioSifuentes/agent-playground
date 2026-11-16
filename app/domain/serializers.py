"""Explicit conversions from MongoDB documents to API models."""

from __future__ import annotations

from typing import Any

from bson import ObjectId

from ..core.time import as_utc
from .models import ActivityEntry, AgentInfo, Task


def _string_id(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    return str(value)


def serialize_task(document: dict[str, Any]) -> Task:
    return Task(
        id=_string_id(document["_id"]),
        title=document["title"],
        description=document.get("description", ""),
        priority=document["priority"],
        status=document["status"],
        created_at=as_utc(document["created_at"]),
        updated_at=as_utc(document["updated_at"]),
    )


def serialize_connection(document: dict[str, Any]) -> AgentInfo:
    return AgentInfo(
        connection_id=_string_id(document["_id"]),
        agent_name=document["agent_name"],
        created_at=as_utc(document["created_at"]),
        expires_at=as_utc(document["expires_at"]),
    )


def serialize_activity(document: dict[str, Any]) -> ActivityEntry:
    connection_id = document.get("connection_id")
    return ActivityEntry(
        id=_string_id(document["_id"]),
        timestamp=as_utc(document["timestamp"]),
        connection_id=_string_id(connection_id) if connection_id is not None else None,
        agent_name=document.get("agent_name", "unknown"),
        method=document["method"],
        path=document["path"],
        request_summary=document.get("request_summary", ""),
        status_code=int(document["status_code"]),
        duration_ms=float(document.get("duration_ms", 0)),
        success=bool(document.get("success", False)),
    )
