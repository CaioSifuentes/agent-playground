"""Task CRUD API. Every query is scoped to the authenticated connection."""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..dependencies import get_broker, get_database
from ..events import EventBroker
from ..models import ErrorResponse, Task, TaskCreate, TaskList, TaskUpdate
from ..security import AuthContext, require_auth
from ..serializers import serialize_task
from ..services import (
    create_task,
    delete_task,
    get_task,
    list_tasks,
    update_task,
)

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])

COMMON_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Missing, invalid, expired or revoked token."},
    404: {"model": ErrorResponse, "description": "Task not found for this agent."},
    422: {"description": "Validation error."},
}


def task_object_id(task_id: str) -> ObjectId:
    if not ObjectId.is_valid(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return ObjectId(task_id)


async def _task_or_404(
    database: Any, auth: AuthContext, task_id: str
) -> dict[str, Any]:
    document = await get_task(database, auth.connection_id, task_object_id(task_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return document


@router.get(
    "",
    response_model=TaskList,
    summary="List the authenticated agent's tasks",
    responses={401: COMMON_RESPONSES[401]},
    operation_id="list_tasks",
)
async def list_own_tasks(
    auth: AuthContext = Depends(require_auth),
    database: Any = Depends(get_database),
) -> TaskList:
    documents = await list_tasks(database, auth.connection_id)
    tasks = [serialize_task(document) for document in documents]
    return TaskList(tasks=tasks, count=len(tasks))


@router.post(
    "",
    response_model=Task,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    responses={
        401: COMMON_RESPONSES[401],
        422: COMMON_RESPONSES[422],
    },
    operation_id="create_task",
)
async def create_own_task(
    payload: TaskCreate,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_auth),
    database: Any = Depends(get_database),
    broker: EventBroker = Depends(get_broker),
) -> Task:
    document = await create_task(database, auth.connection_id, payload)
    task = serialize_task(document)
    response.headers["Location"] = f"/api/tasks/{task.id}"
    await broker.publish(
        "task.created",
        {
            "connection_id": auth.connection_id_str,
            "task": task.model_dump(mode="json"),
        },
    )
    return task


@router.get(
    "/{task_id}",
    response_model=Task,
    summary="Get one task",
    responses=COMMON_RESPONSES,
    operation_id="get_task",
)
async def get_own_task(
    task_id: str,
    auth: AuthContext = Depends(require_auth),
    database: Any = Depends(get_database),
) -> Task:
    document = await _task_or_404(database, auth, task_id)
    return serialize_task(document)


@router.patch(
    "/{task_id}",
    response_model=Task,
    summary="Update a task",
    description="Partial update. Only the supplied fields are changed.",
    responses={
        400: {"model": ErrorResponse, "description": "No fields provided or null values supplied."},
        **COMMON_RESPONSES,
    },
    operation_id="update_task",
)
async def update_own_task(
    task_id: str,
    payload: TaskUpdate,
    auth: AuthContext = Depends(require_auth),
    database: Any = Depends(get_database),
    broker: EventBroker = Depends(get_broker),
) -> Task:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=400, detail="No fields provided. Send at least one field."
        )
    if any(value is None for value in changes.values()):
        raise HTTPException(
            status_code=400,
            detail="Fields cannot be null. Omit the field to leave it unchanged.",
        )

    document = await update_task(
        database, auth.connection_id, task_object_id(task_id), changes
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task = serialize_task(document)
    await broker.publish(
        "task.updated",
        {
            "connection_id": auth.connection_id_str,
            "task": task.model_dump(mode="json"),
        },
    )
    return task


@router.post(
    "/{task_id}/complete",
    response_model=Task,
    summary="Mark a task as completed",
    description="Idempotent: completing an already completed task returns 200.",
    responses=COMMON_RESPONSES,
    operation_id="complete_task",
)
async def complete_own_task(
    task_id: str,
    auth: AuthContext = Depends(require_auth),
    database: Any = Depends(get_database),
    broker: EventBroker = Depends(get_broker),
) -> Task:
    document = await update_task(
        database,
        auth.connection_id,
        task_object_id(task_id),
        {"status": "completed"},
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task = serialize_task(document)
    await broker.publish(
        "task.updated",
        {
            "connection_id": auth.connection_id_str,
            "task": task.model_dump(mode="json"),
        },
    )
    return task


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
    responses=COMMON_RESPONSES,
    operation_id="delete_task",
)
async def delete_own_task(
    task_id: str,
    auth: AuthContext = Depends(require_auth),
    database: Any = Depends(get_database),
    broker: EventBroker = Depends(get_broker),
) -> Response:
    deleted = await delete_task(
        database, auth.connection_id, task_object_id(task_id)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")

    await broker.publish(
        "task.deleted",
        {
            "connection_id": auth.connection_id_str,
            "task_id": str(task_object_id(task_id)),
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
