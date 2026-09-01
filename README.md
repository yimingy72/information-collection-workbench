# 信息收集工作台

> 最后更新：2026-09-01

信息收集工作台是一个面向**已获授权的信息核验场景**的企业关联主体与 ICP 备案查询工作台。平台先通过企业数据源识别根企业及符合条件的对外投资企业，再按企业名称查询工信部备案信息，并把查询过程、结果、错误和耗时保存为可追踪的历史记录。

当前一级页面：

- **ICP备案查询**：输入企业名称、数据源、查询深度和最低持股比例，查看“对外投资”和“ICP备案”两类结果。
- **历史查询**：按企业和状态筛选历史任务；点击记录后在当前页面切换到单独的详情视图，可返回列表。
- **基础配置**：分别配置数据源登录状态和 SeaMoon 云函数代理。

## 当前能力

- 数据源：天眼查、爱企查、快查、风鸟。
  - 天眼查使用匿名接口。
  - 爱企查、快查、风鸟需要在“基础配置 → 数据源配置”扫码登录。云函数启用时，爱企查查询使用 SeaMoon 代理。
- 按查询深度（1～5层）和最低持股比例过滤对外投资关系。
- 根企业优先进行 ICP 查询，随后查询符合条件的关联企业。
- ICP 多页完整性校验、跨页去重、有限次数补全和明确的部分成功状态。
- SeaMoon 云函数一键部署：阿里云函数计算 FC、腾讯云函数 SCF。
- 查询结果导出为一个 Excel 兼容 `.xls` 文件，包含“对外投资”和“ICP备案”两张工作表。
- 历史列表使用服务端分页；查询结果表使用本地分页，默认每页20条，可切换10/20/50条。
- PostgreSQL 持久化任务、实体、关系、结果、运行配置和错误信息。

## 查询流程

```text
用户提交企业名称
    ↓
所选企业数据源搜索根企业
    ↓
按深度和持股比例采集对外投资关系
    ↓
根企业优先，随后查询关联企业的 ICP 备案
    ↓
分页完整性校验与去重
    ↓
保存结果并标记成功 / 部分成功 / 失败
```

SeaMoon 启用时用于 ICP 和爱企查查询；天眼查、快查、风鸟及数据源登录流程保持直连。云函数未部署、未验证或已停止时，ICP 和爱企查均使用直连路由。

## ICP 查询规则

当前规则以 `/backend/app/miit.py` 为准：

- 工信部单页大小：**26条**。
- 单页请求：`httpx` 超时与 `asyncio.timeout` 双重硬超时，最多 **10秒**。
- 单个企业 ICP 查询总预算：最多 **30秒**。
- 云函数模式：每批最多 **5家企业并发**；云函数单实例并发为6，保留1个槽位。
- 云函数批次不套用直连出口的全局冷却；不同企业使用独立分页会话和 SeaMoon 隧道。
- 直连模式：请求间隔约0.4秒，每累计5次请求等待12秒。
- 同一轮分页使用会话标识保持 YMICP 上游会话亲和。
- 工信部偶发返回重叠分页时，平台按“域名 + 服务备案号”合并，最多执行1次初始分页和2次恢复分页。
- 只有合并后的唯一记录数达到上游报告的 `total` 时才写入该企业的 ICP 结果；否则标记“结果不完整”。
- 查询失败信息包含企业名、请求页面次数、最终错误和实际耗时。
- ICP 阶段持续刷新任务心跳，避免长查询被误判为 worker 租约过期。

## 爱企查代理规则

- 爱企查与 ICP 共用当前启用的 SeaMoon 云函数配置。
- 每个爱企查客户端最多复用同一条 HTTP CONNECT / WebSocket 隧道发送 **15次实际 HTTP 请求**，第16次请求前主动关闭客户端并建立新隧道。
- 网络连接重试会立即重建隧道；遇到安全验证、HTTP 401、403 或429时，下一次请求也会先轮换隧道。
- 轮换隧道会创建新的云函数请求，但云平台仍可能复用同一实例，因此不能承诺公网出口IP一定变化。
- 扫码登录链路保持直连，不计入15次请求。

## 系统结构

```text
React 19 + Ant Design 6
          │ /api
          ▼
FastAPI API ───────── PostgreSQL 16
    │                        ▲
    │ 同步查询 / 设置         │ 任务与结果
    ▼                        │
企业数据源                 Worker
    │                        │
    └──── 企业名称 ──────────┘
                             │
                             ▼
                         YMICP 服务
                             │
              直连或 SeaMoon HTTP 代理
                             │
                             ▼
                 阿里云 FC / 腾讯云 SCF
                             │
                             ▼
                         工信部接口
```

详细说明见：

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- [`seamoon/README.md`](seamoon/README.md)

## Docker 启动

仓库目录名包含中文时，Docker Compose 可能无法自动生成项目名，建议始终显式指定：

```bash
cp .env.example .env
docker compose -p asset-workbench up -d --build
```

生产构建后的前端由 API 容器提供：

- 工作台：<http://127.0.0.1:8000>
- 健康检查：<http://127.0.0.1:8000/api/health>

API 默认只绑定宿主机回环地址。当前没有平台登录和租户隔离，不要直接把8000端口暴露到公网。

### YMICP 服务

YMICP 当前作为外部容器运行，不在本仓库的 `docker-compose.yml` 中自动创建。示例：

