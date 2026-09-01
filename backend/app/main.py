from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.collector import RunSpec, collect_run
from app.models import (
    CollectionRequest,
    IcpRow,
    InvestmentRow,
    ProviderSessionRequest,
    ProviderSessionView,
    QueryResponse,
    RelationshipItem,
    ResultItem,
    ResultsResponse,
    RunDetail,
    RunListResponse,
    RunSummary,
    ServerlessProxyDeployResponse,
    ServerlessProxyEnableResponse,
    ServerlessProxyRequest,
    ServerlessProxyTestResponse,
    SettingsResponse,
    ShareholderRow,
)
from app.providers.names import normalize_providers, provider_label
from app.providers.registry import build_providers, login_required_errors
from app.qr_login import cancel_qr, poll_qr, start_qr
from app.repository import Repository, create_pool
from app.runtime import SESSION_PROVIDERS, cookie_active
from app.session_refresh import refresh_provider_sessions
from app.serverless_proxy import (
    ServerlessProxyError,
    configure_gateway,
    run_cloud_operation,
    serverless_proxy_view,
    test_serverless_proxy,
    validate_saved_config,
)
from app.settings import settings

logger = logging.getLogger(__name__)


class DeleteRunsRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1)



def session_status(row: dict) -> str:
    if not str(row.get("cookie") or "").strip():
        return "logged_out"
    return "logged_in" if cookie_active(row) else "expired"


def settings_view(config: dict) -> SettingsResponse:
    raw = config.get("sessions") or {}
    sessions = []
    for provider in SESSION_PROVIDERS:
        row = raw.get(provider) or {"cookie": "", "expires_at": None, "updated_at": None}
        sessions.append(
            ProviderSessionView(
                provider=provider,
                label=provider_label(provider),
                status=session_status(row),
                expires_at=row.get("expires_at"),
                updated_at=row.get("updated_at"),
            )
        )
    return SettingsResponse(
        sessions=sessions,
        serverless_proxy=serverless_proxy_view(config),
    )


pool: asyncpg.Pool | None = None
repo: Repository | None = None


def current_repo() -> Repository:
    if repo is None:
        raise HTTPException(503, "database is not ready")
    return repo


def _providers_of(row: asyncpg.Record) -> list[str]:
    values = row.get("providers") if "providers" in row.keys() else None
    if isinstance(values, list):
        return normalize_providers(values)
    return normalize_providers([row["provider"]])


def run_summary(row: asyncpg.Record) -> RunSummary:
    providers = _providers_of(row)
    return RunSummary(
        id=row["id"], keyword=row["keyword"],
        provider=providers[0] if providers else row["provider"],
        providers=providers,
        depth=row["depth"], holding_percent=row["holding_percent"], include_branches=row["include_branches"],
        fields=row["fields"], status=row["status"], attempts=row["attempts"],
        progress=row["progress"], total=row["total"], error=row["error"],
        created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
    )


def _source_from(payload) -> str:
    if isinstance(payload, dict) and payload.get("source"):
        return provider_label(str(payload["source"]))
    return "天眼查"


SOURCE_ORDER = ("天眼查", "爱企查", "风鸟", "快查")


def _merge_sources(*values: str) -> str:
    names: list[str] = []
    for value in values:
        for part in str(value).replace(",", "、").split("、"):
            label = provider_label(part.strip()) if part.strip() else ""
            if label and label not in names:
                names.append(label)
    return "、".join(sorted(names, key=lambda name: SOURCE_ORDER.index(name) if name in SOURCE_ORDER else len(SOURCE_ORDER)))


def _merge_investments(rows: list[InvestmentRow]) -> list[InvestmentRow]:
    grouped: dict[tuple, InvestmentRow] = {}
    for item in rows:
        key = (item.parent_name, item.child_name, item.depth, item.holding_percent)
        current = grouped.get(key)
        if current is None:
            grouped[key] = item.model_copy(update={"source": provider_label(item.source)})
        else:
            grouped[key] = current.model_copy(update={"source": _merge_sources(current.source, item.source)})
    return list(grouped.values())


