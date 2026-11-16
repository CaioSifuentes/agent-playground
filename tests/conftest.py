"""Test fixtures.

The app accepts an injected database object, so tests run against mongomock
through a tiny async adapter. No MongoDB server or Docker is required.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class AsyncCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def sort(self, key_or_list: Any, direction: int | None = None) -> "AsyncCursor":
        if direction is not None:
            self._cursor = self._cursor.sort(key_or_list, direction)
        else:
            self._cursor = self._cursor.sort(key_or_list)
        return self

    def limit(self, count: int) -> "AsyncCursor":
        self._cursor = self._cursor.limit(count)
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return list(self._cursor)


class AsyncCollection:
    """Async facade over a mongomock collection for the methods the app uses."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def find(self, *args: Any, **kwargs: Any) -> AsyncCursor:
        return AsyncCursor(self._collection.find(*args, **kwargs))

    async def create_index(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.create_index(*args, **kwargs)

    async def find_one(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        return self._collection.find_one(*args, **kwargs)

    async def insert_one(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.insert_one(*args, **kwargs)

    async def insert_many(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.insert_many(*args, **kwargs)

    async def update_one(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.update_one(*args, **kwargs)

    async def delete_one(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.delete_one(*args, **kwargs)

    async def delete_many(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.delete_many(*args, **kwargs)

    async def count_documents(self, *args: Any, **kwargs: Any) -> int:
        return self._collection.count_documents(*args, **kwargs)


class FakeDatabase:
    def __init__(self) -> None:
        self._client = mongomock.MongoClient()
        self._db = self._client["agent_playground_test"]
        self.connections = AsyncCollection(self._db["connections"])
        self.tasks = AsyncCollection(self._db["tasks"])
        self.activity = AsyncCollection(self._db["activity"])

        # Raw handles for assertions and setup in tests.
        self.raw_connections = self._db["connections"]
        self.raw_tasks = self._db["tasks"]
        self.raw_activity = self._db["activity"]

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> None:
        return None


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://not-used",
        mongodb_db="agent_playground_test",
        token_ttl_hours=24,
        dashboard_session_hours=12,
        dashboard_password="test-dashboard-password",
        cookie_secure=False,
        seed_tasks=True,
        activity_limit=200,
        sse_heartbeat_seconds=15,
    )


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def app(settings: Settings, database: FakeDatabase):
    return create_app(settings=settings, database=database)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def dashboard(client: TestClient):
    response = client.post(
        "/api/dashboard/login", json={"password": "test-dashboard-password"}
    )
    assert response.status_code == 200, response.text
    return client


def connect_agent(
    client: TestClient, name: str = "TestAgent"
) -> tuple[dict[str, Any], Any]:
    response = client.post(f"/connect/{quote(name)}")
    assert response.status_code == 201, response.text
    return response.json(), response


@pytest.fixture()
def connect(client: TestClient):
    return lambda name="TestAgent": connect_agent(client, name)


@pytest.fixture()
def agent(connect):
    def _agent(name: str = "TestAgent"):
        data, _ = connect(name)
        return data, {"Authorization": f"Bearer {data['token']}"}

    return _agent
