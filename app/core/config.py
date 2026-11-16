"""Environment-driven configuration.

All knobs live here so the rest of the application never reads ``os.environ``.
A local ``.env`` file is loaded when present; real environment variables always
win because ``load_dotenv`` does not override them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "agent_playground"
    mongodb_tls_ca_file: str | None = None
    mongodb_tls_cert_file: str | None = None
    token_ttl_hours: int = 24
    dashboard_session_hours: int = 12
    dashboard_password: str = "playground"
    cookie_secure: bool = True
    seed_tasks: bool = True
    activity_limit: int = 200
    sse_heartbeat_seconds: int = 15

    @property
    def agent_cookie_max_age(self) -> int:
        return self.token_ttl_hours * 3600

    @property
    def dashboard_cookie_max_age(self) -> int:
        return self.dashboard_session_hours * 3600


def load_settings() -> Settings:
    """Read settings from the environment (and an optional local .env)."""

    dashboard_password = os.getenv("DASHBOARD_PASSWORD", "playground")
    if not dashboard_password.strip():
        raise RuntimeError("DASHBOARD_PASSWORD cannot be empty")

    return Settings(
        mongodb_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017").strip()
        or "mongodb://localhost:27017",
        mongodb_db=os.getenv("MONGODB_DB", "agent_playground").strip()
        or "agent_playground",
        mongodb_tls_ca_file=os.getenv("MONGODB_TLS_CA_FILE") or None,
        mongodb_tls_cert_file=os.getenv("MONGODB_TLS_CERT_FILE") or None,
        token_ttl_hours=_env_int("TOKEN_TTL_HOURS", 24),
        dashboard_session_hours=_env_int("DASHBOARD_SESSION_HOURS", 12),
        dashboard_password=dashboard_password,
        cookie_secure=_env_bool("COOKIE_SECURE", True),
        seed_tasks=_env_bool("SEED_TASKS", True),
        activity_limit=_env_int("ACTIVITY_LIMIT", 200),
        sse_heartbeat_seconds=_env_int("SSE_HEARTBEAT_SECONDS", 15, minimum=5),
    )
