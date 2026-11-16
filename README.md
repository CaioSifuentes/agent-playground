# Agent Playground

An observable task environment for AI agents. Agents connect over a small REST
API and operate on **their own isolated task manager**; the dashboard shows every
connected agent, its tasks, and a real-time log of API calls.

Built for teaching how agents observe an environment, call tools, and verify
results.

- **Backend:** FastAPI + MongoDB (async PyMongo driver)
- **Frontend:** plain HTML, CSS and vanilla JavaScript (no build step)
- **Realtime:** Server-Sent Events
- **Hosting:** ready for Square Cloud (Python runtime + managed MongoDB)
- **Tests:** 48 automated tests covering auth, isolation, CRUD, activity logging and dashboard access

---

## Table of contents

1. [How it works](#how-it-works)
2. [Security model](#security-model)
3. [Quick start](#quick-start)
4. [Configuration](#configuration)
5. [Agent API](#agent-api)
6. [Python agent example](#python-agent-example)
7. [Real-time events](#real-time-events)
8. [Dashboard](#dashboard)
9. [Tests](#tests)
10. [Deploying to Square Cloud](#deploying-to-square-cloud)
11. [Operational notes](#operational-notes)

---

## How it works

```
 External agent (Python / Colab / Pydantic AI)
        │
        │  POST /connect/MyAssistant  ->  token (shown once)
        │  Authorization: Bearer <token>
        ▼
 ┌──────────────────────────────────────────────────────────┐
 │ FastAPI                                                  │
 │  • token hash lookup + expiry/revocation checks          │
 │  • task endpoints scoped to the authenticated connection │
 │  • activity middleware (redacted request summary)        │
 │  • in-process SSE broker                                 │
 └───────────────┬──────────────────────────────┬───────────┘
                 │                              │
                 ▼                              ▼
          MongoDB collections            Browser dashboard
          connections / tasks / activity  (password session,
                                           read-only observation,
                                           SSE updates)
```

Each `POST /connect/{name}` creates a **new connection with a unique internal
id**, a 24-hour token and a seeded task manager. The same agent name can be used
more than once; each call is a separate environment.

Tasks remain in MongoDB after a disconnect or token expiry. The connection can
no longer authenticate, but the data is preserved.

The backend always derives the owner from the token. No task endpoint accepts an
agent id, connection id or owner field from the client.

---

## Security model

### Agent authentication

- Tokens are generated with `secrets.token_urlsafe(32)` (256 bits).
- Only the SHA-256 hash is stored in MongoDB. The raw token is returned once.
- Tokens expire after 24 hours (`TOKEN_TTL_HOURS`) and can be revoked immediately
  with `POST /disconnect`.
- Every task request must send `Authorization: Bearer <token>`. The browser may
  use the HttpOnly `agent_token` cookie instead.
- Expired and revoked tokens are rejected with `401`.
- Header values, cookies and raw tokens are never written to the activity log.
- Request summaries have sensitive keys (`token`, `password`, `secret`,
  `authorization`, `api_key`, ...) redacted before storage.

### Agent isolation

- Every task document stores the owning connection's MongoDB `ObjectId`.
- Every task query filters by the connection id from the authenticated token.
- Cross-agent reads, updates, completions and deletes return `404` and never
  reveal the other agent's data.
- Tests assert that two agents with the same name still have separate tasks.

### Dashboard authentication

The dashboard is **not** authenticated with agent tokens. It has a separate
password session (`DASHBOARD_PASSWORD`), signed with HMAC and stored in an
HttpOnly cookie. This separation is deliberate:

- Agent A's token can never be used to read Agent B's tasks through the
  dashboard API.
- The dashboard session is **read-only**. It cannot create, edit or delete tasks.
- Task writes from the browser use the selected agent's own token against the
  same `/api/tasks` endpoints available to external agents.

Because an external agent's token is never known to the browser, externally
connected agents are shown **read-only** in the UI. Connections created from the
dashboard keep their token in `sessionStorage` for the tab's lifetime, so they
are fully editable.

> **Before deploying, change `DASHBOARD_PASSWORD`.** The default is
> `playground` for local development, and a warning is logged at startup.

---

## Quick start

### Prerequisites

- Python 3.11+ (tested on 3.13; Square Cloud uses 3.14)
- A MongoDB database:
  - local MongoDB, or
  - a free MongoDB Atlas cluster, or
  - a Square Cloud managed MongoDB (for deployment)

### 1. Install

```powershell
cd agent-playground
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

macOS/Linux:

```bash
cd agent-playground
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

If `.env` already exists, **edit it instead of copying over it**.

```powershell
copy .env.example .env
```

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=agent_playground
DASHBOARD_PASSWORD=choose-something-better
COOKIE_SECURE=false
```

`COOKIE_SECURE=false` is required for local HTTP. Use `true` in production over
HTTPS.

For Atlas, use the connection string Atlas gives you:

```dotenv
MONGODB_URI=mongodb+srv://user:password@cluster.example.mongodb.net/?retryWrites=true&w=majority
```

### 3. Run

```powershell
python main.py
```

or:

```powershell
uvicorn app.main:app --reload --port 8000
```

Open:

- Dashboard: <http://localhost:8000>
- OpenAPI docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

MongoDB indexes are created automatically at startup. There is no separate
migration step: collections are created on first use.

### 4. Try it

1. Unlock the dashboard with `DASHBOARD_PASSWORD`.
2. Click **New agent connection**, name it `MyAssistant`.
3. Three example tasks appear. Create, edit, complete and delete tasks.
4. Open the **Activity Log** tab and watch the calls.
5. Run the Python example in another terminal:

```powershell
pip install -r requirements-dev.txt
python examples/python_agent.py
```

---

## Configuration

All settings are read from environment variables (a local `.env` is loaded if
present). Real environment variables win over `.env`.

| Variable | Default | Description |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string. |
| `MONGODB_DB` | `agent_playground` | Database name. |
| `MONGODB_TLS_CA_FILE` | — | Path to a CA certificate (Square Cloud / Atlas with custom CA). |
| `MONGODB_TLS_CERT_FILE` | — | Path to a client certificate/key bundle (Square Cloud managed MongoDB). |
| `DASHBOARD_PASSWORD` | `playground` | Dashboard password. **Change in production.** |
| `DASHBOARD_SESSION_HOURS` | `12` | Dashboard session lifetime. |
| `TOKEN_TTL_HOURS` | `24` | Agent token lifetime. |
| `COOKIE_SECURE` | `true` | Set `false` only for local HTTP. |
| `SEED_TASKS` | `true` | Seed three example tasks on each new connection. |
| `ACTIVITY_LIMIT` | `200` | Default activity log page size (max 500). |
| `SSE_HEARTBEAT_SECONDS` | `15` | SSE keep-alive interval (minimum 5). |
| `HOST` | `0.0.0.0` | Local server host used by `main.py`. |
| `PORT` | `8000` local, `80` on Square Cloud | Server port. |

If you set TLS certificate files, the app also sets `tls=true` for PyMongo. You
can alternatively put TLS options directly in `MONGODB_URI`.

---

## Agent API

Base URL: your deployment URL, for example
`https://agent-playground-demo.squareweb.app`.

Interactive documentation with schemas and the **Authorize** button:
`/docs`.

### Connect

```bash
curl -X POST https://your-app.squareweb.app/connect/MyAssistant
```

```json
{
  "agent_name": "MyAssistant",
  "connection_id": "6730f1e0a1b2c3d4e5f60718",
  "token": "6Vb0m...opaque-token...",
  "created_at": "2026-11-16T12:00:00Z",
  "expires_at": "2026-11-17T12:00:00Z"
}
```

- `201 Created`.
- The token is shown **once**. Store it in memory or a secret store.
- The browser also receives an HttpOnly `agent_token` cookie.
- Invalid names return `400`.

### Authenticate

Send the token on every task request:

```http
Authorization: Bearer <token>
```

An invalid, expired or revoked token returns:

```json
{"detail": "Invalid, expired, or revoked token"}
```

with `401` and `WWW-Authenticate: Bearer`.

### Task payload

```json
{
  "title": "Write the weekly report",
  "description": "Summarise progress and blockers.",
  "priority": "high",
  "status": "pending"
}
```

- `priority`: `low` | `medium` | `high` (default `medium`)
- `status`: `pending` | `in_progress` | `completed` (default `pending`)
- `title`: required, 1–200 characters after trimming
- `description`: optional, up to 5000 characters
- Unknown fields are rejected with `422`.

### Endpoints

| Method | Endpoint | Description | Success |
|---|---|---|---|
| `GET` | `/api/tasks` | List the authenticated agent's tasks | `200` |
| `GET` | `/api/tasks/{id}` | Get one task | `200` |
| `POST` | `/api/tasks` | Create a task | `201` |
| `PATCH` | `/api/tasks/{id}` | Update supplied fields | `200` |
| `DELETE` | `/api/tasks/{id}` | Delete a task | `204` |
| `POST` | `/api/tasks/{id}/complete` | Mark completed (idempotent) | `200` |
| `POST` | `/disconnect` | Revoke the token immediately | `200` |
| `GET` | `/api/me` | Identify the current connection | `200` |
| `GET` | `/health` | Public health check | `200` |

All task endpoints are strictly scoped to the token owner. A task id belonging
to another agent behaves exactly like a missing task: `404`.

#### List

```bash
curl https://your-app.squareweb.app/api/tasks \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "tasks": [
    {
      "id": "6730f2b1a1b2c3d4e5f60720",
      "title": "Write the weekly report",
      "description": "Summarise progress and blockers.",
      "priority": "high",
      "status": "pending",
      "created_at": "2026-11-16T12:01:00Z",
      "updated_at": "2026-11-16T12:01:00Z"
    }
  ],
  "count": 1
}
```

#### Create

```bash
curl -X POST https://your-app.squareweb.app/api/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Write the weekly report","priority":"high"}'
```

Returns the created task and `Location: /api/tasks/{id}`.

#### Update

`PATCH` is partial. At least one non-null field is required.

```bash
curl -X PATCH https://your-app.squareweb.app/api/tasks/$TASK_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"in_progress","priority":"medium"}'
```

#### Complete / delete

```bash
curl -X POST https://your-app.squareweb.app/api/tasks/$TASK_ID/complete \
  -H "Authorization: Bearer $TOKEN"

curl -X DELETE https://your-app.squareweb.app/api/tasks/$TASK_ID \
  -H "Authorization: Bearer $TOKEN"
```

#### Disconnect

```bash
curl -X POST https://your-app.squareweb.app/disconnect \
  -H "Authorization: Bearer $TOKEN"
```

Revocation is immediate. Tasks are **not** deleted.

### Status codes

| Code | Meaning |
|---|---|
| `200` | Successful read, update, completion or disconnect |
| `201` | Connection or task created |
| `204` | Task deleted |
| `400` | Invalid request (empty patch, null field, bad agent name) |
| `401` | Missing, invalid, expired or revoked token |
| `404` | Task or agent not found (including cross-agent access) |
| `422` | Pydantic validation error |
| `500` | Unexpected server error |

Error bodies are consistent:

```json
{"detail": "Task not found"}
```

Validation errors use FastAPI's standard `detail` array.

---

## Python agent example

[`examples/python_agent.py`](examples/python_agent.py) is a complete external
agent client. It connects, lists seeded tasks, creates a task, changes its
status, completes and deletes it, then disconnects without printing the token.

```bash
pip install httpx
python examples/python_agent.py --base-url http://localhost:8000 --name MyAssistant
```

The core is a handful of HTTP calls:

```python
import httpx

BASE = "https://your-app.squareweb.app"

with httpx.Client(base_url=BASE, timeout=20) as http:
    connection = http.post("/connect/MyAssistant").raise_for_status().json()
    headers = {"Authorization": f"Bearer {connection['token']}"}

    print(http.get("/api/tasks", headers=headers).json()["tasks"])

    created = http.post(
        "/api/tasks",
        headers=headers,
        json={"title": "Research the topic", "priority": "high"},
    ).raise_for_status().json()

    http.post(f"/api/tasks/{created['id']}/complete", headers=headers)

    http.post("/disconnect", headers=headers)
```

For Pydantic AI, expose these calls as tools. Tool functions should return plain
data and let the model choose the next call:

```python
from pydantic_ai import Agent

agent = Agent("openai:gpt-4o-mini", system_prompt="Use the task tools.")

@agent.tool_plain
def list_tasks() -> list[dict]:
    return http.get("/api/tasks", headers=headers).json()["tasks"]

@agent.tool_plain
def create_task(title: str, priority: str = "medium") -> dict:
    response = http.post(
        "/api/tasks",
        headers=headers,
        json={"title": title, "priority": priority},
    )
    response.raise_for_status()
    return response.json()
```

The agent's environment is isolated by its token; the tool functions need no
agent id or owner argument.

---

## Real-time events

`GET /api/dashboard/events` is a Server-Sent Events stream. It requires the
dashboard session cookie; `EventSource` sends it automatically.

Each message is a JSON envelope:

```text
data: {"type":"task.created","payload":{"connection_id":"...","task":{...}}}
```

Event types:

| Type | Payload |
|---|---|
| `agent.connected` | `connection_id`, `agent_name`, `created_at`, `expires_at` |
| `agent.disconnected` | `connection_id`, `agent_name` |
| `task.created` | `connection_id`, `task` |
| `task.updated` | `connection_id`, `task` |
| `task.deleted` | `connection_id`, `task_id` |
| `activity.created` | `entry` (same shape as the activity API) |

The frontend also reads events with a plain `EventSource` and refreshes only the
selected environment when its `connection_id` matches.

> SSE fan-out is in-process. Run **one Uvicorn worker** (the supplied Square
> Cloud command does). Multiple workers would each have their own subscriber set,
> so events would only reach clients connected to the same worker.

---

## Dashboard

### Sidebar — connected agents

- Lists active (non-expired, non-revoked) connections with name, status and time
  until token expiry.
- Clicking an agent selects its task environment (highlighted).
- **Editable** means this browser created the connection and still holds its
  token in `sessionStorage`.
- **Read-only** means the agent connected externally. Its token belongs to the
  agent, so the dashboard cannot mutate its tasks.
- **New agent connection** creates a connection through `POST /connect/{name}`.

### Task manager

- List tasks with priority/status badges, timestamps and short ids.
- Create and edit through a modal.
- Change priority and status inline.
- Mark complete, delete.
- All writes call the same `/api/tasks` API with a bearer token.
- Empty states explain when no agent is selected or no tasks exist.

### Activity log

- Every `/connect/{name}`, `/disconnect`, `/api/me` and `/api/tasks...` call is
  recorded, including failed operations.
- Columns: time, agent, method + path, sanitized request summary, status code,
  success/failure, duration.
- Updates immediately over SSE.
- Filter by agent or result. Filters are also available server-side on
  `GET /api/dashboard/activity?agent_id=...&success=false`.
- Tokens, cookies and authorization headers are never stored. Sensitive JSON
  keys and query parameters are redacted.

### Dashboard API (read-only)

Protected by the dashboard password session:

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/dashboard/login` | `{"password": "..."}` → session cookie |
| `POST` | `/api/dashboard/logout` | Clear session |
| `GET` | `/api/dashboard/session` | `{"authenticated": true/false}` |
| `GET` | `/api/dashboard/agents` | Active connections |
| `GET` | `/api/dashboard/agents/{id}/tasks` | Read one environment |
| `GET` | `/api/dashboard/activity` | Activity log with filters |
| `GET` | `/api/dashboard/events` | SSE stream |

There are no dashboard mutation endpoints by design.

---

## Tests

Tests use an injected in-memory MongoDB double (`mongomock`), so they run without
Docker or a database server.

```powershell
pip install -r requirements-dev.txt
pytest
```

```text
48 passed
```

Coverage includes:

- token generation, hashing, 24-hour expiry and HttpOnly cookie
- missing/invalid/expired/revoked tokens, cookie auth, Bearer precedence
- full task CRUD, completion, validation and status codes
- strict cross-agent isolation for read, update, complete and delete
- tasks preserved after disconnect; disconnect does not affect other agents
- seeding of example tasks with mixed priorities/statuses
- activity logging for successful and failed calls, redaction, filtering
- dashboard login/session/logout and agent-token rejection
- SSE broker fan-out, overflow behaviour and published event types
- OpenAPI paths, security schemes, docs and frontend assets

Manual UI verification was also performed against the real FastAPI app using the
in-memory database, including live SSE updates from an external API client.

---

## Deploying to Square Cloud

The repository includes [`squarecloud.app`](squarecloud.app):

```ini
MAIN=main.py
MEMORY=512
VERSION=recommended
DISPLAY_NAME=Agent Playground
DESCRIPTION=Observable task environments for AI agents
AUTORESTART=true
SUBDOMAIN=agent-playground-demo
START=python -m uvicorn main:app --host 0.0.0.0 --port 80
```

Square Cloud sets `PORT=80` and `HOST=0.0.0.0`. The app listens on port 80 via
the start command.

### 1. Create the database

**Option A — Square Cloud managed MongoDB** (Standard plan or higher):

1. Dashboard → **Databases** → **Create database** → MongoDB.
2. Copy the host, port, user and password.
3. Download `certificate.pem` from the database's SSL/TLS settings.
4. Build the URI:

   ```text
   mongodb://default:PASSWORD@square-cloud-db-ID.squareweb.app:PORT/
   ```

**Option B — MongoDB Atlas** (free tier works): create a cluster, a database
user and allow your host. Copy the `mongodb+srv://...` URI. Square Cloud egress
addresses can change, so for a classroom deployment either allow access from
anywhere (`0.0.0.0/0`, with a strong database password) or use the managed
MongoDB option.

### 2. Prepare the ZIP

The ZIP must contain `squarecloud.app`, `main.py` and `requirements.txt` at its
**root** — do not zip the parent folder.

Include:

- `app/`, `main.py`, `requirements.txt`, `squarecloud.app`
- `certificate.pem` when using Square Cloud managed MongoDB (not committed to Git)

Exclude `.venv/`, `__pycache__/`, `.env`, `.git/` and test caches. The included
`.gitignore` covers them. `squarecloud.ignore` also excludes local-only files for
CLI uploads; `certificate.pem` is intentionally **not** excluded because the
managed MongoDB connection needs it.

```powershell
# Example: build a clean ZIP from the project root
Compress-Archive -Path app,main.py,requirements.txt,squarecloud.app,certificate.pem `
  -DestinationPath agent-playground.zip -Force
```

### 3. Upload and configure

1. Upload the ZIP in the Square Cloud dashboard and enable **Web Publication**
   with a unique subdomain.
2. Set the environment variables (Settings → Environment Variables):

   ```dotenv
   MONGODB_URI=mongodb://default:PASSWORD@square-cloud-db-ID.squareweb.app:PORT/
   MONGODB_DB=agent_playground
   MONGODB_TLS_CA_FILE=certificate.pem
   MONGODB_TLS_CERT_FILE=certificate.pem
   DASHBOARD_PASSWORD=a-long-random-password
   COOKIE_SECURE=true
   TOKEN_TTL_HOURS=24
   ```

   For Atlas, use your Atlas URI instead and omit the certificate variables.

3. Deploy. Square Cloud runs `pip install -r requirements.txt` and then:

   ```bash
   python -m uvicorn main:app --host 0.0.0.0 --port 80
   ```

4. Open `https://<your-subdomain>.squareweb.app`.
5. Check `https://<your-subdomain>.squareweb.app/health` and `/docs`.

If you change the subdomain, edit `SUBDOMAIN` in `squarecloud.app` before the
first deploy; it cannot be added later.

### 4. Verify a public agent

```bash
curl -X POST https://<your-subdomain>.squareweb.app/connect/PublicTest
```

The response includes a token valid for 24 hours. Use it against
`/api/tasks` from an external Python process.

---

## Operational notes

- **Single worker:** required for in-process SSE fan-out. Do not add
  `--workers N` unless you add a shared pub/sub layer (Redis, etc.).
- **Data retention:** connections are retained after expiry/revocation so tasks
  and history are preserved. Active queries filter them out; there is no
  destructive TTL index. Add a retention policy if you need one.
- **Dashboard password:** shared by instructors/students. Change it before public
  deployment. Login attempts get a small fixed delay but there is no distributed
  rate limiter; put the app behind a proxy/WAF if the URL is widely public.
- **Browser tokens:** dashboard-created tokens live in `sessionStorage` for the
  tab. Closing the tab loses the ability to edit those connections, but the
  agent tokens remain valid until expiry/revocation. All user-provided strings
  are rendered with `textContent`, not HTML.
- **CORS:** intentionally not enabled. External agents call the API server-side.
  If you build a browser-based agent on another origin, add an explicit CORS
  policy.
- **Health checks:** `/health` pings MongoDB and returns `503` if it is
  unavailable.
- **Time:** all timestamps are stored and returned in UTC; the UI formats them in
  the viewer's locale.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the data model, API
contracts, request flow and design trade-offs.
