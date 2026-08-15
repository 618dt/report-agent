# Report Agent（报告智能体）

基于 FastAPI + LangGraph 的主题/领域分析报告智能体。支持流式对话、人机确认（HITL）、Plan 模式、联网检索与 Markdown 报告产物流式生成；前端为 React 对话工作台。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12+、FastAPI、Uvicorn、Pydantic |
| Agent | LangChain / LangGraph、DeepSeek（OpenAI 兼容）、Skill + Tools |
| 数据 | MongoDB（业务数据 + LangGraph Checkpoint）、Redis |
| 检索 | Tavily（web_search）、web_fetch |
| 可观测 | Langfuse（可选） |
| 前端 | React 18、Vite 5、react-markdown |
| 部署 | Docker Compose、Nginx（前端静态资源） |

---

## 核心功能

- **流式对话（SSE）**：`POST /api/chat/stream` 统一新消息与 HITL 恢复；支持断线重连订阅 `GET /api/chat/runs/{run_id}/stream`、取消 run
- **报告生成**：Skill 驱动流程；目录确认 → 联网检索 → `begin_report` / 流式正文 / `submit_report`；报告与聊天气泡分离（artifact 通道）
- **HITL 人机确认**：报告目录确认、Plan 计划确认等 interrupt，前端可编辑后 resume
- **Plan 模式**：开启后先产出可编辑执行计划，确认后再执行任务
- **深度思考**：可选 thinking 中间件，流式展示推理过程
- **会话管理**：对话列表、消息历史、标题更新、逻辑删除；run / event 查询与回放
- **可观测性**：Trace ID 中间件；可选 Langfuse 链路追踪

---

## 项目主要目录

```text
report-agent/
├── run.py                      # 本地启动入口（读 YAML，启动 uvicorn）
├── requirements.txt            # Python 依赖
├── structure.md                # 目录职责说明（更细）
├── app/                        # 后端
│   ├── main.py                 # FastAPI 工厂、lifespan、/health
│   ├── api/                    # 路由（参数校验 + 响应）
│   ├── logic/                  # 业务逻辑
│   ├── agent/                  # Agent、工具、Skill、中间件
│   ├── models/ / schemas/      # Mongo DO / API Schema
│   ├── configs/                # YAML 配置（含 *.example.yaml）
│   ├── utils/                  # Mongo、Redis、日志、响应、鉴权等
│   ├── constants/              # BizCode 等
│   └── core/ / trd_api/        # 领域逻辑 / 第三方集成
├── front/                      # React 前端（Vite）
├── deploy/                     # Docker Compose、后端镜像、Nginx 示例
├── docs/                       # 设计文档
├── scripts/ / tests/           # 脚本与测试
```

---

## 环境准备

- **Python** 3.12+（Docker 镜像为 3.12）
- **Node.js** 18+（前端）
- **MongoDB** 7.x
- **Redis** 7.x（本地或 Docker profile）
- 外部密钥：DeepSeek API Key；可选 Tavily、Langfuse

---

## 配置

从示例复制并填写密钥（配置文件含凭据，已被 gitignore）：

```bash
cp app/configs/configs.example.yaml app/configs/configs.yaml
cp app/configs/cluster.example.yaml app/configs/cluster.configs.yaml
```

| 文件 | 用途 |
|------|------|
| `app/configs/configs.yaml` | 服务 host/port、日志等节点配置 |
| `app/configs/cluster.configs.yaml` | MongoDB、Redis、LLM、Tavily、Langfuse |
| `deploy/.env` | 仅 Docker Compose 用（端口等） |

**本机进程**时，将 `cluster.configs.yaml` 中 `mongodb.host` / `redis.host` 改为 `127.0.0.1`。  
**Docker 后端**时，Mongo 用服务名 `mongodb`；Redis 默认连宿主机 `host.docker.internal`，或见下文 `--profile redis`。

---

## 本地启动

### 1. 依赖与配置

```bash
# 后端
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
cp app/configs/configs.example.yaml app/configs/configs.yaml
cp app/configs/cluster.example.yaml app/configs/cluster.configs.yaml
# 编辑 cluster.configs.yaml：mongodb/redis 指向本机，填入 llm.deepseek.api_key 等
```

确保本机 MongoDB、Redis 已启动并可连。

### 2. 启动后端

```bash
python run.py
```

默认（见 `configs.yaml`）：`http://0.0.0.0:8989`  
- API 文档：http://localhost:8989/docs  
- 健康检查：http://localhost:8989/health  

### 3. 启动前端

```bash
cd front
npm ci
npm run dev
```

开发服默认 http://localhost:3000，Vite 将 `/api` 代理到 `http://127.0.0.1:8989`。

生产构建：

```bash
cd front
npm ci && npm run build
```

可用 Nginx 托管 `front/dist`，示例见 [`deploy/nginx`](deploy/nginx) 与 [`deploy/README.md`](deploy/README.md)。

---

## Docker 启动

面向 Linux（Docker Engine + Compose v2）。更细说明见 [`deploy/README.md`](deploy/README.md)。

### 1. 准备配置

```bash
cp app/configs/configs.example.yaml app/configs/configs.yaml
cp app/configs/cluster.example.yaml app/configs/cluster.configs.yaml
cp deploy/.env.example deploy/.env
# 编辑 cluster.configs.yaml：API Key；按 Redis 模式设置 host/password
```

### 2. 启动（默认：MongoDB + Backend，Redis 用宿主机）

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

### 3. 可选：同时启动容器 Redis

```bash
# cluster.configs.yaml 中 redis.host 改为 redis，password 与 deploy/.env 的 REDIS_PASSWORD 一致
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile redis up -d --build
```

### 常用命令

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f backend
docker compose -f deploy/docker-compose.yml --env-file deploy/.env down
```

| 服务 | 默认地址 |
|------|----------|
| 后端文档 | http://localhost:8989/docs |
| 健康检查 | http://localhost:8989/health |
| MongoDB | localhost:27017 |
| 前端 | 本机 Nginx / `npm run dev`（Compose 不含前端容器） |

---

## 主要 API（摘要）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/stream` | 流式对话 / HITL 恢复（SSE） |
| GET | `/api/chat/runs/{run_id}/stream` | 订阅已有 run 的 SSE |
| POST | `/api/chat/runs/{run_id}/cancel` | 取消 run |
| GET | `/api/conversations/list` | 对话列表 |
| GET | `/api/conversations/{id}/messages` | 消息列表 |
| GET | `/api/conversations/{id}/runs/events` | run 事件（含 artifact 回放） |
| GET | `/health` | 健康检查（含 mongo/redis ping） |

---
