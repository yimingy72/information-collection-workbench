# 系统架构

> 最后更新：2026-09-09

## 组件

| 组件 | 技术 | 职责 |
|---|---|---|
| `frontend/` | React 19、TypeScript、Ant Design 6、Vite | 异步查询、SSE 实时结果、历史记录、基础配置、分页和Excel导出 |
| `backend/app/main.py` | FastAPI | HTTP API、同步查询、设置管理、前端静态文件 |
| `backend/app/worker.py` | asyncio worker | 领取异步任务、租约恢复、执行采集 |
| `backend/app/repository.py` | asyncpg | PostgreSQL 持久化与迁移 |
| `backend/app/event_hub.py` | asyncpg、asyncio | 用一条共享连接接收 PostgreSQL 通知，并按任务唤醒 SSE |
| `backend/app/providers/` | httpx | 企业数据源搜索和投资关系采集 |
| `backend/app/miit.py` | httpx、asyncio | ICP 缓存校验、调度、分页完整性、超时、去重和保存 |
| `ymicp` Compose 服务 | aiohttp、上游 ICP_Query Python 镜像加工作台补丁 | 工信部认证、验证码和查询接口适配 |
| `seamoon-gateway` | Go | 本地 HTTP 代理到云函数 WebSocket 隧道 |
| SeaMoon 云函数 | Go、阿里云 FC / 腾讯云 SCF | 将 WebSocket 隧道转发到目标 HTTP/HTTPS 服务 |
| PostgreSQL 16 | PostgreSQL | 任务、实体、关系、结果、ICP 完整快照缓存、登录状态、云函数配置和 FOFA/Hunter 密钥 |

## 查询时序

1. 页面调用 `POST /api/v1/collection-runs` 创建排队任务并立即进入详情，不再让浏览器长时间等待同步查询响应。
2. Worker 刷新数据源登录状态，并根据路由优先级配置本地 SeaMoon 网关；所选 provider 并行启动，根企业搜索结果写入 PostgreSQL 后立即触发该企业的 ICP 查询。
3. provider 遍历投资关系时，每个符合持股比例的新企业写入后立即进入 ICP 队列，不必等待全部层级遍历完成。同层投资结果按批写入；ICP 发现名单按 `stream_seq` 增量读取。
4. ICP 消费者先按精确企业全称批量读取缓存。只有未过期、算法版本一致且缓存唯一记录数与上游报告总数完全相等的快照才复制到本次任务；其他企业继续实时查询。
5. 实时查询按当前代理模式分批运行：云函数模式按就绪节点数扩展（每节点5个逻辑槽位，最多40个），手动 HTTP 代理同一入口最多并发8家，直连最多 5 个在途企业并统一节流。ICP备案结果不会再次作为待查询企业，避免队列自反馈。
6. 每个实时企业执行分页查询、完整性检查、跨页去重和结果保存；确认完整后再原子替换企业缓存。ICP 阶段持续刷新任务心跳。
7. 任一阶段存在错误但已有结果时，任务状态为 `partial`；无错误时为 `succeeded`。

## 企业查询实时事件

