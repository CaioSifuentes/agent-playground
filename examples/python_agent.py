#!/usr/bin/env python3
"""A minimal external agent for Agent Playground.

Install the only dependency:
    pip install httpx

Run:
    python examples/python_agent.py --base-url http://localhost:8000

The script connects anonymously, receives a 24-hour bearer token, exercises the
task API, and disconnects at the end. It never prints the token.
"""

from __future__ import annotations

import argparse
import sys

import httpx


class AgentPlaygroundClient:
    def __init__(self, base_url: str, agent_name: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_name = agent_name
        self.token: str | None = None
        self.connection_id: str | None = None
        self.http = httpx.Client(base_url=self.base_url, timeout=20.0)

    def __enter__(self) -> "AgentPlaygroundClient":
        response = self.http.post(f"/connect/{self.agent_name}")
        response.raise_for_status()
        payload = response.json()
        self.token = payload["token"]
        self.connection_id = payload["connection_id"]
        print(f"connected as {payload['agent_name']} ({payload['connection_id'][-8:]})")
        print(f"token expires at {payload['expires_at']}")
        return self

    def __exit__(self, *exc) -> None:
        if self.token:
            try:
                self.http.post(
                    "/disconnect",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
            except httpx.HTTPError:
                pass
        self.http.close()

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("not connected")
        return {"Authorization": f"Bearer {self.token}"}

    def list_tasks(self) -> list[dict]:
        response = self.http.get("/api/tasks", headers=self.headers)
        response.raise_for_status()
        return response.json()["tasks"]

    def create_task(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        status: str = "pending",
    ) -> dict:
        response = self.http.post(
            "/api/tasks",
            headers=self.headers,
            json={
                "title": title,
                "description": description,
                "priority": priority,
                "status": status,
            },
        )
        response.raise_for_status()
        return response.json()

    def update_task(self, task_id: str, **changes) -> dict:
        response = self.http.patch(
            f"/api/tasks/{task_id}", headers=self.headers, json=changes
        )
        response.raise_for_status()
        return response.json()

    def complete_task(self, task_id: str) -> dict:
        response = self.http.post(
            f"/api/tasks/{task_id}/complete", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def delete_task(self, task_id: str) -> None:
        response = self.http.delete(
            f"/api/tasks/{task_id}", headers=self.headers
        )
        response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Agent Playground URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--name",
        default="ExamplePythonAgent",
        help="Agent name used for the isolated environment",
    )
    args = parser.parse_args()

    try:
        with AgentPlaygroundClient(args.base_url, args.name) as client:
            print("\nseeded tasks:")
            for task in client.list_tasks():
                print(f"  [{task['status']:>11}] {task['title']} ({task['priority']})")

            created = client.create_task(
                title="Created by the example agent",
                description=(
                    "This task was created by examples/python_agent.py through the "
                    "same REST API used by every agent."
                ),
                priority="high",
            )
            print(f"\ncreated task {created['id']}")
            print(f"  {created['title']} - {created['status']}")

            moved = client.update_task(created["id"], status="in_progress")
            print(f"updated status: {moved['status']}")

            completed = client.complete_task(created["id"])
            print(f"completed task: {completed['status']}")

            client.delete_task(created["id"])
            print("deleted the example task")

            print("\nfinal task count:", len(client.list_tasks()))
        return 0
    except httpx.HTTPStatusError as error:
        print(
            f"HTTP {error.response.status_code}: {error.response.text}",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError as error:
        print(f"Could not reach {args.base_url}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