def relationship_item(row: asyncpg.Record) -> RelationshipItem:
    payload = row["raw_payload"] if "raw_payload" in row.keys() else {}
    data = dict(row)
    data.pop("raw_payload", None)
    data["source"] = _source_from(payload)
    return RelationshipItem(**data)


def parse_stored_request(raw: dict) -> CollectionRequest:
    payload = dict(raw)
    if payload.get("provider") == "tianyancha-anonymous":
        payload["provider"] = "tianyancha"
    if "providers" not in payload:
        payload["providers"] = normalize_providers([payload.get("provider") or "tianyancha"])
    payload.setdefault("fields", ["invest"])
    payload.setdefault("kind", "enterprise")
    return CollectionRequest.model_validate(payload)


def frontend_dir() -> Path:
    here = Path(__file__).resolve()
    for candidate in (
        here.parent.parent / "frontend" / "dist",
        here.parent.parent.parent / "frontend" / "dist",
    ):
        if candidate.exists():
            return candidate
    return here.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool, repo
    pool = await create_pool(settings.database_url, min_size=1, max_size=10)
    repo = Repository(pool, Path(__file__).parent.parent / "migrations")
    await repo.migrate()
    try:
        await configure_gateway(await repo.get_runtime_config())
    except ServerlessProxyError:
        logger.exception("SeaMoon 网关启动同步失败，将在查询时重试")
    refresh_task = asyncio.create_task(_session_refresh_loop())
    try:
        yield
    finally:
        refresh_task.cancel()
        await asyncio.gather(refresh_task, return_exceptions=True)
        await pool.close()
        pool = None
        repo = None


async def _session_refresh_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await refresh_provider_sessions(current_repo())
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("自动续期失败")
        await asyncio.sleep(15 * 60)


