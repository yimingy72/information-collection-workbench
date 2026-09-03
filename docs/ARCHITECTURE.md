# 系统架构

> 最后更新：2026-09-02

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

1. API 刷新数据源登录状态，并根据路由优先级决定是否配置本地 SeaMoon 网关。
2. 所选 provider 并行启动；根企业搜索结果写入 PostgreSQL 后立即触发该企业的 ICP 查询。
3. provider 遍历投资关系时，每个符合持股比例的新企业写入后立即进入 ICP 队列，不必等待全部层级遍历完成。
<<<<<<< HEAD
4. ICP 消费者按当前代理模式分批运行：代理模式最多5家企业并发，直连模式串行节流。ICP备案结果不会再次作为待查询企业，避免队列自反馈。
=======
4. ICP 消费者按当前代理模式分批运行：云函数模式按就绪节点数扩展（每节点5个逻辑槽位，最多40个），手动代理按节点数并发，直连模式串行节流。ICP备案结果不会再次作为待查询企业，避免队列自反馈。
>>>>>>> 00b6672 (优化ICP节点调度并同步手动代理规则)
5. 每个企业执行分页查询、完整性检查、跨页去重和结果保存；ICP 阶段持续刷新任务心跳。
6. 任一阶段存在错误但已有结果时，任务状态为 `partial`；无错误时为 `succeeded`。

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
ICP_EMPTY_RESULT_RECOVERY_PASSES = 1
ICP_BATCH_SIZE                  = 5
<<<<<<< HEAD
ICP_PROXY_REQUEST_LIMIT         = 5
ICP_PROXY_WAF_RETRIES           = 2
=======
ICP_CONCURRENCY                 = 40
ICP_PROXY_REQUEST_LIMIT         = 5
ICP_PROXY_WAF_RETRIES           = 2
ICP_PROXY_ERROR_RETRIES         = 1
>>>>>>> 00b6672 (优化ICP节点调度并同步手动代理规则)
ICP_PAGE_TIMEOUT_SECONDS        = 10
ICP_COMPANY_TIMEOUT_SECONDS     = 30
ICP_COMPANY_MAX_ATTEMPTS        = 3
ICP_COMPANY_RETRY_BACKOFF_SECONDS = 2
```

算法：

1. 为每个分页轮次生成新的 `sessionKey`。
2. 同一轮的所有页在 YMICP 中复用短期会话和认证上下文。
3. 解析每页的 `list`、`pages` 和 `total`。
4. 对不同轮次的记录按 `domain + serviceLicence` 合并；域名为空时退化使用 `mainLicence`、`mainId` 或企业名称。
5. 唯一记录数等于 `total` 时立即结束并写入数据库。
6. 超过 `total` 时报告结果不一致；恢复轮次耗尽仍不足时报告结果不完整。
7. 单页始终受10秒双重硬超时约束，每次企业级尝试受30秒预算约束。
8. `total=0` 或已返回有效记录时立即结束；正数 `total` 但空列表仅执行1次快速恢复。
9. 一轮完成后只重新调度失败企业，默认最多执行3次企业级尝试，轮次间线性退避；每次尝试从新的分页会话开始。

结果在确认完整前保存在内存集合中，避免把已知不完整的企业分页静默保存为成功结果。数据库同时使用唯一索引防止重复写入。

## 并发与路由

### 云函数路由

<<<<<<< HEAD
- 每批最多5家企业并发。
- `ICP_PROXY_REQUEST_LIMIT=5` 按实际 ICP 页面请求计数，不按企业数计数。
- `ICP_BATCH_SIZE=5` 控制代理模式下的企业并发；投资关系遍历与 ICP 消费者并行执行。
- 达到5次后，下一次请求使用新的路由代次和新的 YMICP 分页会话，迫使 YMICP 建立新的 HTTP 代理 TCP 连接/SeaMoon WebSocket 隧道。
- 创宇盾拦截时对当前页最多重建2次隧道并重试，不从第一页重新查询。
- 每家企业拥有独立分页会话；云函数路由代次变化时会为当前页切换到新的会话键。单个手动代理保持同一分页会话，不套用云函数代次轮换。
- 云函数批次不使用直连出口的12秒全局冷却。
- 云函数单实例并发6，保留1个槽位。
- 一个代理 TCP 连接对应一个 WebSocket 隧道；单个 FC 函数地址仍不保证获得不同的公网出口IP。
=======
- 按就绪云节点数扩展，每节点5个逻辑槽位，最多40个逻辑并发；7个就绪节点时目标为35个槽位。
- `ICP_PROXY_REQUEST_LIMIT=5` 按实际 ICP 页面请求计数，不按企业数计数。
- `ICP_BATCH_SIZE=5` 是单节点基准槽位，不是整个云池的固定并发；投资关系遍历与 ICP 消费者并行执行。
- 达到请求轮换阈值后，下一次请求使用新的路由代次和新的 YMICP 分页会话，迫使 YMICP 建立新的 HTTP 代理 TCP 连接/SeaMoon WebSocket 隧道。
- 创宇盾拦截时对当前页最多重建2次隧道并重试，不从第一页重新查询。
- 每家企业拥有独立分页会话；云函数路由代次变化时会为当前页切换到新的会话键。单个手动代理保持同一分页会话，不套用云函数代次轮换。
- 云函数批次不使用直连出口的12秒全局冷却；传输错误只做1次页面级快速换隧道，后续交给企业级重试。
- 云函数单实例并发6，保留1个槽位。
- 一个代理 TCP 连接对应一个 WebSocket 隧道；单个 FC 函数地址仍不保证获得不同的公网出口IP。

### 手动代理路由

- 并发数等于已启用且检测成功的手动代理节点数，最多5家企业并发。
- 每个手动代理同一时间只处理1家企业，避免多个验证码流程在同一固定出口上互相阻塞并触发9秒页面超时。
- 不同手动代理节点拥有独立分页会话和验证码流程，可以并行查询。
>>>>>>> 00b6672 (优化ICP节点调度并同步手动代理规则)

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
