# 账号体系与多端系统架构设计

> 面向 Report Agent 平台从「单体 + 写死 guest 登录」演进到「登录 + 主系统 + 运营系统 + 管理系统」四端形态的架构设计。
>
> 本文只做设计说明，不涉及代码改动。

---

## 1. 结论先行

**需要抽离账号中心，但推荐「逻辑先行、物理后拆」：第一步把账号能力做成边界清晰的独立模块（独立库、独立接口、不与业务表 join），当触发拆分信号时再平移为独立服务。**

理由：

1. 四端一旦成立，登录就是**共享能力**而不是主系统的一个功能。三套后台各自实现一遍登录，等于三套密码策略、三套验证码/风控、三套会话失效逻辑，长期维护成本远高于一次抽离。
2. 账号中心的**变更节奏和 SLA 与业务不同**：业务功能每周发版，账号中心一旦稳定几乎不动，但它挂了全站登录不可用。二者混在一个进程里，业务的每次发布都在赌账号可用性。
3. 但现在**不建议一上来就拆成微服务**。当前是单体、单前端、`app/utils/auth.py` 还是占位实现，直接上网关 + 独立服务会把调试成本和部署复杂度提前引入，而收益（多团队并行、独立扩容）此刻还不存在。

拆分信号（出现任意 2 条即建议物理拆分为独立服务）：

| 信号 | 说明 |
|------|------|
| 系统数 ≥ 3 且需要单点登录 | 用户在主系统登录后进运营/管理系统不应再登一次 |
| 出现第二个后端服务或第二种技术栈 | 账号逻辑无法再靠「同一个进程内 import」共享 |
| 需要对外开放 API / 第三方接入 | 要做 OAuth2 授权服务器，这件事不应长在业务服务里 |
| 需要对接企业 SSO / LDAP / OA | 内部员工不应在平台里单独维护一套密码 |
| 合规审计要求（等保、个人信息保护法） | 账号操作审计、敏感字段加密需要独立的数据边界 |
| 账号中心需要独立 SLA / 独立扩容 | 登录高峰与报告生成高峰不同步 |

如果团队规模小于 5 人且没有强定制需求，**用开源或云 IdP（Keycloak / Logto / Authing / 云 IDaaS）当身份提供方，自己只维护 `user_profile` 和业务权限**，是比自研更省的选择。自研的价值主要在深度定制的账号绑定关系、配额套餐和风控策略。

---

## 2. 四个「系统」到底各自负责什么

这是整套架构里最容易做错的地方——**运营系统和管理系统被揉成一个「后台」**，结果是运营同学天然拥有改权限、看全量用户数据的能力，且没有审计。先把边界定清楚：

| 系统 | 使用者 | 职责 | 数据特征 | 关键约束 |
|------|--------|------|----------|----------|
| **登录 / 账号中心** | 全部 | 认证、账号、凭据绑定、会话、令牌签发、授权数据的权威来源、账号审计 | 写少读多，读靠令牌本地校验 | 无状态、多实例、故障时已登录用户不受影响 |
| **主系统**（现有 report-agent） | 最终用户 | 对话、报告生成、会话管理、产物下载 | 用户只能访问自己的数据 | 只关心「我是谁 + 我的配额」，不做 RBAC |
| **运营系统** | 运营、客服、内容 | 会话/报告巡检与检索、内容审核、Prompt/Skill/模型配置与灰度、用量与成本看板、用户答疑与工单、配额调整 | **跨用户高频读** | 必须脱敏 + 跨用户读也要审计 |
| **管理系统** | 管理员、安全 | 账号与组织管理、角色与权限、菜单、系统参数、密钥、审计日志查询、功能开关 | 低频写，影响面大 | 权限的**唯一写入方**；高危操作二次校验 |

一句话区分运营和管理：**运营系统经营的是业务数据，管理系统治理的是平台本身。**

团队小的时候，运营和管理可以是**同一个前端工程的两个菜单域**，但权限模型、接口前缀（`/api/ops/*` 与 `/api/admin/*`）和审计策略必须从第一天就分开，否则后面拆不动。

---

## 3. 目标架构

