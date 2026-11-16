"""MongoDB connection and index bootstrapping."""

from __future__ import annotations

from typing import Any

from pymongo import AsyncMongoClient

from .config import Settings


class MongoDatabase:
    """Thin async MongoDB handle.

    Only the collections needed by the app are exposed. The connection is
    created during application startup so importing the app (for tests or
    tooling) never touches the network.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncMongoClient | None = None
        self._db: Any = None

    async def connect(self) -> None:
        kwargs: dict[str, Any] = {
            "serverSelectionTimeoutMS": 8000,
            "appname": "agent-playground",
            "tz_aware": True,
        }
        if self._settings.mongodb_tls_ca_file:
            kwargs["tlsCAFile"] = self._settings.mongodb_tls_ca_file
        if self._settings.mongodb_tls_cert_file:
            kwargs["tlsCertificateKeyFile"] = self._settings.mongodb_tls_cert_file
        if self._settings.mongodb_tls_ca_file or self._settings.mongodb_tls_cert_file:
            kwargs["tls"] = True

        self._client = AsyncMongoClient(self._settings.mongodb_uri, **kwargs)
        self._db = self._client[self._settings.mongodb_db]
        await self._client.admin.command("ping")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._db = None

    async def ping(self) -> None:
        if self._client is None:
            raise RuntimeError("MongoDB client is not connected")
        await self._client.admin.command("ping")

    @property
    def connections(self) -> Any:
        if self._db is None:
            raise RuntimeError("MongoDB is not connected")
        return self._db["connections"]

    @property
    def tasks(self) -> Any:
        if self._db is None:
            raise RuntimeError("MongoDB is not connected")
        return self._db["tasks"]

    @property
    def activity(self) -> Any:
        if self._db is None:
            raise RuntimeError("MongoDB is not connected")
        return self._db["activity"]


async def ensure_indexes(database: Any) -> None:
    """Create the indexes used by queries and uniqueness checks.

    Expired connections are intentionally left in the database: their tasks
    must be preserved, and date filters already reject them.
    """

    await database.connections.create_index("token_hash", unique=True, name="uniq_token_hash")
    await database.connections.create_index(
        [("revoked_at", 1), ("expires_at", 1)], name="active_connections"
    )
    await database.tasks.create_index(
        [("connection_id", 1), ("created_at", -1)], name="tasks_by_owner"
    )
    await database.activity.create_index(
        [("timestamp", -1)], name="activity_recent"
    )
    await database.activity.create_index(
        [("connection_id", 1), ("timestamp", -1)], name="activity_by_agent"
    )
