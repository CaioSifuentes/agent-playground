"""Pure ASGI activity-log middleware.

It captures the request body as it streams to the application (without
consuming it), records the final status code and duration, and publishes a
real-time event. Headers are never stored, so bearer tokens and cookies cannot
leak into the activity log.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode

from ..core.events import EventBroker
from ..core.security import AuthContext
from ..core.time import utc_now
from ..domain.serializers import serialize_activity

logger = logging.getLogger("agent_playground.activity")

MAX_BODY_CAPTURE = 8_192
MAX_SUMMARY_LENGTH = 400
SENSITIVE_KEY_PARTS = (
    "token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "apikey",
    "cookie",
)


def _is_logged_path(path: str) -> bool:
    return (
        path == "/api/tasks"
        or path.startswith("/api/tasks/")
        or path == "/api/me"
        or path == "/disconnect"
        or path.startswith("/connect/")
    )


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "…"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact(item, depth + 1)
        return redacted
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:160] + ("…" if len(value) > 160 else "")
    return value


def _sanitized_query(query_string: bytes) -> str:
    if not query_string:
        return ""
    try:
        decoded = query_string.decode("utf-8", errors="replace")
    except Exception:
        return "<query>"
    pairs = parse_qsl(decoded, keep_blank_values=True)
    clean: list[tuple[str, str]] = []
    for key, value in pairs[:30]:
        if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
            clean.append((key, "[redacted]"))
        else:
            clean.append((key, value[:120]))
    return urlencode(clean)[:300]


def build_request_summary(scope: dict[str, Any], body: bytes) -> str:
    parts: list[str] = []
    query = _sanitized_query(scope.get("query_string", b""))
    if query:
        parts.append("?" + query)
    if body:
        try:
            parsed = json.loads(body.decode("utf-8"))
            parts.append(
                json.dumps(
                    _redact(parsed),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            parts.append("<non-JSON body>")

    summary = " ".join(parts)
    if len(summary) > MAX_SUMMARY_LENGTH:
        summary = summary[:MAX_SUMMARY_LENGTH] + "…"
    return summary


class ActivityMiddleware:
    def __init__(self, app: Any, *, database: Any, broker: EventBroker) -> None:
        self.app = app
        self.database = database
        self.broker = broker

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _is_logged_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})
        started_at = utc_now()
        started = time.perf_counter()
        status_code = 500
        captured = bytearray()
        captured_size = 0

        async def wrapped_receive() -> dict[str, Any]:
            nonlocal captured_size
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                remaining = MAX_BODY_CAPTURE - captured_size
                if remaining > 0 and chunk:
                    captured.extend(chunk[:remaining])
                    captured_size += len(chunk[:remaining])
            return message

        async def wrapped_send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            await self._record(
                scope=scope,
                started_at=started_at,
                status_code=status_code,
                duration_ms=duration_ms,
                body=bytes(captured),
            )

    async def _record(
        self,
        *,
        scope: dict[str, Any],
        started_at: Any,
        status_code: int,
        duration_ms: float,
        body: bytes,
    ) -> None:
        try:
            state = scope.get("state", {}) or {}
            auth: AuthContext | None = state.get("auth")
            log_agent: dict[str, Any] = state.get("log_agent") or {}

            if auth is not None:
                connection_id = auth.connection_id
                agent_name = auth.agent_name
            elif log_agent:
                connection_id = log_agent.get("connection_id")
                agent_name = log_agent.get("agent_name", "unknown")
            else:
                connection_id = None
                agent_name = "unauthenticated"

            document = {
                "timestamp": started_at,
                "connection_id": connection_id,
                "agent_name": agent_name,
                "method": scope.get("method", "GET"),
                "path": scope.get("path", ""),
                "request_summary": build_request_summary(scope, body),
                "status_code": status_code,
                "duration_ms": round(duration_ms, 1),
                "success": status_code < 400,
            }
            result = await self.database.activity.insert_one(document)
            document["_id"] = result.inserted_id
            entry = serialize_activity(document).model_dump(mode="json")
            await self.broker.publish("activity.created", {"entry": entry})
        except Exception as exc:  # Never let observability break an API call.
            logger.warning("Could not record activity: %s", exc)
