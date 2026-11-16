"""Pydantic request and response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["low", "medium", "high"]
TaskStatus = Literal["pending", "in_progress", "completed"]


class TaskCreate(BaseModel):
    """Payload accepted by ``POST /api/tasks``."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "title": "Write the weekly report",
                    "description": "Summarise progress and blockers.",
                    "priority": "high",
                    "status": "pending",
                }
            ]
        },
    )

    title: str = Field(
        min_length=1,
        max_length=200,
        description="Short task title.",
        examples=["Write the weekly report"],
    )
    description: str = Field(
        default="",
        max_length=5000,
        description="Optional long-form description.",
        examples=["Summarise progress and blockers."],
    )
    priority: Priority = Field(
        default="medium", description="Task priority: low, medium or high."
    )
    status: TaskStatus = Field(
        default="pending", description="Task status: pending, in_progress or completed."
    )


class TaskUpdate(BaseModel):
    """Payload accepted by ``PATCH /api/tasks/{task_id}``.

    Only the fields that are present are changed. At least one field must be
    provided.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {"status": "in_progress"},
                {"priority": "high", "description": "Updated details."},
            ]
        },
    )

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    priority: Priority | None = None
    status: TaskStatus | None = None


class Task(BaseModel):
    """A task as returned by the API."""

    id: str = Field(description="Task identifier (MongoDB ObjectId as hex).")
    title: str
    description: str
    priority: Priority
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class TaskList(BaseModel):
    tasks: list[Task]
    count: int


class AgentInfo(BaseModel):
    connection_id: str = Field(description="Stable internal connection identifier.")
    agent_name: str
    created_at: datetime
    expires_at: datetime


class AgentList(BaseModel):
    agents: list[AgentInfo]
    count: int


class ConnectResponse(AgentInfo):
    token: str = Field(
        description=(
            "Opaque bearer token. It is shown once and only its SHA-256 hash is stored. "
            "Send it as `Authorization: Bearer <token>`."
        )
    )


class DisconnectResponse(BaseModel):
    status: Literal["disconnected"] = "disconnected"
    connection_id: str
    agent_name: str


class ActivityEntry(BaseModel):
    id: str
    timestamp: datetime
    connection_id: str | None = Field(
        default=None, description="Null when the request had no valid agent token."
    )
    agent_name: str
    method: str
    path: str
    request_summary: str = Field(
        description="Sanitized summary; tokens, passwords and secrets are redacted."
    )
    status_code: int
    duration_ms: float
    success: bool


class ActivityList(BaseModel):
    entries: list[ActivityEntry]
    count: int


class DashboardLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=300)


class DashboardSession(BaseModel):
    authenticated: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "unavailable"]


class ErrorResponse(BaseModel):
    detail: str
