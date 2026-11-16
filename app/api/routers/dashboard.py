"""Dashboard API, protected by a separate dashboard session.

The dashboard session is deliberately *not* an agent token: it cannot mutate
tasks. It can read every agent environment and disconnect agents. Task writes
always go through ``/api/tasks`` with the selected agent's own token, keeping
agent isolation strict.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from ...core.config import Settings
from ...core.events import EventBroker
from ...core.security import (
    AGENT_COOKIE_NAME,
    clear_agent_cookie,
    create_dashboard_session,
    dashboard_cookie_scheme,
    dashboard_password_matches,
    hash_token,
    require_dashboard,
    set_dashboard_cookie,
    clear_dashboard_cookie,
    verify_dashboard_session,
)
from ...domain.models import (
    ActivityList,
    AgentList,
    DashboardLoginRequest,
    DashboardSession,
    DisconnectResponse,
    ErrorResponse,
    TaskList,
)
from ...domain.serializers import (
    serialize_activity,
    serialize_connection,
    serialize_task,
)
from ...domain.services import (
    get_connection,
    list_active_connections,
    list_activity,
    list_tasks,
    revoke_connection,
)
from ..dependencies import get_app_settings, get_broker, get_database

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.post(
    "/login",
    response_model=DashboardSession,
    summary="Open a dashboard session",
    description=(
        "Authenticates the read-only dashboard with the password from the "
        "`DASHBOARD_PASSWORD` environment variable. Sets a signed HttpOnly cookie."
    ),
    responses={401: {"model": ErrorResponse, "description": "Wrong password."}},
    operation_id="dashboard_login",
)
async def dashboard_login(
    payload: DashboardLoginRequest,
    response: Response,
    settings: Settings = Depends(get_app_settings),
) -> DashboardSession:
    if not dashboard_password_matches(payload.password, settings):
        # Small fixed delay to make online guessing less pleasant.
        await asyncio.sleep(0.35)
        raise HTTPException(status_code=401, detail="Invalid dashboard password")

    set_dashboard_cookie(response, create_dashboard_session(settings), settings)
    return DashboardSession(authenticated=True)


@router.post(
    "/logout",
    response_model=DashboardSession,
    summary="Close the dashboard session",
    operation_id="dashboard_logout",
)
async def dashboard_logout(
    response: Response,
    settings: Settings = Depends(get_app_settings),
) -> DashboardSession:
    clear_dashboard_cookie(response, settings)
    return DashboardSession(authenticated=False)


@router.get(
    "/session",
    response_model=DashboardSession,
    summary="Check the dashboard session",
    operation_id="dashboard_session",
)
async def dashboard_session(
    session_value: str | None = Depends(dashboard_cookie_scheme),
    settings: Settings = Depends(get_app_settings),
) -> DashboardSession:
    return DashboardSession(
        authenticated=verify_dashboard_session(session_value, settings)
    )


def _agent_object_id(agent_id: str) -> ObjectId:
    if not ObjectId.is_valid(agent_id):
        raise HTTPException(status_code=404, detail="Agent connection not found")
    return ObjectId(agent_id)


@router.get(
    "/agents",
    response_model=AgentList,
    summary="List currently connected agents",
    description=(
        "Read-only observation endpoint. Only active (non-revoked, non-expired) "
        "connections are listed. Dashboard session required."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Dashboard session required."}
    },
    dependencies=[Depends(require_dashboard)],
    operation_id="dashboard_list_agents",
)
async def dashboard_agents(
    database: Any = Depends(get_database),
) -> AgentList:
    documents = await list_active_connections(database)
    agents = [serialize_connection(document) for document in documents]
    return AgentList(agents=agents, count=len(agents))


@router.post(
    "/agents/{agent_id}/disconnect",
    response_model=DisconnectResponse,
    summary="Disconnect any agent (dashboard)",
    description=(
        "Dashboard-only privileged action. Revokes the selected agent's token "
        "immediately while preserving its tasks. This endpoint requires the "
        "dashboard session and is deliberately unavailable with an agent token."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Dashboard session required."},
        404: {"model": ErrorResponse, "description": "Agent connection not found."},
    },
    dependencies=[Depends(require_dashboard)],
    operation_id="dashboard_disconnect_agent",
)
async def dashboard_disconnect_agent(
    agent_id: str,
    request: Request,
    response: Response,
    database: Any = Depends(get_database),
    broker: EventBroker = Depends(get_broker),
    settings: Settings = Depends(get_app_settings),
) -> DisconnectResponse:
    connection_id = _agent_object_id(agent_id)
    connection = await get_connection(database, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Agent connection not found")

    await revoke_connection(database, connection_id)

    # If the browser also holds this agent's cookie, clear it. Other active
    # agent cookies (from other browser-created connections) are untouched.
    cookie_token = request.cookies.get(AGENT_COOKIE_NAME)
    if cookie_token and hash_token(cookie_token) == connection.get("token_hash"):
        clear_agent_cookie(response, settings)

    await broker.publish(
        "agent.disconnected",
        {
            "connection_id": str(connection_id),
            "agent_name": connection["agent_name"],
        },
    )
    return DisconnectResponse(
        connection_id=str(connection_id),
        agent_name=connection["agent_name"],
    )


@router.get(
    "/agents/{agent_id}/tasks",
    response_model=TaskList,
    summary="Read an agent's tasks (dashboard)",
    description=(
        "Dashboard-only, read-only view. External agents cannot call this: the "
        "endpoint requires the dashboard session, not an agent token."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Dashboard session required."},
        404: {"model": ErrorResponse, "description": "Agent connection not found."},
    },
    dependencies=[Depends(require_dashboard)],
    operation_id="dashboard_agent_tasks",
)
async def dashboard_agent_tasks(
    agent_id: str,
    database: Any = Depends(get_database),
) -> TaskList:
    connection = await get_connection(database, _agent_object_id(agent_id))
    if connection is None:
        raise HTTPException(status_code=404, detail="Agent connection not found")
    documents = await list_tasks(database, connection["_id"])
    tasks = [serialize_task(document) for document in documents]
    return TaskList(tasks=tasks, count=len(tasks))


@router.get(
    "/activity",
    response_model=ActivityList,
    summary="List activity log entries",
    description=(
        "Newest first. Dashboard session required. Supports filtering by agent "
        "connection id and by success/failure."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid agent_id."},
        401: {"model": ErrorResponse, "description": "Dashboard session required."},
    },
    dependencies=[Depends(require_dashboard)],
    operation_id="dashboard_activity",
)
async def dashboard_activity(
    agent_id: str | None = Query(default=None),
    success: bool | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    database: Any = Depends(get_database),
    settings: Settings = Depends(get_app_settings),
) -> ActivityList:
    connection_id: ObjectId | None = None
    if agent_id:
        if not ObjectId.is_valid(agent_id):
            raise HTTPException(status_code=400, detail="Invalid agent_id")
        connection_id = ObjectId(agent_id)

    entries = await list_activity(
        database,
        connection_id=connection_id,
        success=success,
        limit=min(limit or settings.activity_limit, 500),
    )
    return ActivityList(
        entries=[serialize_activity(document) for document in entries],
        count=len(entries),
    )


@router.get(
    "/events",
    summary="Server-Sent Events stream",
    description=(
        "Real-time events: `agent.connected`, `agent.disconnected`, `task.created`, "
        "`task.updated`, `task.deleted`, `activity.created`. Requires the dashboard "
        "session cookie, which EventSource sends automatically.\n\n"
        "Each message is `data: {\"type\": \"...\", \"payload\": {...}}`."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Dashboard session required."}
    },
    dependencies=[Depends(require_dashboard)],
    operation_id="dashboard_events",
)
async def dashboard_events(
    request: Request,
    broker: EventBroker = Depends(get_broker),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    queue = broker.subscribe()

    async def event_stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=settings.sse_heartbeat_seconds
                    )
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            broker.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
