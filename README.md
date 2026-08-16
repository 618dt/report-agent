# Report Agent

[English](README.md) | [中文](README.zh-CN.md)

A topic/domain analysis **report agent** built with FastAPI and LangGraph. It supports streaming chat, human-in-the-loop (HITL) confirmation, Plan mode, web search, and streaming Markdown report generation. The frontend is a React conversation workbench.

---

## Tech Stack

| Layer | Technologies |
|------|------|
| Backend | Python 3.12+, FastAPI, Uvicorn, Pydantic |
| Agent | LangChain / LangGraph, DeepSeek (OpenAI-compatible), Skills + Tools |
| Data | MongoDB (business data + LangGraph checkpoints), Redis |
| Retrieval | Tavily (`web_search`), `web_fetch` |
| Observability | Langfuse (optional) |
| Frontend | React 18, Vite 5, react-markdown |
| Deploy | Docker Compose, Nginx (frontend static assets) |

---

## Features

- **Streaming chat (SSE)** — `POST /api/chat/stream` handles both new messages and HITL resume. Clients can reconnect via `GET /api/chat/runs/{run_id}/stream` and cancel a run explicitly.
- **Report generation** — Skill-driven workflow: outline confirmation → web research → `begin_report` / streaming body / `submit_report`. Reports are delivered on a separate artifact channel, not mixed into chat bubbles.
- **HITL confirmation** — Interrupts for report outlines, Plan approval, and similar gates. The frontend can edit the payload and resume.
- **Plan mode** — When enabled, the agent first produces an editable execution plan and only runs the task after confirmation.
- **Deep thinking** — Optional thinking middleware streams the reasoning process to the UI.
- **Conversation management** — Conversation list, message history, title updates, soft delete, plus run/event query and replay.
- **Observability** — Trace ID middleware; optional Langfuse tracing.

---

## Project Layout

```text
report-agent/
├── run.py                      # Local entrypoint (loads YAML, starts uvicorn)
├── requirements.txt            # Python dependencies
├── app/                        # Backend
│   ├── main.py                 # FastAPI factory, lifespan, /health
│   ├── api/                    # Routes (validation + response)
│   ├── logic/                  # Business logic
│   ├── agent/                  # Agent, tools, skills, middleware
│   ├── models/ / schemas/      # Mongo documents / API schemas
│   ├── configs/                # YAML config (includes *.example.yaml)
│   ├── utils/                  # Mongo, Redis, logging, response, auth, etc.
│   ├── constants/              # BizCode and related constants
│   └── core/ / trd_api/        # Domain logic / third-party integrations
├── front/                      # React frontend (Vite)
├── deploy/                     # Docker Compose, backend image, Nginx example
├── docs/                       # Design docs
├── scripts/ / tests/           # Scripts and tests
```

---

## Prerequisites

- **Python** 3.12+ (the Docker image uses 3.12)
- **Node.js** 18+ (frontend)
- **MongoDB** 7.x
- **Redis** 7.x (local or Docker Compose profile)
- External keys: DeepSeek API key; optional Tavily and Langfuse

---

## Configuration

Copy the example files and fill in secrets (the real config files contain credentials and are gitignored):

```bash
cp app/configs/configs.example.yaml app/configs/configs.yaml
cp app/configs/cluster.example.yaml app/configs/cluster.configs.yaml
```

| File | Purpose |
|------|------|
| `app/configs/configs.yaml` | Node settings: host/port, logging, etc. |
| `app/configs/cluster.configs.yaml` | MongoDB, Redis, LLM, Tavily, Langfuse |
| `deploy/.env` | Docker Compose only (published ports, etc.) |

When running as a **local process**, set `mongodb.host` / `redis.host` in `cluster.configs.yaml` to `127.0.0.1`.

When running the **Docker backend**, use the Compose service name `mongodb` for Mongo. Redis defaults to the host via `host.docker.internal`; see `--profile redis` below for an in-compose Redis.

---

## Local Development

### 1. Dependencies and config

```bash
# Backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
cp app/configs/configs.example.yaml app/configs/configs.yaml
cp app/configs/cluster.example.yaml app/configs/cluster.configs.yaml
# Edit cluster.configs.yaml: point mongodb/redis at localhost, set llm.deepseek.api_key, etc.
```

