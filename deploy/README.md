# Docker 部署说明（Linux）

## 账号密码（必配）

| 组件 | `deploy/.env` | `app/configs/cluster.configs.yaml` |
|------|---------------|--------------------------------------|
| MongoDB | `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD` | `mongodb.user` / `mongodb.pwd`，`is_auth: true`，`auth_source: admin` |
| 容器 Redis（`--profile redis`） | `REDIS_PASSWORD`（不可为空） | `redis.password`，`is_auth: true`，`host: redis` |
| 宿主机 Redis（默认） | 不使用 `.env` 里的 Redis 项 | `redis.password` 填现有密码，`host: host.docker.internal` |

两边必须一致。上线前请把示例里的 `change-me-*` 改成强密码。

> **注意：** `MONGO_INITDB_ROOT_*` 只在 **数据卷首次初始化** 时生效。若之前用无认证起过 Mongo，需 `down -v` 清空卷后再起，或手工建用户。

## 启动

```bash
cp app/configs/configs.example.yaml app/configs/configs.yaml
cp app/configs/cluster.example.yaml app/configs/cluster.configs.yaml
cp deploy/.env.example deploy/.env
# 编辑 .env 与 cluster.configs.yaml 中的账号密码、API Key

# 默认：Mongo（带账号）+ backend；Redis 用宿主机
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build

# 同时起容器 Redis（带密码）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile redis up -d --build
```

## Redis 可选 profile

| 命令 | Redis |
|------|--------|
| 不加 profile | 不起容器 Redis，连宿主机 |
| `--profile redis` | 起容器 Redis，且必须设置 `REDIS_PASSWORD` |

## 其它

- 配置唯一来源：`app/configs/*.yaml`（compose 挂载进容器）
- 前端：本机 nginx，见 `deploy/nginx/frontend.conf`
- 常用：`ps` / `logs -f backend` / `restart backend` / `down` / `--profile redis down -v`