- 迁移 `026_collection_event_stream.sql` 为 `relationships` 和 `results` 增加单调递增的 `stream_seq`，分别建立 `(run_id, stream_seq)` 索引；`027_subdomain_result_stream.sql` 为子域名结果建立更新游标。
- 迁移 `032_event_notifications.sql` 为关系、ICP、子域名结果以及任务状态变更建立触发器。PostgreSQL 在事务提交后向 `collection_events` / `subdomain_events` 发送任务 ID；API 用一条共享监听连接扇出到同一任务的所有 SSE 客户端，队列容量为 1，可合并连续写入。
- `GET /api/v1/collection-runs/{id}/events` 使用 SSE 批量发送新增投资关系、ICP 结果和任务摘要；每次最多读取1,000条增量，空闲时发送 keepalive。订阅在首次游标查询前注册，避免“读完后、等待前”发生提交而丢失唤醒；监听正常时按通知读取，15 秒超时仅用于保底校验。
- `GET /api/v1/subdomain-runs/{id}/results` 默认按页返回；支持 `keyword` 和 `view=all|web|wildcard` 服务端过滤，并同时返回三类总数。打开任务和历史详情先加载一页，导出 Excel 再分页拉全量。
- `GET /api/v1/subdomain-runs/{id}/events` 使用 `subdomain_results.stream_seq` 作为变更游标；DNS 初次写入和后续 HTTP 丰富字段更新都会被推送。SSE 的 `id` 保存当前游标，刷新页面或自动重连可通过初始参数 / `Last-Event-ID` 继续接收。
- `GET /api/v1/history` 合并 `collection_runs` 与 `subdomain_runs`，按创建时间倒序分页；`POST /api/v1/history/batch-delete` 按记录类型分别删除。历史查询页在当前页面打开详情，不再跳转到对应功能页。
- 初始 `QueryResponse` 在读取完整快照前先捕获关系/结果游标，投资关系和 ICP 记录一次完整读取，不再把 ICP 截成 10000 条。并发写入的数据可能重复出现在快照和增量中，但前端按稳定业务键合并，因此不会因竞态漏掉数据。任务结束时若 SSE 已收到不少于快照的行，前端会复用增量结果，避免再整表替换。
- 企业和子域名 SSE 都发送事件游标 ID；断开后浏览器通过 `Last-Event-ID` 从最后确认位置自动重连，避免从页面初始游标重复传输。企业任务最终状态到达后仍重新读取一次完整查询结果，校准多数据源合并、错误信息和最终数量。
- 投资/ICP/子域名结果表使用分页加虚拟滚动；企业结果表按稳定业务键渲染，列配置保持稳定，并忽略只改变进度、不改变状态或结果数组的父级重渲染。
- `DeferredQueryResultsPanel` 将 Ant Design `Table` 及分页依赖从空白查询页首屏移出；查询开始时预加载，深链结果返回后直接渲染。Excel 导出模块同样按点击加载。Vite 再把 React 和共享 Ant Design 运行时拆成稳定缓存块，避免普通业务改动使整套框架缓存失效。
- `POST /api/v1/collection-runs/{id}/cancel` 将排队或运行任务标记为 `cancelled` 并释放租约。ICP 心跳缩短到5秒，使运行协程及时发现租约失效并退出，同时保留已保存结果。

## 子域名并发与缓存

- 多个主域名以最多 `ROOT_CONCURRENCY=5` 个根任务并发处理；总 DNS 并发和 HTTP 并发仍分别受 `DNS_CONCURRENCY`、`HTTP_CONCURRENCY` 限制，避免把单域名优化成全局洪峰。纯字典命中的泛解析结果会跳过 HTTP 探测。
- `subdomain_source_cache` 缓存被动来源；数据库结果使用 `(run_id, root_domain, hostname)` 去重。
- `subdomain_api_settings` 保存可选的 FOFA / Hunter 密钥；采集时仅在密钥存在时调用对应接口。
- FOFA 查询 `domain="主域名"`，最多 3 页、每页 1000 条；Hunter 查询 `domain_suffix="主域名"`，最多 5 页、每页 100 条。两者都只贡献候选名，仍须通过 DNS 验证后才会入库。
- 被动来源先走容器直连；遇到连接/超时、403 或 5xx 时，若已配置并验证手动代理或 SeaMoon 路由，则立即通过代理重试，不等待下一轮。DNS 解析和发现主机的 HTTP 探测不自动改走该兜底代理。
- CertSpotter 的分页 `Link` 既支持绝对地址也支持相对地址，会先解析为完整 URL，避免第二页出现 `unknown url type`。
- `027_subdomain_result_stream.sql` 为结果增加单调递增变更游标，避免“先展示 DNS、后补充 HTTP”时前端长期看不到更新。

## ICP 企业级缓存

- 表：`icp_company_cache`；键为规范化后的精确企业全称。
- 非零完整结果默认保存24小时；明确 `total=0` 的成功结果默认保存6小时。
- 缓存记录保存上游 `reported_total`、实际唯一记录数 `saved_total`、原始记录数组、算法版本、检查时间和过期时间。
- 命中时仍会重新按当前 ICP 记录键去重并复核数量；任一字段缺失、数量不等、内容损坏、版本变化或过期都会强制实时查询。
- 只有实时查询唯一记录数精确达到上游 `total` 时才写缓存。超时、WAF、接口异常、分页不足和部分成功不会污染缓存。
- `collection_runs.icp_cache_hits` 与 `icp_live_queries` 记录本轮按企业统计的缓存/实时口径，供查询页和历史详情展示。
- 迁移不会从旧历史结果倒推缓存，因为旧记录没有保存可验证的上游总数；部署此版本后首次完整实时查询会建立缓存，后续查询才能安全命中。

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
ICP_PAGINATION_RECOVERY_PASSES = 1
ICP_EMPTY_RESULT_RECOVERY_PASSES = 1
ICP_BATCH_SIZE                  = 5
ICP_CONCURRENCY                 = 40
ICP_PROXY_REQUEST_LIMIT         = 5
ICP_PROXY_WAF_RETRIES           = 2
ICP_PROXY_ERROR_RETRIES         = 1
ICP_PAGE_TIMEOUT_SECONDS        = 90
ICP_COMPANY_TIMEOUT_SECONDS     = 80
ICP_COMPANY_MAX_ATTEMPTS        = 2
ICP_COMPANY_RETRY_BACKOFF_SECONDS = 0.5
INVEST_CONCURRENCY              = 20
```

算法：

1. 云函数按就绪节点分配固定 `lane_{slot}_{generation}` 会话；同一通道的多家企业复用热隧道。
2. 同一通道内的分页和连续查询复用 YMICP 认证上下文，直到创宇盾/传输失败才提升 generation。
3. 解析每页的 `list`、`pages` 和 `total`。
4. 对不同轮次的记录按 `domain + serviceLicence` 合并；域名为空时退化使用 `mainLicence`、`mainId` 或企业名称。
5. 唯一记录数等于 `total` 时立即结束并写入数据库。
6. 超过 `total` 时报告结果不一致；恢复轮次耗尽仍不足时报告结果不完整。
7. 单页始终受90秒双重硬超时约束（覆盖冷启动 JSL+验证码）；每次企业级尝试受80秒预算约束，整家企业分页包在该预算内。同一云函数通道在 5 次查询后暂停 8 秒，不重建隧道。
8. `total=0` 或已返回有效记录时立即结束；正数 `total` 但空列表仅执行1次快速恢复。
9. 一轮完成后只重新调度失败企业，默认最多执行2次企业级尝试，轮次间短退避；每次尝试从新的分页会话开始。
10. 同一轮使用滚动信号量维护并发槽位，快速企业释放槽位后立即启动下一家，不再按固定切片等待最慢企业完成。

结果在确认完整前保存在内存集合中，避免把已知不完整的企业分页静默保存为成功结果。数据库同时使用唯一索引防止重复写入。

## 并发与路由

### 云函数路由

- 按就绪云节点数扩展，每节点5个逻辑槽位，最多40个逻辑并发；7个就绪节点时目标为35个槽位。
- `ICP_PROXY_REQUEST_LIMIT=5` 按实际 ICP 页面请求计数，不按企业数计数。
- `ICP_BATCH_SIZE=5` 是单节点基准槽位，不是整个云池的固定并发；投资关系遍历与 ICP 消费者并行执行。
- 达到请求轮换阈值后，下一次请求使用新的路由代次和新的 YMICP 分页会话，迫使 YMICP 建立新的 HTTP 代理 TCP 连接/SeaMoon WebSocket 隧道。
- 创宇盾拦截时对当前页最多重建2次隧道并重试，不从第一页重新查询。
- 每家企业拥有独立分页会话；云函数路由代次变化时会为当前页切换到新的会话键。单个手动代理保持同一分页会话，不套用云函数代次轮换。
- 云函数批次不使用直连出口的12秒全局冷却；传输错误只做1次页面级快速换隧道，后续交给企业级重试。
- 云函数单实例并发6，保留1个槽位。
- 一个代理 TCP 连接对应一个 WebSocket 隧道；单个 FC 函数地址仍不保证获得不同的公网出口IP。同一函数在并发升高时由云平台复用或新建容器，最小实例为 0 时第一次请求可能冷启动。
- 平台扩容先加不同地域的函数；地域用尽后才在健康地域部署 `function-r2` 这类副本。网关单次握手最多尝试 2 个节点，握手超时 12 秒，避免把整池冷启动失败叠加到一次 ICP 页面请求上。

### 手动代理路由

- 快代理这类每次请求换 IP 的入口，同一 URL 最多并发 8 家企业；多个入口再叠加，上限 40。
- 每个请求使用独立 YMICP 会话，不粘滞同一出口，也不套用云函数 5 次 / 8 秒窗口。
- 不同手动代理节点仍然会轮询使用。

### 直连路由

- 企业顺序执行。
- 请求之间等待约0.4秒。
- 每累计5次请求等待8秒。
- 页请求而不是企业数量消耗该计数。

## 数据模型

主要表：

- `collection_runs`：不可变请求、状态、进度、租约、错误和时间。
- `entities`：按 `(provider, external_id)` 归一化的企业实体。
- `relationships`：投资关系、层级、持股比例和来源。
- `results`：企业选择、投资结果和 ICP 结果。
- `provider_sessions`：需要登录的数据源会话。
- `serverless_proxy_settings`：云平台配置、函数地址、状态、凭证和本轮代理池选择。
- `manual_proxy_nodes`：自定义 HTTP 代理。
- `subdomain_api_settings`：FOFA / Hunter 密钥。
- `icp_company_cache`：按企业全称保存完整 ICP 快照。

`results` 使用任务、实体、类别和 payload 摘要去重；ICP 采集还在进程内按域名和服务备案号去重。

## 前端分页

- 历史任务列表：服务端分页，通过 `limit` 和 `offset` 请求。
- 对外投资、ICP备案结果：查询视图一次返回，前端本地分页。
- 子域名结果：服务端按页返回；运行中可叠加 SSE 增量，完成后翻页、搜索和分类筛选均请求服务端当前页及分类总数。
- 默认20条/页，可选10、20、50、100条。
- `TableFrame` 固定分页栏，`.table-host` 负责内部滚动。
- 历史详情与历史列表互斥渲染，避免同时出现两套分页。

## 安全边界

- API 不接收来自前端的任意代理 URL。
- YMICP 仅允许配置的内部 SeaMoon 代理地址。
- 只有 ICP 和爱企查业务查询经过 SeaMoon；其他数据源和登录链路不经过 SeaMoon。
- 云平台 Secret 不回传前端。
- API 当前没有登录和租户隔离，只应运行在本机或受控私网。


## 子域名查询链路

```text
单个/批量输入，或选择 ICP 结果
  -> POST /api/v1/subdomain-runs
  -> subdomain_runs 等待队列
  -> 独立 Subdomain Worker
  -> 公开数据源（证书透明度、公开数据集、Wayback、DNS 记录、站点元数据、Common Crawl，6 小时缓存、限速/错误分类）
     + 可选 FOFA / Hunter
     + DNS 字典并行
  -> 三点泛解析判断 -> DNS 验证 -> 安全重定向校验 -> 可选 HTTP 探测
  -> subdomain_results
  -> SSE /events 实时推送到前端
