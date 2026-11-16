"""MongoDB indexes.

Index creation is idempotent and runs during application startup.
"""

from __future__ import annotations

from typing import Any


async def ensure_indexes(database: Any) -> None:
    """Create the indexes used by queries and uniqueness checks.

    Expired connections are intentionally left in the database: their tasks
    must be preserved, and date filters already reject them.
    """

    await database.connections.create_index(
        "token_hash", unique=True, name="uniq_token_hash"
    )
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
