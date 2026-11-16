"""FastAPI dependencies readable from request state."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ..core.config import Settings
from ..core.events import EventBroker


def get_database(request: Request) -> Any:
    return request.app.state.database


def get_broker(request: Request) -> EventBroker:
    return request.app.state.broker


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings
