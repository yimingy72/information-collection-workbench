# 运行与排障

> 最后更新：2026-09-09

## 常用命令

```bash
# 构建并启动
docker compose -p asset-workbench up -d --build

# 查看状态
docker compose -p asset-workbench ps

# 本地开发镜像（前端由宿主机 Vite 提供）
docker build -f Dockerfile.local -t asset-workbench-api:latest .
docker compose -p asset-workbench -f docker-compose.yml -f docker-compose.local.yml up -d --no-build --force-recreate api worker

# 生产构建
# 前端应生成 react-vendor、antd-runtime、Table 等独立块，且不再出现默认 500 kB 大块警告
docker compose -p asset-workbench build api
docker compose -p asset-workbench up -d --no-deps --force-recreate api worker

# 健康检查
curl http://127.0.0.1:8000/api/health
```

## YMICP

YMICP 已纳入 `docker compose -p asset-workbench up -d --build`。容器名仍为 `ymicp`，默认地址 `http://ymicp:16181`。

```bash
# 查看状态
docker compose -p asset-workbench ps ymicp
curl 'http://127.0.0.1:16181/query/web?search=测试企业&pageNum=1&pageSize=26'

# 重建已打补丁的镜像
docker compose -p asset-workbench up -d --build ymicp

# 热更新运行中容器的补丁
./scripts/apply_ymicp_patch.sh
```

补丁在镜像构建时已经写入。热更新脚本会复制：

- `scripts/ymicp_jsl.py`
- `scripts/ymicp_patched.py`
- `scripts/query_routes_patched.py`
- `scripts/batch_routes_patched.py`

随后安装 `quickjs`、把验证码重试次数调整为3并重启容器。不要再手工 `docker run yiminger/ymicp` 后 `docker network connect`。

## 日志

```bash
# API
docker logs --tail 200 asset-workbench-api-1

# Worker
docker logs --tail 200 asset-workbench-worker-1

# YMICP
docker logs --tail 200 ymicp

# SeaMoon 网关
docker logs --tail 200 asset-workbench-seamoon-gateway-1

# 指定时间范围
docker logs \
  --since '2026-09-01T16:00:00+08:00' \
  --until '2026-09-01T16:10:00+08:00' \
  asset-workbench-worker-1
```

Docker 日志默认使用容器内部时区；YMICP 日志常显示 UTC。排障时同时核对任务表中的 `created_at / started_at / finished_at`。

## 数据库检查

```bash
docker exec -it asset-workbench-postgres-1 \
  psql -U workbench -d workbench
```

常用 SQL：

```sql
SELECT id, keyword, status, error, created_at, started_at, finished_at
FROM collection_runs
ORDER BY created_at DESC
LIMIT 20;

SELECT e.name, count(*)
FROM results r
JOIN entities e ON e.id = r.entity_id
WHERE r.run_id = '<run_id>' AND r.category = 'icp'
GROUP BY e.name
ORDER BY count(*) DESC;
```

## 备份与恢复

```bash
docker compose -p asset-workbench exec -T postgres \
  pg_dump -U workbench workbench > backup.sql

docker compose -p asset-workbench exec -T postgres \
  psql -U workbench workbench < backup.sql
```

恢复前应停止 API 和 worker，避免恢复期间继续写入。

## 常见问题

### “单企业云函数代理查询总预算达到80秒”

检查：

1. YMICP 日志中该企业实际请求了哪些页。
2. 是否存在单页超过36秒。
3. 是否出现验证码、非 JSON、HTTP 5xx、521 或创宇盾拦截。
4. 云函数是否处于冷启动或触发器异常状态。
5. 当前代码是否错误地对云函数应用了直连12秒冷却；现行版本不应这样做。
6. 查询页“代理”下拉框决定本轮路由。直连模式保留单 IP 的约 0.4 秒间隔和每 5 次 8 秒冷却；云函数和自定义代理不套用该窗口。没有就绪云函数节点或可用手动代理时，不会静默改走直连。

单次80秒预算耗尽后，该企业会进入后续企业级重试轮次。默认总计尝试2次，轮次间等待0.5秒；只有全部尝试均失败时，任务才记录该企业失败并标记为部分成功。

### 青岛等地域 502 / `executable file not found`

已部署节点如果仍是空 Entrypoint，即使当前 `/_health` 能通，也可能在冷启动后再次变成 502。重新部署会把 `Entrypoint=/app/seamoon` 写回函数。最小实例为 0 时，没有查询请求通常不会产生函数调用费；镜像仓库、日志、公网出网等仍可能产生少量费用。

