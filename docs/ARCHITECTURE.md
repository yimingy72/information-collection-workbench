# 系统架构

> 最后更新：2026-09-01

## 组件

| 组件 | 技术 | 职责 |
|---|---|---|
| `frontend/` | React 19、TypeScript、Ant Design 6、Vite | 查询、历史记录、基础配置、分页和Excel导出 |
| `backend/app/main.py` | FastAPI | HTTP API、同步查询、设置管理、前端静态文件 |
| `backend/app/worker.py` | asyncio worker | 领取异步任务、租约恢复、执行采集 |
| `backend/app/repository.py` | asyncpg | PostgreSQL 持久化与迁移 |
| `backend/app/providers/` | httpx | 企业数据源搜索和投资关系采集 |
| `backend/app/miit.py` | httpx、asyncio | ICP 调度、分页完整性、超时、去重和保存 |
| `ymicp` 外部容器 | aiohttp | 工信部认证、验证码和查询接口适配 |
| `seamoon-gateway` | Go | 本地 HTTP 代理到云函数 WebSocket 隧道 |
| SeaMoon 云函数 | Go、阿里云 FC / 腾讯云 SCF | 将 WebSocket 隧道转发到目标 HTTP/HTTPS 服务 |
| PostgreSQL 16 | PostgreSQL | 任务、实体、关系、结果、登录状态和云函数配置 |

## 查询时序

1. API 刷新数据源登录状态并配置本地 SeaMoon 网关。
2. 所选 provider 并行启动；云函数启用时爱企查通过 `seamoon-gateway` 请求，其他 provider 直连；单个 provider 内按企业队列执行投资穿透。
3. 企业实体与投资关系保存到 PostgreSQL。
4. `entity_names_for_run()` 汇总根企业、投资父企业、投资子企业和结果实体名称。
5. 调度器把用户输入的根企业移动到第一位。
6. ICP 阶段读取云函数启用状态：
   - 启用：YMICP 使用白名单 `seamoon-gateway` HTTP 代理。
   - 未启用：YMICP 直连工信部。
7. 每个企业执行分页查询、完整性检查和结果保存。
8. 任一阶段存在错误但已有结果时，任务状态为 `partial`；无错误时为 `succeeded`。

## 爱企查代理轮换

- `build_providers()` 仅向爱企查注入当前启用的 `SERVERLESS_PROXY_URL`。
- 一个爱企查 `httpx.AsyncClient` 复用一条目标为 `aiqicha.baidu.com` 的 CONNECT / WebSocket 隧道。
- 每累计15次实际 HTTP 请求关闭客户端；第16次请求创建新客户端和新云函数隧道。
- 网络重试立即重建隧道；安全验证以及 HTTP 401、403、429 会把当前隧道标记为下一请求前轮换。
- 隧道轮换不等价于固定出口IP轮换，实际出口仍由云平台实例和网络调度决定。

## ICP 分页算法

常量位于 `backend/app/miit.py`：

```text
PAGE_SIZE                       = 26
ICP_PAGINATION_RECOVERY_PASSES = 2
ICP_BATCH_SIZE                  = 5
ICP_PAGE_TIMEOUT_SECONDS        = 10
ICP_COMPANY_TIMEOUT_SECONDS     = 30
```

算法：

1. 为每个分页轮次生成新的 `sessionKey`。
2. 同一轮的所有页在 YMICP 中复用短期会话和认证上下文。
3. 解析每页的 `list`、`pages` 和 `total`。
4. 对不同轮次的记录按 `(domain, serviceLicence)` 合并。
5. 唯一记录数等于 `total` 时立即结束并写入数据库。
6. 超过 `total` 时报告结果不一致；恢复轮次耗尽仍不足时报告结果不完整。
7. 单页始终受10秒双重硬超时约束，整个企业受30秒预算约束。

结果在确认完整前保存在内存集合中，避免把已知不完整的企业分页静默保存为成功结果。数据库同时使用唯一索引防止重复写入。

## 并发与路由

### 云函数路由

- 每批最多5家企业并发。
- 每家企业拥有独立分页会话和 SeaMoon 代理连接。
- 云函数批次不使用直连出口的12秒全局冷却。
- 云函数单实例并发6，保留1个槽位。
- 一个代理 TCP 连接对应一个 WebSocket 隧道；不同连接可能获得不同云出口IP。

### 直连路由

- 企业顺序执行。
- 请求之间等待约0.4秒。
- 每累计5次请求等待12秒。
- 页请求而不是企业数量消耗该计数。

## 数据模型

主要表：

- `collection_runs`：不可变请求、状态、进度、租约、错误和时间。
- `entities`：按 `(provider, external_id)` 归一化的企业实体。
- `relationships`：投资关系、层级、持股比例和来源。
- `results`：企业选择、投资结果和 ICP 结果。
- `provider_sessions`：需要登录的数据源会话。
- `serverless_proxy_settings`：云平台配置、函数地址、状态和凭证。

`results` 使用任务、实体、类别和 payload 摘要去重；ICP 采集还在进程内按域名和服务备案号去重。

## 前端分页

- 历史任务列表：服务端分页，通过 `limit` 和 `offset` 请求。
- 对外投资、ICP备案结果：查询视图一次返回，前端本地分页。
- 默认20条/页，可选10、20、50条。
- `TableFrame` 固定分页栏，`.table-host` 负责内部滚动。
- 历史详情与历史列表互斥渲染，避免同时出现两套分页。

## 安全边界

- API 不接收来自前端的任意代理 URL。
- YMICP 仅允许配置的内部 SeaMoon 代理地址。
- 只有 ICP 和爱企查业务查询经过 SeaMoon；其他数据源和登录链路不经过 SeaMoon。
- 云平台 Secret 不回传前端。
- API 当前没有登录和租户隔离，只应运行在本机或受控私网。