```
                            ┌──────────────────────────────────┐
   app.example.com  ─┐      │        接入层 / API Gateway       │
   ops.example.com  ─┼─────>│  TLS · 路由 · 限流 · JWT 前置校验  │
 admin.example.com  ─┘      │  trace_id 注入 · 统一错误码       │
                            └───────┬──────────┬───────────┬───┘
                                    │          │           │
                    ┌───────────────▼──┐  ┌────▼──────┐  ┌─▼──────────────┐
                    │   主系统 API      │  │ 运营 BFF   │  │  管理 BFF       │
                    │ /api/chat        │  │ /api/ops   │  │  /api/admin    │
                    │ /api/conversations│ │            │  │                │
                    │ (LangGraph Agent)│  │            │  │                │
                    └───────┬──────────┘  └────┬───────┘  └─┬──────────────┘
                            │                  │            │
                            │   内部接口 /internal/*（服务间，双向 mTLS 或内网 token）
                            └──────────┬───────┴────────────┘
                                       │
                        ┌──────────────▼───────────────────────┐
                        │      账号中心 Account Center (IAM)    │
                        │  AuthN 认证  ·  Account 账号/凭据      │
                        │  AuthZ 授权(RBAC) · Session 会话       │
                        │  JWKS 公钥端点 · 账号审计              │
                        └──────┬───────────────────┬───────────┘
                               │                   │
                     ┌─────────▼────────┐   ┌──────▼──────────────┐
                     │ account_db(Mongo)│   │ Redis: session /    │
                     │ account/credential│   │ refresh / 黑名单 /   │
                     │ role/permission  │   │ 验证码 / 限流        │
                     │ audit_log        │   └─────────────────────┘
                     └──────────────────┘

        业务数据仍在 report_db（conversations / message / chat_run / chat_run_event），
        与 account_db 之间只靠 account_id 关联，禁止跨库 join。
```

要点：

- **网关做粗校验，业务做细校验**。网关只验签名与有效期（拿 JWKS 公钥本地验，不回调账号中心），把解析出的身份放入内部请求头；业务服务再做资源归属和权限点判断。
- **账号中心不在请求主链路上**。正常请求不需要访问账号中心，只有登录、刷新令牌、权限变更时才访问。这样账号中心短时故障不影响已登录用户，是可用性设计的关键。

---

## 4. 账号模型设计

### 4.1 核心思路：账号与凭据分离

不要把「手机号」「邮箱」「密码」直接塞进 `account` 表。一旦以后要加微信登录、企业 SSO、API Key，就得改主表。

```
account (全局身份，一个人一条)
  ├── credential  (登录凭据，一对多)
  ├── membership  (组织/租户归属，一对多)  ← 有 B 端需求时才需要
  └── user_profile (业务侧资料，归主系统)
```

**account**（`account_db.account`）

| 字段 | 说明 |
|------|------|
| `_id` / `account_id` | 全局唯一身份 ID，业务侧一律用它做外键 |
| `realm` | 身份域：`consumer`（业务用户）/ `internal`（内部员工） |
| `nickname` / `avatar` | 展示信息 |
| `status` | `active` / `disabled` / `locked` / `deleted` |
| `perm_version` | 权限版本号，权限变更时 +1，用于令牌快速失效 |
| `last_login_at` / `last_login_ip` | 风控与运营用 |
| `create_time` / `update_time` / `is_deleted` | 沿用现有 `BaseModel` 约定 |

**credential**（`account_db.credential`）

| 字段 | 说明 |
|------|------|
| `account_id` | 归属账号 |
| `type` | `password` / `phone` / `email` / `wechat` / `oidc` / `api_key` |
| `identifier` | 登录标识（手机号、邮箱、OIDC sub、AK）；加密存储 + 唯一索引（`type` + `identifier`） |
| `secret` | 密码哈希（argon2id 或 bcrypt）/ SK 哈希；OAuth 类为空 |
| `verified` | 是否已验证 |

唯一索引建在 `(realm, type, identifier)` 上，同一手机号可以在 `consumer` 和 `internal` 各存在一次而互不冲突。

### 4.2 内部员工与业务用户：用 realm 隔离，不要混登

推荐**同一套模型 + 不同 realm**，而不是两张完全独立的表：

- 好处：认证、风控、审计、会话管理只写一遍。
- 关键约束：**令牌里带 `realm`，主系统只接受 `consumer`，运营/管理系统只接受 `internal`**。这样即使员工账号泄露也进不了业务前台，反之亦然。
- 内部员工的 `credential` 优先走企业 SSO（OIDC/LDAP），平台侧不存密码。