官方 SeaMoon 镜像的 ENTRYPOINT 是 `/app/entrypoint.sh`，实际二进制是 `/app/seamoon`。如果函数只配置 `Command: server -p 9000 -t websocket` 且 Entrypoint 为空，部分地域会把 `server` 当成可执行文件，容器起不来并返回 502。阿里云部署会显式设置 `Entrypoint=/app/seamoon`；腾讯云会设置 `Command=/app/seamoon`。已有异常节点需要重新部署后才会更新函数配置。腾讯云已存在的同名函数还会调用 `UpdateFunctionCode` 同步镜像和启动参数，已存在的 HTTP 触发器会复用公网地址而不是直接失败。

### ICP 自动扩容节点池

当本轮已发现企业数量增长时，worker 会按 `ICP_TARGET_SECONDS`、`ICP_AUTO_SCALE_COMPANIES_PER_NODE` 和 `ICP_AUTO_SCALE_MAX_NODES` 计算目标节点数。新增云函数必须先通过 WebSocket 探活，失败节点保持 disabled/error，不会污染健康节点。默认最多 8 个函数节点：先使用不同大陆地域，地域用尽后在健康地域追加副本函数，不再因为“没有更多地域”直接停止扩容。每个函数内部由阿里云按并发自动创建/复用容器，不是“一个地域永远只有一个容器”。批次开始前会对就绪节点访问 `/_health` 预热；网关握手超时 12 秒，单次最多再试 1 个备用节点。5 分钟是健康依赖条件下的目标，不对第三方接口、WAF、云配额或网络中断作绝对保证。

### “上游报告 N 条，分页合并后仅获取 M 条”

工信部接口可能返回重叠页。平台会执行有限恢复分页；80秒预算内仍未达到 `total` 时标记部分成功，避免把不完整数量当成最终结果。

### 子域名查询长时间停留在“等待中”

检查 worker 是否运行，以及数据库中 `subdomain_runs` 的 `status`、`heartbeat_at` 和
`lease_id`。子域名任务使用独立协程，数量由 `SUBDOMAIN_WORKER_CONCURRENCY` 控制。
网络/超时/5xx 等被动数据源错误会进行有限重试；429 会进入该来源的限流冷却，不再立即重复请求，也不通过代理轮换硬绕过。最终失败才写入简短警告；DNS 字典和其他来源仍会继续。相同主域名、相同来源的成功结果默认缓存 6 小时，因此第二次查询通常会更快。

常用 SQL：

```sql
SELECT id, domains, status, phase, progress, total, discovered, warnings, error, created_at
FROM subdomain_runs
ORDER BY created_at DESC
LIMIT 20;
```

### 历史记录显示“部分成功”

查看 `collection_runs.error` 或历史详情顶部警告。只要已有投资或 ICP 数据，单个企业/数据源失败会保留有效结果并标记 `partial`。

### 点击历史记录后出现上下两块

现行界面采用列表/详情互斥渲染。点击记录后只显示详情，并提供“返回列表”。如仍看到两块，清理浏览器缓存或确认 API 容器已经包含最新前端构建。

### ICP 云函数请求大量出现“创宇盾拦截”

确认应用日志中的 YMICP 请求带有 `proxy=http://seamoon-gateway:19080`。当前 ICP 路由按节点复用热隧道：每通道连续 5 次查询后补足 8 秒窗口剩余等待，不重建会话；创宇盾/传输失败才轮换 generation。SeaMoon 网关按 lane_{slot}_{generation} 的 slot 固定打到同一个云函数，避免不同通道哈希撞到同一出口 IP。需要注意，单个 FC 函数地址无法保证每次隧道获得不同公网 IP；若仍持续拦截，应降低并发/请求速率，或配置多个云函数出口、云 NAT/EIP。

若同时看到大量“天眼查：请登录以使用完整功能”，这是天眼查匿名接口权限限制，需要登录天眼查或改用已登录的数据源，代理轮换不能补全该阶段数据。

### 单个手动代理开启后 ICP 反而变慢

检查 YMICP 日志是否同时出现大量页面超时。手动代理模式当前总并发为 `min(40, max(8, 可用入口数))`；这一策略适配每次请求更换出口的代理服务，普通固定出口不能据此假设有多个 IP。YMICP 的独立分页会话不再共用全局验证码锁，避免一个验证码请求阻塞其他代理节点或 SeaMoon 隧道。

### `FunctionAlreadyExists`

部署逻辑应复用并更新同名函数，而不是直接失败。若仍报错：

1. 确认运行的是最新 `seamoon-core`。
2. 查看 API 的 `last_error` 和云平台请求 ID。
3. 检查云平台默认地域和函数名是否与控制台中的函数一致（阿里云默认 `cn-hangzhou`，腾讯云默认 `ap-guangzhou`，函数名均为 `asset-workbench-seamoon`）。

