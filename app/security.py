"""Agent authentication, dashboard sessions and cookie helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from bson import ObjectId
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .utils import utc_now

AGENT_COOKIE_NAME = "agent_token"
DASHBOARD_COOKIE_NAME = "dashboard_session"

AuthSource = Literal["bearer", "cookie"]

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="AgentToken",
    bearerFormat="Opaque token from POST /connect/{name}",
    description=(
        "Paste the token returned by `POST /connect/{name}`. External agents should "
        "send `Authorization: Bearer <token>`; browsers may use the HttpOnly cookie "
        "set by the connect endpoint instead."
    ),
)

dashboard_cookie_scheme = APIKeyCookie(
    name=DASHBOARD_COOKIE_NAME,
    auto_error=False,
    scheme_name="DashboardSession",
    description="HttpOnly session cookie created by POST /api/dashboard/login.",
)


@dataclass(frozen=True, slots=True)
class AuthContext:
    connection_id: ObjectId
    agent_name: str
    created_at: datetime
    expires_at: datetime
    source: AuthSource
    token_hash: str

    @property
    def connection_id_str(self) -> str:
        return str(self.connection_id)


def generate_agent_token() -> str:
    """URL-safe, cryptographically secure token (256 bits of entropy)."""

    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthContext:
    """Resolve the token to an active connection.

    The client never supplies an agent/connection id. Identity comes only from
    the bearer token or, for the browser, the HttpOnly agent cookie.
    """

    if credentials is not None:
        token = credentials.credentials
        source: AuthSource = "bearer"
    else:
        token = request.cookies.get(AGENT_COOKIE_NAME)
        source = "cookie"

    if not token:
        raise _unauthorized("Missing authentication token")

    token_hash = hash_token(token)
    now = utc_now()
    connection = await request.app.state.database.connections.find_one(
        {
            "token_hash": token_hash,
            "revoked_at": None,
            "expires_at": {"$gt": now},
        }
    )
    if connection is None:
        raise _unauthorized("Invalid, expired, or revoked token")

    context = AuthContext(
        connection_id=connection["_id"],
        agent_name=connection["agent_name"],
        created_at=connection["created_at"],
        expires_at=connection["expires_at"],
        source=source,
        token_hash=token_hash,
    )
    # Shared with the activity middleware through scope["state"].
    request.state.auth = context
    return context


# ---------------------------------------------------------------------------
# Dashboard session (read-only, separate from agent authentication)
# ---------------------------------------------------------------------------

DASHBOARD_SESSION_VERSION = "v1"


def _dashboard_key(settings: Settings) -> bytes:
    return f"agent-playground:dashboard:{settings.dashboard_password}".encode("utf-8")


def dashboard_password_matches(provided: str, settings: Settings) -> bool:
    return hmac.compare_digest(
        provided.encode("utf-8"), settings.dashboard_password.encode("utf-8")
    )


def create_dashboard_session(settings: Settings) -> str:
    expires = int(time.time()) + settings.dashboard_cookie_max_age
    payload = f"{DASHBOARD_SESSION_VERSION}.{expires}"
    signature = hmac.new(
        _dashboard_key(settings), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_dashboard_session(value: str | None, settings: Settings) -> bool:
    if not value:
        return False
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != DASHBOARD_SESSION_VERSION:
        return False
    payload = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(
        _dashboard_key(settings), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(parts[2], expected):
        return False
    try:
        expires = int(parts[1])
    except ValueError:
        return False
    return expires > int(time.time())


async def require_dashboard(
    request: Request,
    session: str | None = Depends(dashboard_cookie_scheme),
) -> None:
    settings: Settings = request.app.state.settings
    if not verify_dashboard_session(session, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dashboard authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------


def set_agent_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=AGENT_COOKIE_NAME,
        value=token,
        max_age=settings.agent_cookie_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_agent_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=AGENT_COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def set_dashboard_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        key=DASHBOARD_COOKIE_NAME,
        value=value,
        max_age=settings.dashboard_cookie_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_dashboard_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=DASHBOARD_COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