Make sure MongoDB and Redis are running and reachable on the host.

### 2. Start the backend

```bash
python run.py
```

Default (see `configs.yaml`): `http://0.0.0.0:8989`

- API docs: http://localhost:8989/docs
- Health check: http://localhost:8989/health

### 3. Start the frontend

```bash
cd front
npm ci
npm run dev
```

The Vite dev server defaults to http://localhost:3000 and proxies `/api` to `http://127.0.0.1:8989`.

Production build:

```bash
cd front
npm ci && npm run build
```

Serve `front/dist` with Nginx. See [`deploy/nginx`](deploy/nginx) and [`deploy/README.md`](deploy/README.md) for an example.

---

## Docker

Targeted at Linux (Docker Engine + Compose v2). More detail is in [`deploy/README.md`](deploy/README.md).

### 1. Prepare config

```bash
cp app/configs/configs.example.yaml app/configs/configs.yaml
cp app/configs/cluster.example.yaml app/configs/cluster.configs.yaml
cp deploy/.env.example deploy/.env
# Edit cluster.configs.yaml: API keys; set Redis host/password for your Redis mode
```

Keep Mongo/Redis credentials in `deploy/.env` in sync with `cluster.configs.yaml`. Change the sample `change-me-*` passwords before going to production.

> **Note:** `MONGO_INITDB_ROOT_*` only takes effect on **first volume init**. If Mongo was previously started without auth, run `down -v` to wipe the volume, or create the user manually.

### 2. Start (default: MongoDB + backend; Redis on the host)

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

### 3. Optional: also start container Redis

```bash
# In cluster.configs.yaml, set redis.host to redis and match password with deploy/.env REDIS_PASSWORD
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile redis up -d --build
```

### Common commands

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f backend
docker compose -f deploy/docker-compose.yml --env-file deploy/.env down
```

| Service | Default URL |
|------|------|
| Backend docs | http://localhost:8989/docs |
| Health check | http://localhost:8989/health |
| MongoDB | localhost:27017 |
| Frontend | Host Nginx / `npm run dev` (Compose does not include a frontend container) |

---

## API Overview

| Method | Path | Description |
|------|------|------|
| POST | `/api/chat/stream` | Streaming chat / HITL resume (SSE) |
| GET | `/api/chat/runs/{run_id}/stream` | Subscribe to an existing run's SSE |
| POST | `/api/chat/runs/{run_id}/cancel` | Cancel a run |
| GET | `/api/conversations/list` | Conversation list |
| GET | `/api/conversations/{id}/messages` | Message list |
| PUT | `/api/conversations/{id}` | Update conversation title |
| DELETE | `/api/conversations/{id}` | Soft-delete a conversation |
| GET | `/api/conversations/{id}/runs/events` | Run events (including artifact replay) |
| GET | `/api/conversations/{id}/runs/active` | Active run for a conversation |
| GET | `/api/conversations/{id}/runs/interrupted` | Interrupted (HITL) run |
| GET | `/health` | Health check (includes mongo/redis ping) |

`POST /api/chat/send-message` is deprecated; use `POST /api/chat/stream` with a `query` field instead.

### Chat stream modes

`POST /api/chat/stream` switches mode from the request body:

- `query` only (no `approved` / `response`) → new message
- `approved` or `response` (no `query`) → HITL resume; `conversation_id` is required
- both query and resume fields → rejected

Optional flags: `deep_thinking`, `plan_mode`.

---

## How the Agent Works

The agent is a LangGraph graph with a DeepSeek (OpenAI-compatible) model, disk-loaded skills, local tools, and a MongoDB checkpointer keyed by `conversation_id`.

| Tool | Role |
|------|------|
| `request_user_confirmation` | HITL gate for the report outline |
| `propose_plan` / `update_plan_step` | Plan mode: propose and track steps |
| `web_search` / `web_fetch` | Research after the outline is confirmed |
| `begin_report` / `submit_report` | Start streaming the Markdown body, then persist the report |

Typical report flow:

1. Confirm topic, scope, and target length.
2. Propose a chapter outline and wait for HITL confirmation.
3. Search the web, then call `begin_report`, stream the Markdown body, and `submit_report`.
4. If Plan mode is on, `propose_plan` must be confirmed before outline confirmation and writing.

---

## Tests

```bash
python -m pytest tests
```