### `Either Code or CustomContainerConfig must be set`

阿里云 FC 的 `CreateFunction` 请求必须在 `code` 和
`customContainerConfig` 中二选一。SeaMoon 使用自定义容器镜像，部署请求应携带
`customContainerConfig.image`、监听端口 `9000` 以及 `server -p 9000 -t websocket`
启动参数。更新同名函数时也要同步提交相同的自定义容器配置，否则首次创建失败后的
重试或旧函数更新仍可能失败。

## 测试

```bash
cd backend && pytest -q
cd ../frontend && npm run typecheck && npm run build
cd ../seamoon && go test ./...
```

## 非破坏性原则

排障默认只读取日志、查询数据库和发送普通查询请求。不要删除任务、云函数、数据库卷或云资源，除非用户明确要求并确认影响范围。


### 被动来源结果未立即更新

实时列表会优先显示 DNS 字典已经验证的结果。较慢的被动来源随后命中同一子域名时，数据库会合并来源标签，SSE 按同一结果 `id` 更新来源。完成后前端按页加载，不再一次拉全量；导出 Excel 时才会分页拉完全部结果。

检查缓存：

```sql
SELECT root_domain, source, jsonb_array_length(hosts) AS host_count,
       refreshed_at, expires_at
FROM subdomain_source_cache
ORDER BY refreshed_at DESC
LIMIT 50;
```

如需排障某个来源，可只删除对应的过期/错误测试缓存；不要删除任务或业务结果。正常情况下等待 6 小时自动过期即可。


### 如何提高结果覆盖率

默认查询已经组合公开证书、DNS 数据集、SRV 记录、站点元数据、Common Crawl、DNS 字典、页面引用和有限名称变体。对于已获授权的目标，建议保持“被动数据源、DNS 字典和智能变体”开启；如果只需要快速初筛，可关闭智能变体和 HTTP 探测。FOFA 和 Hunter 需要在“基础配置 → 数据源配置 → 子域名情报源”填写 API Key 后才会参与子域名收集；未填写时跳过这两个来源。平台不会默认开启 AXFR、接管或端口类检查。

检查密钥是否已保存：

```sql
SELECT fofa_email,
       (fofa_key <> '') AS has_fofa_key,
       (hunter_key <> '') AS has_hunter_key,
       updated_at
FROM subdomain_api_settings
WHERE id = 1;
```

FOFA 请求 `https://fofa.info/api/v1/search/all`，查询 `domain="主域名"`；Hunter 请求 `https://hunter.qianxin.com/openApi/search`，查询 `domain_suffix="主域名"`。接口报额度/权限错误会写入该主域名警告，不影响其他来源。

结果不完整时先检查任务警告和 `subdomain_source_cache`，再等待限流来源冷却后重新查询。不要用代理轮换连续冲击返回 429 的来源；这不能保证增加结果，反而可能扩大限流范围。

## ICP 缓存排障

默认配置：

```env
ICP_CACHE_ENABLED=true
ICP_CACHE_TTL_HOURS=24
ICP_ZERO_CACHE_TTL_HOURS=6
```

查询页结果头的“ICP：缓存 N 家 · 实时 M 家”按企业统计。只有完整性证明仍成立的缓存才计入命中；首次查询、缓存过期、算法升级、数量不一致或缓存内容损坏都会计入实时查询。

只读检查：

```sql
SELECT company_name, reported_total, saved_total, complete, query_version,
       checked_at, expires_at
FROM icp_company_cache
ORDER BY checked_at DESC
LIMIT 50;

SELECT id, keyword, icp_cache_hits, icp_live_queries, status, created_at
FROM collection_runs
ORDER BY created_at DESC
LIMIT 20;
```

不要把失败任务或旧 `results` 表数据手工补进缓存。旧历史结果缺少当时上游 `total` 的完整性证据，直接回填可能让不完整记录阻止实时查询。正常做法是让该企业完成一次实时查询，由程序在数量严格相等后自动建立或替换缓存。

## 企业查询实时输出与停止

查询页面提交后应立即进入 `#/collection/{run_id}`，并建立：

```text
GET /api/v1/collection-runs/{run_id}/events
```

该 SSE 会推送 `delta`、`progress` 和 `done`。如果页面一直显示加载但表格没有增量：