### 4.3 组织 / 租户（按需）

如果主系统要卖给企业（一个公司多个成员共享报告、共享配额），需要：

- `org`：组织（`org_id`, `name`, `plan`, `quota`, `status`）
- `membership`：`account_id` × `org_id` × `org_role`（owner / admin / member）

同时业务数据需要增加 `org_id` 维度，权限判断从「owner_id == 我」变成「owner_id == 我 或 (org_id == 我的组织 且 资源可见性=组织内)」。**这个决定必须早做**，因为它会影响 `conversations` / `chat_run` 的索引设计；后补要做数据迁移。如果确定短期只做 C 端，可以先只留 `account_id`，但索引设计上预留组合索引的位置。

### 4.4 授权模型

- **主系统**：不用 RBAC。以**资源归属**为主（现有 `owner_id`），加上**套餐 + 配额**（`plan`: free/pro/enterprise，`quota`: 每日报告数、token 上限、并发 run 数）。配额存 Redis 计数、Mongo 持久化。
- **运营 / 管理系统**：标准 RBAC + 数据范围。

```
staff_account ── staff_role ── role ── role_permission ── permission
                                 └── data_scope: all / dept / self
```

权限点命名建议 `域:资源:动作`，例如：

```
ops:conversation:read      运营查看会话
ops:report:export          运营导出报告
ops:skill:publish          发布 Prompt/Skill 配置
admin:account:disable      禁用账号
admin:role:grant           分配角色
admin:audit:read           查看审计日志
```

**权限数据只由管理系统写入，运营系统只读消费。**

---

## 5. 登录与会话设计

### 5.1 令牌方案

| 项 | 选择 | 理由 |
|----|------|------|
| Access Token | JWT，非对称签名（RS256 / ES256），有效期 5–15 分钟 | 业务服务拿 JWKS 公钥本地验签，不回调账号中心；短有效期降低泄露风险 |
| Refresh Token | 不透明随机串，存 Redis（`sid` 维度），有效期 7–30 天，一次性轮换 | 可吊销、可踢人、可检测重放 |
| 存放位置 | Refresh Token 放 `HttpOnly` + `Secure` + `SameSite=Lax` Cookie；Access Token 放前端内存 | 防 XSS 盗取长期凭据 |

Access Token claims：

```json
{
  "sub": "account_id",
  "realm": "consumer",
  "sid": "会话ID，用于踢人",
  "jti": "令牌ID，用于黑名单",
  "org": "org_id（可选）",
  "pv": 7,
  "plan": "pro",
  "aud": "web|ops|admin",
  "exp": 1730000000
}
```

- 不要把完整权限列表塞进 JWT（运营/管理权限点可能上百个，令牌会膨胀）。带 `pv`（perm_version），业务侧按 `account_id` 从 Redis 缓存里取权限集合，`pv` 不匹配就回源刷新。
- `aud` 区分目标系统，配合 `realm` 双重校验。

### 5.2 单点登录流程（OIDC 授权码 + PKCE）

```
用户访问 ops.example.com
   → BFF 发现无有效会话，302 到 account-center /oauth2/authorize?client_id=ops&code_challenge=...
   → 账号中心：已有 SSO 会话则直接放行；否则展示统一登录页（密码/短信/企业 SSO）
   → 回调 ops.example.com/callback?code=xxx
   → BFF 用 code + code_verifier 换 access/refresh token（后端到后端，client_secret 不出前端）
   → BFF 下发 HttpOnly Cookie，前端拿短期 access token
```

- **统一登录页而不是各系统各自登录页**：验证码、失败锁定、异地提醒、密码策略只维护一份。
- **SSO 会话 Cookie 按 realm 隔离**：`consumer` 的 SSO 会话不应让人直接进管理后台，即使同主域。建议内部系统用独立子域 + 独立会话 Cookie 名。
- 登出：清 Cookie + 吊销 `sid`（Redis 删除 + `jti` 拉黑到自然过期）；多系统登出用 OIDC front-channel logout 或前端主动调各 BFF 的 `/logout`。

### 5.3 SSE 长连接的鉴权（本项目的特殊点）