```

数据表：

- `subdomain_runs`：域名列表、来源 ICP 任务、选项、阶段、进度、警告和租约。
- `subdomain_results`：主域名、子域名、解析地址、CNAME、泛解析标记、HTTP 信息和来源。
- `subdomain_source_cache`：按主域名和来源缓存被动发现结果，默认有效期 6 小时。
- `subdomain_api_settings`：可选 FOFA / Hunter 密钥。

子域名 Worker 与企业/ICP Worker 分离，默认并发数由
`SUBDOMAIN_WORKER_CONCURRENCY` 控制。任务同样使用数据库租约，进程异常后会重新排队。


### 子域名并发与实时性

- 公开来源按接口全局串行（不同接口仍可并行），每个来源硬超时 20 秒；网络/超时/5xx 进行有限重试，短冷却会等待后继续，超过等待上限才跳过本轮；连接失败可走已验证代理兜底，429 仅进入来源冷却。一个来源异常不会终止整条任务。
- DNS 字典不等待被动来源结束即可开始验证，已解析结果先写数据库并通过 SSE 推送；被动来源按完成顺序增量合并，不再等待所有来源返回。
- DNS 与 HTTP 使用独立固定 worker 队列，取消每 500 个候选的整批屏障。根域名、DNS、HTTP 的进程共享上限分别为 5、64、20；HTTP 队列容量为 40，满时等待，不丢弃候选。DNS 使用 64 线程专用执行器，线程排队时间不计入解析超时，暂时失败重试一次，持续失败显式标记缺口。
- 进度与租约心跳按批次/时间节流，避免为每个无效候选执行一次数据库更新。
- SSE 增量查询不执行 `count(*)`。运行中前端按 `id` 合并 DNS 与 HTTP 更新；完成后按页向服务端取结果，导出时再拉全量以同步全部来源标签。响应正文只提取同主域名引用，不保存正文。
- DNS 命中先批量 upsert，HTTP 探测完成后再批量更新，避免慢站点挡住已解析结果。


### 覆盖范围与边界

OneForAll 的核心流程已映射为本地 Python 实现：证书透明度、公开 DNS/数据集、站点元数据、DNS 解析、泛解析判断、HTTP 丰富、页面引用提取和 AltDNS 风格变体。FOFA、Hunter 作为可选情报源，密钥保存在基础配置，未填写时不会请求。CDN/指纹、接管检查、端口扫描和 AXFR 仍未默认接入；避免把高风险或高配额成本模块隐式打开。公开数据源本身存在缓存、历史数据和限流差异，结果应以 DNS 验证后的合并列表为准。

### 查询调度完整性（2026-09-09）

`providers/pagination.py` 统一四个数据源的分页与来源请求预算；未知总数不伪造为当前页条数，已知总数按唯一主体核对。`IncompletePagination.rows` 将有效部分交还 collector，错误仍进入任务状态。投资遍历逐层并发，以深度屏障保证最短路径；ICP 消费者结束前核对一次完整企业集合，消除序列号先分配、后提交造成的漏投递。

`concurrency.py` 统一管理队列和协程生命周期：worker 异常立即向上冒泡，父任务取消时等待所有子任务退出。子域名任务每 5 秒独立续租，等待来源或共享根域名槽位也不会误过期。持久化错误与租约丢失不再降格为可忽略的来源警告。

来源缓存使用 `v2:<来源名>` 键，过往缺少完整性信息的缓存不复用；达到来源请求额度的候选仍参与 DNS 校验，但不写入完整来源缓存。该变化复用 `subdomain_source_cache` 现有结构，无新增表或迁移。企业请求收到 HTTP 429 后按来源共享冷却，ICP 429 同样进入进程共享冷却且停止企业重试与会话轮换；子域名来源收到 429 不通过代理兜底。