1. 在浏览器网络面板确认事件请求保持连接且响应类型为 `text/event-stream`。
2. 检查迁移 `026_collection_event_stream.sql` 和 `032_event_notifications.sql` 是否应用。
3. 检查 `relationships.stream_seq`、`results.stream_seq` 是否持续增长，并确认 `relationships_event_notify`、`icp_results_event_notify`、`collection_runs_update_event_notify` 触发器存在。
4. 检查 API 日志是否出现“PostgreSQL 事件监听启动失败”；监听不可用时会回退短轮询，监听已启动但通知偶发丢失时仍会每 15 秒保底校验。
5. SSE 临时中断会携带 `Last-Event-ID` 从最后确认游标自动重连，完成事件后页面还会重新读取一次最终快照。

页面“停止”按钮调用：

```text
POST /api/v1/collection-runs/{run_id}/cancel
```

停止只改变任务状态并释放租约，不删除已采集数据。ICP 心跳间隔为5秒，因此极端情况下仍可能在停止后短暂写入已完成的一页，随后协程会退出。


## 子域名实时结果与恢复

- 页面首次进入任务时先读取一页已保存结果，再从 `stream_seq` 游标建立 SSE；刷新浏览器不会把历史结果清空，也不会丢失进行中的任务。`subdomain_results_event_notify`、`subdomain_results_update_event_notify` 和 `subdomain_runs_update_event_notify` 在事务提交后唤醒实时流。
- DNS 结果和 HTTP 探测结果可能是同一主机的两次写入，前端按结果 `id` 合并，后一次会补全状态码、标题、访问地址和来源。
- 任务列表默认展示最近 30 条记录，并每 3 秒刷新一次进行中的摘要；SSE 断线时浏览器从最后确认的 `stream_seq` 自动重连。任务完成后，翻页、搜索及“网站可访问 / 泛解析”筛选都向服务端取当前页和分类总数。
- 多主域名默认同时处理 5 个根域名；如需降低压力，调整 `backend/app/subdomains.py` 的 `ROOT_CONCURRENCY`，不要直接取消 DNS/HTTP 全局并发限制。
- 被动数据源采用“直连优先、已验证代理立即兜底”：连接失败、超时、403、5xx 等情况会立即尝试当前可用的手动代理或 SeaMoon 路由；代理兜底成功不会把该来源标记为失败。
- CertSpotter 返回的相对分页链接会自动拼接到 `api.certspotter.com`，因此不会因 `Link: </v1/issuances?...>` 导致分页失败。

## 云函数节点自动回收

- 扩容发生在 ICP 消费者发现更多企业、且达到扩容阈值时；节点探活成功后才加入网关。
- 缩容只发生在本轮 ICP 企业全部消费完成之后，按最终企业数计算目标节点数。
- 缩容失败只记录错误，不影响已完成结果；下次任务仍可再次修复/回收。
- 自动缩容不会删除主节点、手动节点或没有 `auto_managed` 标记的历史节点。

## 性能调优与完整性核对（2026-09-09）

- 并发先看 [审计报告](PERFORMANCE_AUDIT.md)。`PROVIDER_REQUEST_CONCURRENCY=20` 是每个企业数据源、每个进程的请求总上限；`PROVIDER_PAGE_CONCURRENCY=4` 受这个上限约束。不要用增加 worker 副本绕过来源限流，多个进程不共享内存预算。
- 子域名根域名 / DNS / HTTP 的进程共享预算为 5 / 64 / 20。HTTP 队列满时等待，不丢结果；专用 DNS 线程池不会把线程排队误判成域名不存在。暂时解析失败重试一次，仍失败会出现“结果不完整”。
- ICP 返回 HTTP 429 时本进程暂停实时请求，不轮换会话或重试当前企业；等待冷却结束再重新查询，缓存命中仍可使用。
- ICP 缺少 `total`、唯一数不相等或超过现有时间预算时，已获取行仍保留，缓存保持不完整结果不写入的规则。大企业不再按 50 页直接中止，但超时依然有效。
- `subdomain_source_cache.source` 的新值形如 `v2:CertSpotter`。旧缓存保留到自然过期，不作为新算法的完整结果使用；首次升级后查询可能多一些实时来源请求。
- 子域名出现 `[结果不完整]` 表示确认有分页或解析缺口，即使已有结果也显示部分成功；普通可选来源超时仍按原产品规则处理。来源请求额度未自动增加，FOFA / Hunter 未配置密钥仍跳过。
- 任务取消与租约失效会收拢所有采集 worker；数据写入异常向上报告，避免队列一直等待或误标成功。子域名独立心跳每 5 秒执行一次。
- 离线验收：`python3 -m pytest backend/tests -q`、`cd frontend && npx tsc --noEmit -p tsconfig.json`；调度对比：`python3 scripts/benchmark_query_pipeline.py --baseline 0b18e24 --repeats 3`。比较结果包含完整记录集合和请求次数校验，不以少发请求或少保存结果换取提速。