主系统核心接口 `POST /api/chat/stream` 和 `GET /api/chat/runs/{run_id}/stream` 是 SSE。浏览器原生 `EventSource` **不能自定义请求头**，所以不能靠 `Authorization: Bearer`。三个可选做法：

1. **Cookie 鉴权**（推荐）：access token 或短期会话 Cookie 随 SSE 请求自动携带；注意 `SameSite` 与跨子域配置，以及 CSRF 防护（SSE 是 GET，配合 `SameSite` + Origin 校验）。
2. **一次性 ticket**：先用带 header 的普通接口换一个 30 秒有效、单次可用的 `stream_ticket`，SSE 用 `?ticket=xxx` 连接。适合跨域场景。
3. 前端改用 `fetch` + `ReadableStream` 自己解析 SSE，可以带 header（现有前端如果已经这么做则最省事）。

另外：**连接建立时鉴权通过后，长连接期间令牌可能过期**。约定连接不因令牌过期而中断（一次 run 有时长上限），但要在服务端记录 `sid`，被踢人时主动关闭连接。

---

## 6. 与现有代码的映射

这部分说明落地时各处的对应关系，便于评估改造范围（本文不改代码）。

| 现状 | 目标 |
|------|------|
| `app/utils/auth.py` 的 `login` 装饰器写死 `user_id = "guest"` | 换成 FastAPI 依赖：`principal: Principal = Depends(current_principal)`。中间件负责验签 JWT 并把 `Principal(account_id, realm, org_id, plan, perms, sid)` 写入 `request.state`；依赖负责取用与权限点断言。装饰器方式拿不到类型提示也无法做参数级权限，依赖注入更适合 FastAPI |
| `get_current_user_id()` 返回 `"guest"` 兜底 | 保留「游客模式」时应显式区分：匿名用户给临时 `account_id`（设备指纹派生）并限制配额，而不是所有人共用 `guest`，否则所有匿名会话互相可见 |
| `Conversation.owner_id` / `ChatRun.owner_id` | 语义与 `account_id` 一致，**迁移成本低**。建议保留字段名，在模型注释里明确「值为账号中心的 account_id」；如需组织维度再加 `org_id` |
| `app/api/` 只有 `chat.py` / `conversation.py` | 按访问域分层：`app/api/web/`（主系统）、`app/api/ops/`、`app/api/admin/`，路由前缀分别为 `/api`、`/api/ops`、`/api/admin`，每层挂不同的鉴权依赖 |
| `app/api/internal/`（已存在，空） | 正好用作服务间接口：`/internal/accounts/batch`（批量查账号展示信息）、`/internal/quota/consume` 等，仅内网可达 |
| `BizCode` 已有 `20001/20003/20004/20005` | 账号中心与网关复用同一份错误码表，前端只需一套 401/403 处理逻辑（`TOKEN_EXPIRED` 触发静默刷新，`TOKEN_INVALID` / `UNAUTHORIZED` 跳登录） |
| `TraceIDMiddleware` 已注入 `x-trace-id` | 透传到账号中心与审计日志，实现「一次操作全链路可查」。审计记录里存 `trace_id` |
| `CORSMiddleware` 当前 `allow_origins=["*"]` + `allow_credentials=False` | 改 Cookie 鉴权后必须收敛为具体域名白名单并开启 `allow_credentials=True`（通配符与凭据不能共存） |
| Redis 已接入 | 直接用于 refresh token、SSO 会话、`jti` 黑名单、权限缓存、验证码、登录失败计数、配额计数 |
| Mongo 单库 | 拆 `account_db` 与 `report_db`（或至少集合前缀 + 独立访问层），禁止跨库 join |
| 单个 Vite 前端 `front/` | 三个应用：`front/web`（现有）、`front/ops`、`front/admin`，monorepo 共享请求库与 UI；Nginx 按子域分发到不同静态目录，`deploy/nginx/frontend.conf` 扩展为三份 server 块 |

### 跨用户展示信息的取法

运营系统列表要显示「用户昵称」，但业务库里没有。两种做法，建议**混用**：

- 列表页：调 `/internal/accounts/batch` 批量取，BFF 侧加 30–60 秒本地缓存。
- 高频只读场景：在业务侧冗余快照字段（项目已有 `last_msg_content` 这类冗余惯例），接受最终一致。

---

## 7. 审计与安全

