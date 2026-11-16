"""FastAPI application factory and public entrypoints."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routers import connections, dashboard, tasks
from .core.config import Settings, load_settings
from .core.events import EventBroker
from .database.indexes import ensure_indexes
from .database.mongo import MongoDatabase
from .domain.models import HealthResponse
from .middleware.activity import ActivityMiddleware

logger = logging.getLogger("agent_playground")

STATIC_DIR = Path(__file__).resolve().parent / "static"

DESCRIPTION = """
An interactive environment for teaching AI agents to use tools through a
focused REST API. Each agent connection gets its own isolated task manager.

## Authentication

1. Call `POST /connect/{name}` to create a connection. The response contains a
   token valid for **24 hours** (configurable).
2. Send it on every task request:

```http
Authorization: Bearer <token>
```

3. Click **Authorize** below and paste the same token to try endpoints here.
4. Call `POST /disconnect` to revoke the token immediately. Tasks are preserved.

Tokens are stored only as SHA-256 hashes and are never written to logs.
Expired and revoked tokens are rejected with `401`.

## Agent isolation

Task endpoints never accept an agent or connection id from the client. The
backend derives the owner from the authenticated token, so one agent can never
read or modify another agent's tasks.

## Dashboard

The dashboard uses a separate password session (`DASHBOARD_PASSWORD`), not an
agent token. It can observe every agent environment and disconnect any agent,
but it cannot mutate tasks. Task writes always use the selected agent's token,
so one agent's token can never read another agent's tasks through the dashboard.

## Real-time

`GET /api/dashboard/events` streams updates with Server-Sent Events. It requires
the dashboard session cookie, which the browser sends automatically.
"""


def create_app(
    *,
    settings: Settings | None = None,
    database: Any | None = None,
) -> FastAPI:
    """Build an application.

    ``settings`` and ``database`` are injectable so tests can use an in-memory
    MongoDB double without touching a real database.
    """

    settings = settings or load_settings()
    database = database if database is not None else MongoDatabase(settings)
    broker = EventBroker()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await database.connect()
        await ensure_indexes(database)
        if settings.dashboard_password == "playground":
            logger.warning(
                "DASHBOARD_PASSWORD is using the default value. Set a strong "
                "password before exposing this app publicly."
            )
        logger.info(
            "Agent Playground ready (db=%s, ttl=%sh)",
            settings.mongodb_db,
            settings.token_ttl_hours,
        )
        try:
            yield
        finally:
            await database.close()

    app = FastAPI(
        title="Agent Playground",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "Authentication",
                "description": (
                    "Create and revoke agent connections. The token returned by "
                    "`POST /connect/{name}` is the only credential task endpoints use."
                ),
            },
            {
                "name": "Tasks",
                "description": (
                    "Task CRUD, authenticated with `Authorization: Bearer <token>`. "
                    "Every operation is scoped to the token owner."
                ),
            },
            {
                "name": "Dashboard",
                "description": (
                    "Read-only observation API used by the web UI. Protected by a "
                    "separate dashboard password session; agent tokens cannot use it."
                ),
            },
            {
                "name": "Public",
                "description": "Health and documentation endpoints.",
            },
        ],
    )
    app.state.settings = settings
    app.state.database = database
    app.state.broker = broker

    # Outside-in: this records authenticated and failed API calls after response.
    app.add_middleware(ActivityMiddleware, database=database, broker=broker)

    app.include_router(connections.router)
    app.include_router(tasks.router)
    app.include_router(dashboard.router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["Public"],
        summary="Health check",
        responses={503: {"model": HealthResponse, "description": "Database unavailable."}},
        operation_id="health_check",
    )
    async def health():
        try:
            await database.ping()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "database": "unavailable"},
            )
        return HealthResponse(status="ok", database="connected")

    return app


app = create_app()
