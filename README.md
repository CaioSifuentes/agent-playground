# Agent Playground

## Objetivo

Agent Playground é um ambiente para agentes de IA praticarem o uso de ferramentas
através de uma API REST. Cada agente se conecta, recebe um token e trabalha em um
**Task Manager isolado**, com tarefas próprias armazenadas no MongoDB.

O projeto permite observar, em tempo real, o que o agente faz: tarefas criadas e
alteradas, chamadas bem-sucedidas, erros de validação e tentativas não
autenticadas. A comunicação em tempo real usa Server-Sent Events.

## Base URL

```text
http://localhost:8000
```

A documentação interativa (OpenAPI) fica em `/docs` e o health check em `/health`.

## Autenticação

### Conectar

```http
POST /connect/{name}
```

Exemplo:

```bash
curl -X POST http://localhost:8000/connect/MyAssistant
```

```json
{
  "agent_name": "MyAssistant",
  "connection_id": "6730f1e0a1b2c3d4e5f60718",
  "token": "opaque-token-shown-once",
  "created_at": "2026-11-16T12:00:00Z",
  "expires_at": "2026-11-17T12:00:00Z"
}
```

Cada chamada cria uma conexão nova, com ID único, token válido por 24 horas e
três tarefas de exemplo. O servidor guarda apenas o hash SHA-256 do token.

Envie o token nas chamadas seguintes:

```http
Authorization: Bearer <token>
```

Tokens inválidos, expirados ou revogados retornam `401`.

### Desconectar

```http
POST /disconnect
Authorization: Bearer <token>
```

Revoga o token imediatamente. As tarefas **não** são apagadas.

## API de tarefas

| Método | Endpoint | Descrição | Sucesso |
|---|---|---|---|
| `GET` | `/api/tasks` | Lista as tarefas do agente autenticado | `200` |
| `POST` | `/api/tasks` | Cria uma tarefa | `201` |
| `GET` | `/api/tasks/{id}` | Consulta uma tarefa | `200` |
| `PATCH` | `/api/tasks/{id}` | Atualiza os campos enviados | `200` |
| `POST` | `/api/tasks/{id}/complete` | Marca como concluída | `200` |
| `DELETE` | `/api/tasks/{id}` | Exclui uma tarefa | `204` |
| `GET` | `/api/me` | Identifica a conexão atual | `200` |

Todas as operações são limitadas ao dono do token. Um ID de outro agente
responde `404`, como se a tarefa não existisse.

### Tarefa

```json
{
  "id": "6730f2b1a1b2c3d4e5f60720",
  "title": "Write the weekly report",
  "description": "Summarise progress and blockers.",
  "priority": "high",
  "status": "pending",
  "created_at": "2026-11-16T12:01:00Z",
  "updated_at": "2026-11-16T12:01:00Z"
}
```

Campos aceitos na criação:

| Campo | Tipo | Regras |
|---|---|---|
| `title` | string | Obrigatório, 1–200 caracteres |
| `description` | string | Opcional, até 5000 caracteres |
| `priority` | string | `low`, `medium` ou `high`; padrão `medium` |
| `status` | string | `pending`, `in_progress` ou `completed`; padrão `pending` |

`PATCH` é parcial: envie apenas os campos que deseja alterar. Corpo vazio retorna
`400`; campos desconhecidos retornam `422`.

### Exemplos

Criar:

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Write the weekly report","priority":"high"}'
```

Listar:

```bash
curl http://localhost:8000/api/tasks \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "tasks": [],
  "count": 0
}
```

Atualizar:

```bash
curl -X PATCH http://localhost:8000/api/tasks/$TASK_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"in_progress"}'
```

Concluir:

```bash
curl -X POST http://localhost:8000/api/tasks/$TASK_ID/complete \
  -H "Authorization: Bearer $TOKEN"
```

Excluir:

```bash
curl -X DELETE http://localhost:8000/api/tasks/$TASK_ID \
  -H "Authorization: Bearer $TOKEN"
```

### Códigos de status

| Código | Significado |
|---|---|
| `200` | Operação concluída |
| `201` | Recurso criado |
| `204` | Tarefa excluída |
| `400` | Requisição inválida |
| `401` | Token ausente, inválido, expirado ou revogado |
| `404` | Tarefa ou agente não encontrado |
| `422` | Erro de validação |
| `500` | Erro inesperado |

Erros usam o formato:

```json
{"detail": "Task not found"}
```

## API do dashboard

O dashboard usa uma senha própria (`DASHBOARD_PASSWORD`), separada dos tokens dos
agentes. Ele pode observar qualquer ambiente e desconectar qualquer agente, mas
não pode criar, editar ou excluir tarefas.

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/api/dashboard/login` | Abre a sessão do dashboard |
| `POST` | `/api/dashboard/logout` | Encerra a sessão |
| `GET` | `/api/dashboard/session` | Verifica a sessão |
| `GET` | `/api/dashboard/agents` | Lista agentes conectados |
| `GET` | `/api/dashboard/agents/{id}/tasks` | Lê as tarefas de um agente |
| `POST` | `/api/dashboard/agents/{id}/disconnect` | Desconecta um agente |
| `GET` | `/api/dashboard/activity` | Lista o log de atividades |
| `GET` | `/api/dashboard/events` | Stream SSE |

## Eventos em tempo real

`GET /api/dashboard/events` envia eventos SSE no formato:

```text
data: {"type":"task.created","payload":{"connection_id":"...","task":{...}}}
```

Tipos: `agent.connected`, `agent.disconnected`, `task.created`, `task.updated`,
`task.deleted` e `activity.created`.

## Documentação

A especificação completa, com schemas e exemplos, está disponível em `/docs`.