- **审计范围**：运营/管理系统的**所有写操作**，以及**所有跨用户读操作**（谁在什么时候看了哪个用户的会话）。字段：`operator_account_id`、`action`、`target_type`、`target_id`、`before` / `after`、`ip`、`ua`、`trace_id`、`result`、`create_time`。
- **审计不可篡改**：审计集合只允许 append，管理系统只提供查询接口，不提供删除。
- **数据脱敏**：运营侧默认看到掩码手机号/邮箱；查看明文是单独权限点且单条审计。
- **高危操作二次校验**：禁用账号、批量导出、修改权限需要二次验证（短信/TOTP）或走审批流。
- **风控**：登录失败计数锁定、图形/短信验证码、IP 与账号双维度限流、异地登录提醒、管理后台建议 IP 白名单或强制 MFA。
- **密钥管理**：JWT 私钥、第三方 API Key（DeepSeek / Tavily）不进代码库；现有 `cluster.configs.yaml` 已被 gitignore 的做法可延续，生产建议接密钥管理服务，并支持 JWT 签名密钥轮换（JWKS 同时暴露新旧公钥）。

---

## 8. 演进路线（按阶段，不按工期）

**阶段一：把身份做实（改动集中在主系统）**
建立 `account_db` 与账号模型；实现密码 + 短信登录、令牌签发与刷新、JWKS；用 `Principal` 依赖替换 `app/utils/auth.py` 的占位实现；主系统所有接口按 `account_id` 做归属校验；解决 SSE 鉴权；前端加登录页与令牌刷新。此阶段账号中心可以仍在 `app/account/` 内，但**数据库和接口边界按独立服务的标准来划**。

**阶段二：管理系统上线**
RBAC 模型 + 账号管理 + 角色/菜单 + 审计日志查询；`internal` realm 与员工账号；`/api/admin/*` 路由域与权限点断言；`app/api/internal/` 落地第一批服务间接口。

**阶段三：运营系统上线**
跨用户数据检索与巡检（默认脱敏 + 全量审计）、用量与成本看板（复用现有 `chat_run.usage` 与 `token_usage`）、Prompt/Skill/模型配置的灰度发布（现有 `app/agent/skills/loader.py` 具备配置化基础）、配额调整、工单。

**阶段四：物理拆分与外延**
账号中心独立部署 + 网关统一前置鉴权；接企业 SSO；需要时开放 OAuth2 给第三方；账号中心多实例 + 无状态化。

每个阶段都能独立交付价值，且前一阶段不会成为后一阶段的返工来源——这是分阶段的核心目的。

---

## 9. 主要风险与应对

| 风险 | 应对 |
|------|------|
| 账号中心成为单点，挂了全站不可登录 | 无状态 + 多实例 + Redis 主从；令牌本地验签使短时故障不影响**已登录**用户；网关缓存 JWKS 并容忍账号中心不可达 |
| 权限变更延迟生效 | `perm_version` + Redis 权限缓存主动失效；高危操作服务端二次回源校验 |
| 令牌泄露 | 短有效期 access token + refresh token 一次性轮换 + 重放检测（旧 refresh 被使用则吊销整条会话链） |
| 分布式调试复杂度上升 | `trace_id` 全链路透传（已有中间件基础）；账号中心操作日志与业务日志用同一 trace 关联 |
| 过度设计 | 组织/租户、开放平台 OAuth、多级审批在确认需求前不实现，但在数据模型上预留（`org_id`、`realm`、`aud`） |
| 运营权限膨胀 | 运营系统不具备任何权限写入能力；跨用户读默认脱敏且全量审计 |

---

## 10. 决策清单（需要业务侧确认）

以下选择会显著改变实现路径，建议先定：

1. **是否有 B 端 / 多租户需求？** 决定是否引入 `org` + `membership`，以及业务表是否要加 `org_id`。
2. **自研账号中心还是接开源/云 IdP？** 团队小且无深度定制需求时，IdP 方案能省掉登录页、风控、令牌管理的大部分工作。
3. **是否保留游客模式？** 保留则需要匿名账号 + 配额限制 + 登录后数据归并的设计。
4. **内部员工是否走企业 SSO？** 决定 `internal` realm 是否需要存密码。
5. **运营和管理短期是否合并为一个前端？** 可以合并前端，但权限域和接口前缀必须分开。
