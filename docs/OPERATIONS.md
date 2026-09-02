# 运行与排障

> 最后更新：2026-09-01

## 常用命令

```bash
# 构建并启动
docker compose -p asset-workbench up -d --build

# 查看状态
docker compose -p asset-workbench ps

# 重建 API 和 worker
docker compose -p asset-workbench build api
docker compose -p asset-workbench up -d --no-deps --force-recreate api worker

# 健康检查
curl http://127.0.0.1:8000/api/health
```

## YMICP

```bash
# 查看状态
docker ps --filter name=ymicp
curl 'http://127.0.0.1:16181/query/web?search=测试企业&pageNum=1&pageSize=26'

# 重新应用仓库补丁
./scripts/apply_ymicp_patch.sh

# 连接 Compose 网络（仅首次需要）
docker network connect asset-workbench_default ymicp
```

补丁脚本假定容器名为 `ymicp`，会复制：

- `scripts/ymicp_jsl.py`
- `scripts/ymicp_patched.py`
- `scripts/query_routes_patched.py`
- `scripts/batch_routes_patched.py`

随后安装 `quickjs`、把验证码重试次数调整为3并重启容器。

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

### “单企业云函数代理查询总预算达到30秒”

检查：

1. YMICP 日志中该企业实际请求了哪些页。
2. 是否存在单页超过10秒。
3. 是否出现验证码、非 JSON、HTTP 5xx、521 或创宇盾拦截。
4. 云函数是否处于冷启动或触发器异常状态。
5. 当前代码是否错误地对云函数应用了直连12秒冷却；现行版本不应这样做。

### “上游报告 N 条，分页合并后仅获取 M 条”

工信部接口可能返回重叠页。平台会执行有限恢复分页；30秒预算内仍未达到 `total` 时标记部分成功，避免把不完整数量当成最终结果。

### 历史记录显示“部分成功”

查看 `collection_runs.error` 或历史详情顶部警告。只要已有投资或 ICP 数据，单个企业/数据源失败会保留有效结果并标记 `partial`。

### 点击历史记录后出现上下两块

现行界面采用列表/详情互斥渲染。点击记录后只显示详情，并提供“返回列表”。如仍看到两块，清理浏览器缓存或确认 API 容器已经包含最新前端构建。

### ICP 云函数请求大量出现“创宇盾拦截”

确认应用日志中的 YMICP 请求带有 `proxy=http://seamoon-gateway:19080`。当前 ICP 路由按实际页面请求每5次切换 YMICP 会话并重建 SeaMoon 隧道；创宇盾响应会对当前页最多重试2次。需要注意，单个 FC 函数地址无法保证每次隧道获得不同公网 IP；若仍持续拦截，应降低并发/请求速率，或配置多个云函数出口、云 NAT/EIP。

若同时看到大量“天眼查：请登录以使用完整功能”，这是天眼查匿名接口权限限制，需要登录天眼查或改用已登录的数据源，代理轮换不能补全该阶段数据。

### `FunctionAlreadyExists`

部署逻辑应复用并更新同名函数，而不是直接失败。若仍报错：

1. 确认运行的是最新 `seamoon-core`。
2. 查看 API 的 `last_error` 和云平台请求 ID。
3. 检查函数地域和函数名是否与平台配置一致。

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