app = FastAPI(title="信息收集工作台 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    if pool is None:
        raise HTTPException(503, "database is not ready")
    await pool.fetchval("SELECT 1")
    return {"status": "ok"}


@app.post("/api/v1/collection-runs", response_model=RunSummary, status_code=201)
async def create_run(request: CollectionRequest) -> RunSummary:
    created = await current_repo().create_run(json.loads(request.model_dump_json()))
    row = await current_repo().get_run(created)
    return run_summary(row)


@app.post("/api/v1/queries", response_model=QueryResponse)
async def run_query(request: CollectionRequest) -> QueryResponse:
    store = current_repo()
    config = await refresh_provider_sessions(store)
    try:
        await configure_gateway(config)
    except ServerlessProxyError as exc:
        raise HTTPException(502, str(exc)) from exc
    login_errors = login_required_errors(request.providers, config)
    providers = build_providers(request.providers, config)
    if not providers:
        raise HTTPException(400, "；".join(login_errors) or "请先登录对应数据源")
    payload = json.loads(request.model_dump_json())
    run_id = await store.create_run(payload)
    await store.start_sync(run_id)
    errors: list[str] = list(login_errors)
    try:
        spec = RunSpec(
            id=run_id, keyword=request.keyword, depth=request.depth,
            holding_percent=float(request.holding_percent), fields=request.fields,
            providers=list(request.providers),
        )
        errors.extend(await collect_run(store, providers, spec))
        status = "partial" if errors else "succeeded"
        await store.finish(run_id, status, "；".join(errors) if errors else None)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        has_results = await store.has_results(run_id)
        await store.finish(run_id, "partial" if has_results else "failed", str(exc))
        if not has_results:
            raise HTTPException(502, str(exc)) from exc
    finally:
        for provider in providers:
            close = getattr(provider, "close", None)
            if close:
                await close()
    return await query_view(run_id)


async def query_view(run_id: UUID, extra_errors: list[str] | None = None) -> QueryResponse:
    row = await current_repo().get_run(run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    _, rels, _, _ = await current_repo().results(run_id, None, 1000, 0, 1000, 0)
    icp_rows, _, _, _ = await current_repo().results(run_id, "icp", 10000, 0, 0, 0)
    investments = _merge_investments([
        InvestmentRow(
            parent_name=item["parent_name"], child_name=item["child_name"],
            holding_percent=item["holding_percent"], depth=item["depth"],
            source=_source_from(item["raw_payload"] if "raw_payload" in item.keys() else {}),
        )
        for item in rels
    ])
    icp_records = [
        IcpRow(
            unit_name=item["payload"].get("unit_name") or item["entity_name"],
            main_licence=item["payload"].get("main_licence") or "",
            service_licence=item["payload"].get("service_licence") or "",
            domain=item["payload"].get("domain") or "",
            nature_name=item["payload"].get("nature_name") or "",
            update_time=item["payload"].get("update_time") or "",
            source=item["payload"].get("source") or "ICP备案",
        )
        for item in icp_rows
    ]
    shareholders: list[ShareholderRow] = []
    errors: list[str] = []
    for part in extra_errors or []:
        if part and part not in errors:
            errors.append(part)
    if row["error"]:
        for part in str(row["error"]).split("；"):
            if part and part not in errors:
                errors.append(part)
    return QueryResponse(
        run=run_summary(row),
        investments=investments,
        shareholders=shareholders,
        icp_records=icp_records,
        source_errors=errors,
    )


@app.get("/api/v1/queries/{run_id}", response_model=QueryResponse)
async def get_query(run_id: UUID) -> QueryResponse:
    return await query_view(run_id)


@app.get("/api/v1/collection-runs", response_model=RunListResponse)
async def list_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    keyword: str = Query("", max_length=200),
    status: str = Query("", max_length=32),
) -> RunListResponse:
    rows, total = await current_repo().list_runs(limit, offset, keyword, status)
    return RunListResponse(items=[run_summary(row) for row in rows], total=total)



@app.post("/api/v1/collection-runs/batch-delete")
async def delete_runs(request: DeleteRunsRequest) -> dict[str, int]:
    deleted = await current_repo().delete_runs(request.ids)
    return {"deleted": deleted}


@app.delete("/api/v1/collection-runs/{run_id}")
async def delete_run(run_id: UUID) -> dict[str, int]:
    deleted = await current_repo().delete_runs([run_id])
    if not deleted:
        raise HTTPException(404, "run not found")
    return {"deleted": deleted}


@app.get("/api/v1/collection-runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: UUID) -> RunDetail:
    row = await current_repo().get_run(run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    summary = run_summary(row)
    request = parse_stored_request(row["request"] if isinstance(row["request"], dict) else {"keyword": row["keyword"]})
    return RunDetail(**summary.model_dump(), request=request)


@app.get("/api/v1/collection-runs/{run_id}/results", response_model=ResultsResponse)
async def get_results(
    run_id: UUID,
    category: str | None = Query(None, pattern="^(company_selection|invest|partner|icp)$"),
    limit: int = Query(20, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    relationship_limit: int = Query(20, ge=1, le=1000),
    relationship_offset: int = Query(0, ge=0),
) -> ResultsResponse:
    if await current_repo().get_run(run_id) is None:
        raise HTTPException(404, "run not found")
    rows, rels, result_count, rel_count = await current_repo().results(
        run_id, category, limit, offset, relationship_limit, relationship_offset
    )
    return ResultsResponse(
        run_id=run_id,
        results=[ResultItem(**dict(row)) for row in rows],
        relationships=[relationship_item(row) for row in rels],
        total_results=result_count,
        total_relationships=rel_count,
    )


@app.get("/api/v1/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    return settings_view(await current_repo().get_runtime_config())


async def _serverless_proxy_payload(
    store: Repository,
    request: ServerlessProxyRequest,
) -> dict:
    payload = request.model_dump()
    current = (await store.get_runtime_config()).get("serverless_proxy") or {}
    if current.get("provider") != payload["provider"] and payload.get("access_key_secret") is None:
        payload["access_key_secret"] = ""
    return payload


@app.put("/api/v1/settings/serverless-proxy", response_model=SettingsResponse)
async def save_serverless_proxy(request: ServerlessProxyRequest) -> SettingsResponse:
    store = current_repo()
    current = (await store.get_runtime_config()).get("serverless_proxy") or {}
    payload = await _serverless_proxy_payload(store, request)
    same_deployment = all(
        current.get(key) == payload.get(key)
        for key in ("provider", "region", "function_name")
    )
    # Saving credentials/options must not unexpectedly switch traffic. If the
    # deployment identity changes, clear the old endpoint and require a new
    # one-click deployment before the proxy can be used again.
    payload["enabled"] = bool(current.get("enabled")) if same_deployment else False
    if not same_deployment:
        payload["endpoint"] = ""
    try:
        validate_saved_config(payload)
    except ServerlessProxyError as exc:
        raise HTTPException(400, str(exc)) from exc
    await store.update_serverless_proxy(payload)
    config = await store.get_runtime_config()
    try:
        await configure_gateway(config)
    except ServerlessProxyError as exc:
        await store.set_serverless_proxy_status("error", str(exc))
        raise HTTPException(502, str(exc)) from exc
    return settings_view(await store.get_runtime_config())


@app.post("/api/v1/settings/serverless-proxy/deploy", response_model=ServerlessProxyDeployResponse)
async def deploy_serverless_proxy(request: ServerlessProxyRequest) -> ServerlessProxyDeployResponse:
    store = current_repo()
    await store.update_serverless_proxy(await _serverless_proxy_payload(store, request))
    config = await store.get_runtime_config()
    row = config["serverless_proxy"]
    await store.set_serverless_proxy_status("deploying", enabled=False)
    try:
        result = await run_cloud_operation("deploy", row)
        endpoint = str(result.get("endpoint") or "").strip()
        if not endpoint:
            raise ServerlessProxyError("云平台未返回函数地址")
        await store.set_serverless_proxy_status(
            "deployed",
            endpoint=endpoint,
            deployment_id=str(result.get("deployment_id") or ""),
            enabled=False,
        )
        config = await store.get_runtime_config()
        await configure_gateway(config, force_enabled=False)

        # Deployment is not considered complete until the newly returned route
        # has passed the same real proxy check used by the manual Test action.
        test_result = await test_serverless_proxy(config)
        await store.set_serverless_proxy_status("ready", enabled=True)
        config = await store.get_runtime_config()
        await configure_gateway(config, force_enabled=True)
    except ServerlessProxyError as exc:
        await store.set_serverless_proxy_status("error", str(exc), enabled=False)
        await configure_gateway(await store.get_runtime_config(), force_enabled=False)
        raise HTTPException(502, f"云函数部署或验证失败：{exc}") from exc

    return ServerlessProxyDeployResponse(
        settings=settings_view(await store.get_runtime_config()),
        test=test_result,
    )


@app.post(
    "/api/v1/settings/serverless-proxy/enable",
    response_model=ServerlessProxyEnableResponse,
)
async def enable_serverless_proxy() -> ServerlessProxyEnableResponse:
    store = current_repo()
    config = await store.get_runtime_config()
    row = config["serverless_proxy"]
    if not str(row.get("endpoint") or "").strip():
        raise HTTPException(400, "请先一键部署云函数")
    await store.set_serverless_proxy_status("testing", enabled=False)
    try:
        test_result = await test_serverless_proxy(await store.get_runtime_config())
        await store.set_serverless_proxy_status("ready", enabled=True)
        config = await store.get_runtime_config()
        await configure_gateway(config)
        return ServerlessProxyEnableResponse(
            settings=settings_view(config),
            test=test_result,
        )
    except ServerlessProxyError as exc:
        await store.set_serverless_proxy_status("error", str(exc), enabled=False)
        config = await store.get_runtime_config()
        await configure_gateway(config)
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/v1/settings/serverless-proxy/disable", response_model=SettingsResponse)
async def disable_serverless_proxy() -> SettingsResponse:
    store = current_repo()
    config = await store.get_runtime_config()
    row = config["serverless_proxy"]
    status = "deployed" if row.get("deployment_id") else "configured"
    await store.set_serverless_proxy_status(status, enabled=False)
    config = await store.get_runtime_config()
    await configure_gateway(config)
    return settings_view(config)


@app.post(
    "/api/v1/settings/serverless-proxy/test",
    response_model=ServerlessProxyTestResponse,
)
async def test_serverless_proxy_route() -> ServerlessProxyTestResponse:
    store = current_repo()
    config = await store.get_runtime_config()
    await store.set_serverless_proxy_status("testing")
    try:
        result = await test_serverless_proxy(config)
        await store.set_serverless_proxy_status("ready")
        return result
    except ServerlessProxyError as exc:
        await store.set_serverless_proxy_status("error", str(exc))
        raise HTTPException(502, str(exc)) from exc


@app.delete("/api/v1/settings/serverless-proxy/deployment", response_model=SettingsResponse)
async def delete_serverless_proxy_deployment() -> SettingsResponse:
    store = current_repo()
    config = await store.get_runtime_config()
    row = config["serverless_proxy"]
    if row.get("provider") == "custom" or not row.get("deployment_id"):
        raise HTTPException(400, "当前配置不是由平台部署的云函数")
    try:
        await run_cloud_operation("destroy", row)
    except ServerlessProxyError as exc:
        await store.set_serverless_proxy_status("error", str(exc))
        raise HTTPException(502, str(exc)) from exc
    await store.clear_serverless_proxy_deployment()
    config = await store.get_runtime_config()
    await configure_gateway(config)
    return settings_view(config)


@app.put("/api/v1/settings/sessions/{provider}", response_model=SettingsResponse)
async def save_session(provider: str, request: ProviderSessionRequest) -> SettingsResponse:
    if provider not in SESSION_PROVIDERS:
        raise HTTPException(404, "不支持的数据源")
    store = current_repo()
    await store.upsert_provider_session(provider, request.cookie, request.expires_at)
    return settings_view(await store.get_runtime_config())


@app.delete("/api/v1/settings/sessions/{provider}", response_model=SettingsResponse)
async def clear_session(provider: str) -> SettingsResponse:
    if provider not in SESSION_PROVIDERS:
        raise HTTPException(404, "不支持的数据源")
    store = current_repo()
    await store.clear_provider_session(provider)
    return settings_view(await store.get_runtime_config())


@app.post("/api/v1/settings/sessions/{provider}/qr/start")
async def start_qr_login(provider: str) -> dict[str, object]:
    if provider not in SESSION_PROVIDERS:
        raise HTTPException(404, "不支持的数据源")
    try:
        return await start_qr(provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"二维码获取失败：{exc}") from exc


@app.get("/api/v1/settings/sessions/{provider}/qr/{session_id}")
async def poll_qr_login(provider: str, session_id: str) -> dict[str, str]:
    if provider not in SESSION_PROVIDERS:
        raise HTTPException(404, "不支持的数据源")
    try:
        result = await poll_qr(provider, session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"扫码状态获取失败：{exc}") from exc
    if result.status == "success":
        store = current_repo()
        await store.upsert_provider_session(provider, result.cookie, result.expires_at)
    return {"status": result.status}


@app.delete("/api/v1/settings/sessions/{provider}/qr/{session_id}")
async def cancel_qr_login(provider: str, session_id: str) -> dict[str, str]:
    if provider not in SESSION_PROVIDERS:
        raise HTTPException(404, "不支持的数据源")
    await cancel_qr(provider, session_id)
    return {"status": "cancelled"}


_frontend = frontend_dir()
if _frontend.exists():
    app.mount("/assets", StaticFiles(directory=_frontend / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        requested = (_frontend / path).resolve()
        if path and requested.is_file() and _frontend.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(_frontend / "index.html")
