"""Connection lifecycle and identity endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..config import Settings
from ..dependencies import get_app_settings, get_broker, get_database
from ..events import EventBroker
from ..models import (
    AgentInfo,
    ConnectResponse,
    DisconnectResponse,
    ErrorResponse,
)
from ..security import (
    AGENT_COOKIE_NAME,
    AuthContext,
    clear_agent_cookie,
    hash_token,
    require_auth,
    set_agent_cookie,
)
from ..serializers import serialize_connection
from ..services import (
    InvalidAgentName,
    create_connection,
    normalize_agent_name,
    revoke_connection,
)
from ..utils import as_utc

router = APIRouter(tags=["Authentication"])


@router.post(
    "/connect/{name}",
    response_model=ConnectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect an agent and receive a token",
    description=(
        "Creates an isolated task environment for the agent, seeds it with example "
        "tasks on first connection, and returns a 24-hour bearer token.\n\n"
        "The same name may be used more than once: every call creates a new, "
        "independent connection with its own connection id and tasks.\n\n"
        "The token is returned only once. Only its SHA-256 hash is stored. When "
        "calling from a browser, the endpoint also sets an HttpOnly cookie."
    ),
    responses={
        201: {"description": "Connection created."},
        400: {"model": ErrorResponse, "description": "Invalid agent name."},
        422: {"description": "Path parameter validation error."},
    },
    operation_id="connect_agent",
)
async def connect_agent(
    name: str,
    request: Request,
    response: Response,
    database: Any = Depends(get_database),
    broker: EventBroker = Depends(get_broker),
    settings: Settings = Depends(get_app_settings),
) -> ConnectResponse:
    try:
        agent_name = normalize_agent_name(name)
    except InvalidAgentName as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    token, document = await create_connection(
        database,
        agent_name,
        ttl_hours=settings.token_ttl_hours,
        seed_tasks=settings.seed_tasks,
    )
    set_agent_cookie(response, token, settings)

    # Makes the activity entry attributable even though no auth exists yet.
    request.state.log_agent = {
        "connection_id": document["_id"],
        "agent_name": document["agent_name"],
    }

    info = serialize_connection(document)
    await broker.publish("agent.connected", info.model_dump(mode="json"))
    return ConnectResponse(**info.model_dump(), token=token)


@router.post(
    "/disconnect",
    response_model=DisconnectResponse,
    summary="Disconnect the authenticated agent",
    description=(
        "Immediately revokes the current token. Tasks are deliberately preserved. "
        "The authentication cookie is cleared when it holds the same token."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing, invalid, expired or revoked token."}
    },
    operation_id="disconnect_agent",
)
async def disconnect_agent(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_auth),
    database: Any = Depends(get_database),
    broker: EventBroker = Depends(get_broker),
    settings: Settings = Depends(get_app_settings),
) -> DisconnectResponse:
    await revoke_connection(database, auth.connection_id)

    cookie_token = request.cookies.get(AGENT_COOKIE_NAME)
    if cookie_token and hash_token(cookie_token) == auth.token_hash:
        clear_agent_cookie(response, settings)

    await broker.publish(
        "agent.disconnected",
        {
            "connection_id": auth.connection_id_str,
            "agent_name": auth.agent_name,
        },
    )
    return DisconnectResponse(
        connection_id=auth.connection_id_str,
        agent_name=auth.agent_name,
    )


@router.get(
    "/api/me",
    response_model=AgentInfo,
    summary="Identify the authenticated agent",
    description=(
        "Returns the connection behind the current bearer token or agent cookie. "
        "Useful for checking whether a token is still valid."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing, invalid, expired or revoked token."}
    },
    operation_id="get_current_agent",
)
async def get_me(auth: AuthContext = Depends(require_auth)) -> AgentInfo:
    return AgentInfo(
        connection_id=auth.connection_id_str,
        agent_name=auth.agent_name,
        created_at=as_utc(auth.created_at),
        expires_at=as_utc(auth.expires_at),
    )
