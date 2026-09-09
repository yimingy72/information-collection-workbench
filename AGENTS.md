# AGENTS

> 最后更新：2026-09-08

给在本仓库工作的编码代理使用。产品说明见 `PRODUCT.md`，实现细节见 `README.md`、`docs/ARCHITECTURE.md`、`docs/OPERATIONS.md`。

## 范围与授权

- 测试目标范围：仅限用户明确指定、且已获授权的企业、域名或备案核验任务。
- 已获授权：不要扩大到未点名的子公司、未授权域名、公网随机目标或生产账号。
- 仅允许非破坏性验证：查询、解析、HTTP 存活探测、缓存读取、日志和只读 SQL。
- 禁止数据破坏、禁止持久化攻击、禁止越权扩散。
- 不要默认开启接管检查、端口扫描、AXFR、爆破登录或对第三方 429 接口做代理轮换硬绕过。

<!-- CODEGRAPH_START -->
## CodeGraph

本仓库已索引 CodeGraph（根目录存在 `.codegraph/`）。需要理解或定位代码时，**先用 CodeGraph，再 grep/find**：

- **MCP**（若可用）：`codegraph_explore`。一次调用即可拿到相关符号的原文和调用路径，包括 grep 跟不到的动态分发。查询里写文件名或符号名，即可读到带行号的源码。
- **Shell**（始终可用）：`codegraph explore "<符号名或问题>"`，输出相同。

如果没有 `.codegraph/` 目录，完全跳过 CodeGraph，不要自行建索引。
<!-- CODEGRAPH_END -->

## 产品约束（勿回退）

- 查询页选择本轮代理：云函数 / 自定义 HTTP / 直连。基础配置只保存凭证和代理列表，不选择路由。
- 云函数表单只留：云平台、AccessKey ID、AccessKey Secret。默认阿里云 `cn-hangzhou`、腾讯云 `ap-guangzhou`、函数名 `asset-workbench-seamoon`。不要在配置页展示节点池、镜像、触发器地址或“接入已有函数”。
- 选择云函数但没有就绪节点，或选择自定义代理但没有可用入口时，禁止失败后静默直连。
- 完整性优先：不漏查、不截断 ICP / 投资 / 子域名结果；工信部 `total` 对不上不得标成功。
- 历史查询在当前页打开详情，不要跳到功能页，也不要列表和详情上下两套表格同时出现。
- 子域名打开任务先加载一页结果，运行中靠 SSE 增量；完成后按页拉取，导出 Excel 再拉全量。
- FOFA / Hunter 仅在基础配置填写 API Key 后启用；未填写则跳过。它们只贡献候选名，必须 DNS 验证后才入库。
- 前端使用 Ant Design 6，不要引入其它 UI 库或手写一套皮肤。
- 不要恢复已删除的免费代理池 / “查询代理”模块。

## 密钥与提交

- 不提交 `.env`、真实 AccessKey / Secret、Cookie、FOFA / Hunter Key。
- 不提交 `.frontend-dist-local/`、`frontend/node_modules/`、`frontend/dist/`。
- API 对密钥只返回“是否已配置”，不要把明文回传前端。
- 本地 Docker 常用项目名 `asset-workbench`。改后端后用 `Dockerfile.local` 重建 `api` / `worker`；compose bake 失败时直接 `docker build -f Dockerfile.local`。

## 验证

- 后端：`python3 -m pytest backend/tests -q`
- 前端：`cd frontend && npx tsc --noEmit -p tsconfig.json`
- 不要对工信部、天眼查、FOFA、Hunter 做未授权的实弹刷量。用户明确要求完整查询测试时，只打指定目标，并遵守现有超时、缓存和限速。

## 文档同步

行为变化时同步更新：

- `README.md`：能力、路由、环境变量
- `PRODUCT.md`：页面、口径、非目标
- `docs/ARCHITECTURE.md`：数据流和表
- `docs/OPERATIONS.md`：排障和本地重建

不要把过时规则写回去，例如：基础配置选择代理、查询失败改直连、子域名打开时一次拉全量、未配置 FOFA/Hunter 也去请求。