```bash
docker run -d \
  --name ymicp \
  --restart unless-stopped \
  -p 127.0.0.1:16181:16181 \
  yiminger/ymicp

docker network connect asset-workbench_default ymicp
./scripts/apply_ymicp_patch.sh
```

如容器已连接 `asset-workbench_default` 网络，推荐在 `.env` 中使用：

```dotenv
MIIT_API_URL=http://ymicp:16181
```

本地直接运行后端时可使用 `http://127.0.0.1:16181`。补丁脚本会更新 YMICP 的分页参数处理、SeaMoon 白名单代理、分页会话亲和和验证码适配，然后重启名为 `ymicp` 的容器。

## 本地开发

后端要求 Python 3.12+：

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

另开终端启动 worker：

```bash
cd backend
. .venv/bin/activate
python -m app.worker
```

前端：

```bash
cd frontend
npm ci
npm run dev
```

Vite 开发地址默认是 <http://127.0.0.1:5173>，并把 `/api` 转发到 <http://127.0.0.1:8000>。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | 本地 PostgreSQL | 数据库连接串 |
| `TIANYANCHA_BASE_URL` | `https://capi.tianyancha.com` | 天眼查接口地址 |
| `MIIT_API_URL` | `http://127.0.0.1:16181` | YMICP 服务地址；Compose 可改为 `http://ymicp:16181` |
| `WORKER_POLL_SECONDS` | `1` | worker 队列轮询间隔 |
| `WORKER_LEASE_SECONDS` | `120` | worker 任务租约秒数 |
| `WORKER_CONCURRENCY` | `2` | worker 协程数量；同一 worker 内 provider 查询仍受锁控制 |
| `SERVERLESS_PROXY_URL` | `http://127.0.0.1:19080` | API/Worker 访问本地 SeaMoon HTTP 代理的地址 |
| `SERVERLESS_PROXY_ADMIN_URL` | `http://127.0.0.1:19081` | SeaMoon 网关管理地址 |
| `SERVERLESS_PROXY_MIIT_URL` | `http://seamoon-gateway:19080` | 传给 YMICP 的白名单代理地址 |
| `SEAMOON_CORE_BINARY` | `/usr/local/bin/seamoon-core` | 云平台部署适配器路径 |

## SeaMoon 云函数配置

“基础配置 → 云函数配置”只支持平台托管的一键部署：

1. 选择阿里云 FC 或腾讯云 SCF。
2. 填写地域、函数名称和云账户 AK/SK。
3. 点击“一键部署”。平台会创建或复用函数、创建或复用触发器、验证代理并自动启用。

操作含义：

- **保存**：只保存云平台配置，不改变当前查询路由。
- **一键部署**：部署或更新函数，测试成功后启用。
- **测试**：只验证链路，不切换路由。
- **停止**：关闭平台到 SeaMoon 的 ICP 和爱企查查询路由，不删除云端函数。
- **删除**：删除由平台托管的云函数，并关闭代理。

托管规格：**0.1 vCPU、128 MB内存、512 MB磁盘、单实例并发6、最小实例0**。最小实例为0表示没有请求时不保留预置实例，但云平台仍可能按其计费规则产生镜像、日志、流量或其他资源费用。

SeaMoon 每个上游 HTTP 代理连接建立一个 WebSocket 隧道。使用同一个函数地址不保证每次请求获得固定出口IP；固定出口需要额外配置云 NAT/EIP。

## API 示例

同步执行一次查询并返回完整视图：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/queries \
  -H 'content-type: application/json' \
  -d '{
    "keyword":"小米科技有限责任公司",
    "providers":["tianyancha"],
    "depth":3,
    "holding_percent":100,
    "fields":["invest"],
    "include_branches":false
  }'
```

创建异步任务：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/collection-runs \
  -H 'content-type: application/json' \
  -d '{
    "keyword":"小米科技有限责任公司",
    "providers":["tianyancha"],
    "depth":3,
    "holding_percent":100,
    "fields":["invest"],
    "include_branches":false
  }'
```

读取任务与结果：

```bash
curl 'http://127.0.0.1:8000/api/v1/collection-runs?limit=20&offset=0'
curl http://127.0.0.1:8000/api/v1/queries/{run_id}
curl 'http://127.0.0.1:8000/api/v1/collection-runs/{run_id}/results?limit=20&offset=0&relationship_limit=20&relationship_offset=0'
```

## 测试与构建

```bash
cd backend
pytest -q

cd ../frontend
npm run typecheck
npm run build

cd ../seamoon
go test ./...
```

## 数据、安全与范围

- PostgreSQL 是任务队列和结果事实源；原始数据源响应保存在 `raw_payload`。
- worker 使用 `FOR UPDATE SKIP LOCKED` 和 `lease_id` 防止重复领取与旧租约覆盖。
- 云账户 Secret 保存在后端数据库中；API 只返回“是否已配置”，不会把 Secret 返回前端。
- SeaMoon 仅用于 ICP 和爱企查业务查询；天眼查、快查、风鸟以及数据源登录流程保持直连。
- 已删除的免费代理池模块不再读取、验证、轮换或展示；旧表和迁移仅作为历史结构保留。
- 当前不支持分支机构、平台账号体系、租户隔离、固定云函数出口IP和永久 HTML/PDF 证据归档。
- 仅允许在已授权范围内进行非破坏性查询，禁止数据破坏、持久化攻击或越权扩散。
