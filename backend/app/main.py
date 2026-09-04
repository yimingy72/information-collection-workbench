from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID
from urllib.parse import unquote, urlsplit

import httpx

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.collector import RunSpec, collect_run
from app.models import (
    CollectionRequest,
    IcpDomainRun,
    IcpDomainRunListResponse,
    IcpRow,
    ManualProxyRequest,
    ManualProxyTestResponse,
    ManualProxyView,
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
    SubdomainResultItem,
    SubdomainResultsResponse,
    SubdomainRunListResponse,
    SubdomainRunRequest,
    SubdomainRunSummary,
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
    configure_gateway_for_active_route,
    manual_proxy_url,
    pool_nodes,
    remove_pool_node,
    run_cloud_operation,
    serverless_proxy_view,
    upsert_pool_node,
    test_serverless_proxy,
    validate_saved_config,
)
from app.settings import settings
from app.subdomains import normalize_domains, registrable_domain

logger = logging.getLogger(__name__)


class DeleteRunsRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1)



def session_status(row: dict) -> str:
    if not str(row.get("cookie") or "").strip():
        return "logged_out"
    return "logged_in" if cookie_active(row) else "expired"


def _manual_proxy_view(row: dict) -> ManualProxyView:
    return ManualProxyView(
        id=row["id"],
        scheme=str(row.get("scheme") or "http"),
        host=str(row.get("host") or ""),
        port=int(row.get("port") or 0),
        username=str(row.get("username") or ""),
        has_password=bool(str(row.get("password") or "")),
        enabled=bool(row.get("enabled")),
        status=str(row.get("status") or "configured"),
        latency_ms=int(row["latency_ms"]) if row.get("latency_ms") is not None else None,
        failure_count=int(row.get("failure_count") or 0),
        last_error=str(row.get("last_error") or ""),
        last_tested_at=row.get("last_tested_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _parse_manual_proxy(value: str) -> dict[str, object]:
    raw = str(value or "").strip()
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "代理地址必须是 HTTP(S)://[用户名:密码@]主机:端口")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise HTTPException(400, "代理地址不能包含路径、查询参数或片段")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(400, "代理端口无效") from exc
    if not port:
        raise HTTPException(400, "代理地址必须包含端口")
    return {
        "scheme": parsed.scheme.lower(),
        "host": parsed.hostname,
        "port": port,
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


def _manual_proxy_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:500] or type(exc).__name__


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
        manual_proxies=[_manual_proxy_view(row) for row in config.get("manual_proxies") or []],
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
        progress=row["progress"], total=row["total"],
        icp_cache_hits=(row["icp_cache_hits"] if "icp_cache_hits" in row.keys() else 0),
        icp_live_queries=(row["icp_live_queries"] if "icp_live_queries" in row.keys() else 0),
        error=row["error"],
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


def subdomain_run_summary(row: asyncpg.Record) -> SubdomainRunSummary:
    return SubdomainRunSummary(
        id=row["id"],
        domains=list(row["domains"] or []),
        source_run_ids=list(row["source_run_ids"] or []),
        options=dict(row["options"] or {}),
        status=row["status"],
        phase=row["phase"],
        attempts=row["attempts"],
        progress=row["progress"],
        total=row["total"],
        discovered=row["discovered"],
        warnings=list(row["warnings"] or []),
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def subdomain_result_item(row: asyncpg.Record) -> SubdomainResultItem:
    return SubdomainResultItem(
        id=row["id"], run_id=row["run_id"],
        stream_seq=int(row["stream_seq"]) if "stream_seq" in row else 0,
        root_domain=row["root_domain"], hostname=row["hostname"],
        ips=list(row["ips"] or []),
        canonical_name=row["canonical_name"], dns_status=row["dns_status"],
        wildcard=row["wildcard"], http_url=row["http_url"],
        http_status=row["http_status"], title=row["title"],
        sources=list(row["sources"] or []), discovered_at=row["discovered_at"],
    )


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
        await configure_gateway_for_active_route(await repo.get_runtime_config())
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
        await configure_gateway_for_active_route(config)
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
    # Capture cursors before loading the snapshot. Rows committed after this
    # point may appear in both the snapshot and the event stream, which the
    # frontend safely deduplicates. Capturing them afterwards could skip a row
    # committed between the snapshot queries and the cursor query.
    relationship_cursor, result_cursor = await current_repo().collection_event_cursors(run_id)
    # Historical/query detail pages must not silently truncate investments at
    # 1000 rows. The results endpoint remains paginated, while this view is the
    # complete payload used by the detail screen and export action.
    _, rels, _, _ = await current_repo().results(run_id, None, 0, 0, None, 0)
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
        relationship_cursor=relationship_cursor,
        result_cursor=result_cursor,
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


@app.post("/api/v1/collection-runs/{run_id}/cancel", response_model=RunSummary)
async def cancel_collection_run(run_id: UUID) -> RunSummary:
    store = current_repo()
    row = await store.cancel_run(run_id)
    if row is not None:
        return run_summary(row)
    existing = await store.get_run(run_id)
    if existing is None:
        raise HTTPException(404, "run not found")
    return run_summary(existing)


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


@app.get("/api/v1/collection-runs/{run_id}/events")
async def stream_collection_results(
    run_id: UUID,
    relationship_cursor: int = Query(0, ge=0),
    result_cursor: int = Query(0, ge=0),
) -> StreamingResponse:
    store = current_repo()
    if await store.get_run(run_id) is None:
        raise HTTPException(404, "run not found")

    async def events():
        rel_cursor = relationship_cursor
        icp_cursor = result_cursor
        last_progress = ""
        while True:
            relationships, icp_results = await store.collection_events_after(
                run_id, rel_cursor, icp_cursor, 1000
            )
            investments: list[dict[str, Any]] = []
            icp_records: list[dict[str, Any]] = []
            for item in relationships:
                rel_cursor = max(rel_cursor, int(item["stream_seq"] or 0))
                investments.append(
                    InvestmentRow(
                        parent_name=item["parent_name"],
                        child_name=item["child_name"],
                        holding_percent=item["holding_percent"],
                        depth=item["depth"],
                        source=_source_from(item["raw_payload"]),
                    ).model_dump(mode="json")
                )
            for item in icp_results:
                icp_cursor = max(icp_cursor, int(item["stream_seq"] or 0))
                payload = item["payload"] or {}
                icp_records.append(
                    IcpRow(
                        unit_name=payload.get("unit_name") or item["entity_name"],
                        main_licence=payload.get("main_licence") or "",
                        service_licence=payload.get("service_licence") or "",
                        domain=payload.get("domain") or "",
                        nature_name=payload.get("nature_name") or "",
                        update_time=payload.get("update_time") or "",
                        source=payload.get("source") or "ICP备案",
                    ).model_dump(mode="json")
                )
            if investments or icp_records:
                payload = json.dumps(
                    {
                        "relationship_cursor": rel_cursor,
                        "result_cursor": icp_cursor,
                        "investments": investments,
                        "icp_records": icp_records,
                    },
                    ensure_ascii=False,
                )
                yield f"event: delta\ndata: {payload}\n\n"

            run = await store.get_run(run_id)
            if run is None:
                yield 'event: error\ndata: {"detail":"记录已删除"}\n\n'
                return
            summary = run_summary(run)
            progress = summary.model_dump_json()
            if progress != last_progress:
                yield f"event: progress\ndata: {progress}\n\n"
                last_progress = progress
            if summary.status in {"succeeded", "partial", "failed", "cancelled"}:
                yield f"event: done\ndata: {progress}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/subdomain-runs", response_model=SubdomainRunSummary, status_code=201)
async def create_subdomain_run(request: SubdomainRunRequest) -> SubdomainRunSummary:
    store = current_repo()
    values = list(request.domains)
    if not values and request.source_run_ids:
        # API callers may submit only an ICP run id. Ignore malformed legacy
        # domain values instead of making every valid domain in that record fail.
        for value in await store.icp_domains_for_runs(request.source_run_ids):
            try:
                domain = registrable_domain(value)
            except ValueError:
                continue
            if domain not in values:
                values.append(domain)
    try:
        domains = normalize_domains(values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if len(domains) > 200:
        raise HTTPException(400, "单次最多查询 200 个主域名")
    row = await store.create_subdomain_run(
        domains, request.source_run_ids, request.options.model_dump()
    )
    return subdomain_run_summary(row)


@app.get("/api/v1/subdomain-runs", response_model=SubdomainRunListResponse)
async def list_subdomain_runs(
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
) -> SubdomainRunListResponse:
    rows, total = await current_repo().list_subdomain_runs(limit, offset)
    return SubdomainRunListResponse(
        items=[subdomain_run_summary(row) for row in rows], total=total
    )


@app.get("/api/v1/subdomain-runs/{run_id}", response_model=SubdomainRunSummary)
async def get_subdomain_run(run_id: UUID) -> SubdomainRunSummary:
    row = await current_repo().get_subdomain_run(run_id)
    if row is None:
        raise HTTPException(404, "子域名查询记录不存在")
    return subdomain_run_summary(row)


@app.post("/api/v1/subdomain-runs/{run_id}/cancel", response_model=SubdomainRunSummary)
async def cancel_subdomain_run(run_id: UUID) -> SubdomainRunSummary:
    store = current_repo()
    row = await store.cancel_subdomain_run(run_id)
    if row is not None:
        return subdomain_run_summary(row)
    existing = await store.get_subdomain_run(run_id)
    if existing is None:
        raise HTTPException(404, "子域名查询记录不存在")
    return subdomain_run_summary(existing)


@app.get(
    "/api/v1/subdomain-runs/{run_id}/results", response_model=SubdomainResultsResponse
)
async def get_subdomain_results(
    run_id: UUID,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    after_id: int | None = Query(None, ge=0),
) -> SubdomainResultsResponse:
    if await current_repo().get_subdomain_run(run_id) is None:
        raise HTTPException(404, "子域名查询记录不存在")
    rows, total = await current_repo().subdomain_results(run_id, limit, offset, after_id)
    return SubdomainResultsResponse(
        run_id=run_id, items=[subdomain_result_item(row) for row in rows], total=total
    )


@app.get("/api/v1/subdomain-runs/{run_id}/events")
async def stream_subdomain_results(
    run_id: UUID, after_seq: int = Query(0, ge=0)
) -> StreamingResponse:
    if await current_repo().get_subdomain_run(run_id) is None:
        raise HTTPException(404, "子域名查询记录不存在")

    async def events():
        # stream_seq advances for both inserts and enrichment updates. Using the
        # row id here would miss the second write when HTTP probing fills in a
        # DNS-only result that was already sent to the browser.
        cursor = after_seq
        store = current_repo()
        last_progress = ""
        while True:
            rows = await store.subdomain_events_after(run_id, cursor, 500)
            for row in rows:
                item = subdomain_result_item(row)
                cursor = max(cursor, item.stream_seq)
                yield f"event: result\ndata: {item.model_dump_json()}\n\n"
            run = await store.get_subdomain_run(run_id)
            if run is None:
                yield "event: error\ndata: {\"detail\":\"记录已删除\"}\n\n"
                return
            summary = subdomain_run_summary(run)
            progress = summary.model_dump_json()
            if progress != last_progress:
                yield f"event: progress\ndata: {progress}\n\n"
                last_progress = progress
            if summary.status in {"succeeded", "partial", "failed", "cancelled"}:
                yield f"event: done\ndata: {progress}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/v1/subdomain-runs/{run_id}")
async def delete_subdomain_run(run_id: UUID) -> dict[str, int]:
    deleted = await current_repo().delete_subdomain_run(run_id)
    if not deleted:
        raise HTTPException(404, "子域名查询记录不存在")
    return {"deleted": deleted}


@app.get("/api/v1/icp-domain-runs", response_model=IcpDomainRunListResponse)
async def list_icp_domain_runs(limit: int = Query(50, ge=1, le=100)) -> IcpDomainRunListResponse:
    items: list[IcpDomainRun] = []
    for row in await current_repo().icp_domain_runs(limit):
        domains: list[str] = []
        for value in row["domains"] or []:
            try:
                domain = registrable_domain(str(value))
            except ValueError:
                continue
            if domain not in domains:
                domains.append(domain)
        if domains:
            items.append(IcpDomainRun(
                id=row["id"], keyword=row["keyword"], created_at=row["created_at"], domains=domains
            ))
    return IcpDomainRunListResponse(items=items)


@app.get("/api/v1/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    return settings_view(await current_repo().get_runtime_config())


@app.post("/api/v1/settings/manual-proxies", response_model=ManualProxyView)
async def create_manual_proxy(request: ManualProxyRequest) -> ManualProxyView:
    values = _parse_manual_proxy(request.proxy_url)
    try:
        row = await current_repo().create_manual_proxy(**values, enabled=request.enabled)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409, "相同代理已经存在") from exc
    return _manual_proxy_view(dict(row))


@app.put("/api/v1/settings/manual-proxies/{proxy_id}", response_model=ManualProxyView)
async def update_manual_proxy(proxy_id: UUID, request: ManualProxyRequest) -> ManualProxyView:
    values = _parse_manual_proxy(request.proxy_url)
    row = await current_repo().get_manual_proxy(proxy_id)
    if row is None:
        raise HTTPException(404, "代理不存在")
    password = str(values["password"])
    try:
        updated = await current_repo().update_manual_proxy(
            proxy_id,
            scheme=str(values["scheme"]),
            host=str(values["host"]),
            port=int(values["port"]),
            username=str(values["username"]),
            password=password,
            enabled=request.enabled,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409, "相同代理已经存在") from exc
    if updated is None:
        raise HTTPException(404, "代理不存在")
    await configure_gateway_for_active_route(await current_repo().get_runtime_config())
    return _manual_proxy_view(dict(updated))


@app.post("/api/v1/settings/manual-proxies/{proxy_id}/toggle", response_model=ManualProxyView)
async def toggle_manual_proxy(proxy_id: UUID) -> ManualProxyView:
    store = current_repo()
    row = await store.get_manual_proxy(proxy_id)
    if row is None:
        raise HTTPException(404, "代理不存在")
    updated = await store.update_manual_proxy(
        proxy_id,
        scheme=str(row["scheme"]),
        host=str(row["host"]),
        port=int(row["port"]),
        username=str(row["username"] or ""),
        password=str(row["password"] or ""),
        enabled=not bool(row["enabled"]),
    )
    if updated is None:
        raise HTTPException(404, "代理不存在")
    await configure_gateway_for_active_route(await store.get_runtime_config())
    return _manual_proxy_view(dict(updated))


@app.delete("/api/v1/settings/manual-proxies/{proxy_id}")
async def delete_manual_proxy(proxy_id: UUID) -> dict[str, int]:
    deleted = await current_repo().delete_manual_proxy(proxy_id)
    if not deleted:
        raise HTTPException(404, "代理不存在")
    return {"deleted": deleted}


@app.post("/api/v1/settings/manual-proxies/{proxy_id}/test", response_model=ManualProxyTestResponse)
async def test_manual_proxy(proxy_id: UUID) -> ManualProxyTestResponse:
    store = current_repo()
    row = await store.get_manual_proxy(proxy_id)
    if row is None:
        raise HTTPException(404, "代理不存在")
    values = dict(row)
    proxy = manual_proxy_url(values)
    if not proxy:
        raise HTTPException(400, "代理配置不完整")
    await store.set_manual_proxy_result(proxy_id, status="testing", latency_ms=None, error="")
    started = asyncio.get_running_loop().time()
    try:
        timeout = httpx.Timeout(10.0, connect=5.0, read=10.0, write=10.0, pool=5.0)
        async with asyncio.timeout(10):
            async with httpx.AsyncClient(proxy=proxy, timeout=timeout, trust_env=False, follow_redirects=True) as client:
                response = await client.get("https://www.baidu.com/robots.txt")
                response.raise_for_status()
        elapsed = max(0, round((asyncio.get_running_loop().time() - started) * 1000))
        await store.set_manual_proxy_result(proxy_id, status="ready", latency_ms=elapsed, error="", enabled=True)
        return ManualProxyTestResponse(proxy_id=proxy_id, latency_ms=elapsed, target="https://www.baidu.com/robots.txt")
    except Exception as exc:
        detail = _manual_proxy_error(exc)
        await store.set_manual_proxy_result(proxy_id, status="error", latency_ms=None, error=detail, enabled=False)
        raise HTTPException(502, f"代理测试失败：{detail}") from exc


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
        await configure_gateway_for_active_route(config)
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
    previous_enabled = bool(row.get("enabled"))
    node_id = str(request.node_id or f"{request.provider}:{request.region}:{request.function_name}")
    await store.set_serverless_proxy_status("deploying", enabled=False)
    try:
        result = await run_cloud_operation("deploy", row)
        endpoint = str(result.get("endpoint") or "").strip()
        if not endpoint:
            raise ServerlessProxyError("云平台未返回函数地址")
        node = {
            "id": node_id,
            "enabled": True,
            "provider": row["provider"],
            "endpoint": endpoint,
            "region": row["region"],
            "function_name": row["function_name"],
            "image_uri": row.get("image_uri") or "",
            "access_key_id": row.get("access_key_id") or "",
            "access_key_secret": row.get("access_key_secret") or "",
            "insecure_skip_verify": bool(row.get("insecure_skip_verify")),
            "deployment_id": str(result.get("deployment_id") or ""),
            "status": "deployed",
            "last_error": "",
            "latency_ms": None,
            "failure_count": 0,
        }
        nodes = upsert_pool_node(row, node)
        deployment_payload = {
            **row,
            **node,
            "enabled": True,
            "endpoint": endpoint,
            "deployment_id": node["deployment_id"],
            "status": "deployed",
            "nodes": nodes,
        }
        await store.update_serverless_proxy(deployment_payload)
        config = await store.get_runtime_config()
        await configure_gateway(config, force_enabled=False)

        # Verify the newly deployed node in isolation, then put the complete
        # pool back into the gateway. This prevents a broken old node from
        # masking a healthy newly deployed node during deployment verification.
        test_result = await test_serverless_proxy({**deployment_payload, "nodes": [node], "enabled": True})
        ready_nodes = [
            {**item, "status": "ready", "enabled": True, "last_error": "", "failure_count": 0}
            if item.get("id") == node_id else item
            for item in nodes
        ]
        await store.update_serverless_proxy({**deployment_payload, "status": "ready", "nodes": ready_nodes})
        await store.set_serverless_proxy_status("ready", enabled=True)
        config = await store.get_runtime_config()
        await configure_gateway(config, force_enabled=True)
    except ServerlessProxyError as exc:
        # Keep already healthy nodes serving traffic. The failed node remains
        # visible as disabled/error so auto-scaling can retry it or choose the
        # next region, instead of leaving the whole pool in an unusable state.
        current_config = await store.get_runtime_config()
        current_row = current_config["serverless_proxy"]
        marked_nodes = [
            {**item, "enabled": False, "status": "error", "last_error": str(exc)}
            if item.get("id") == node_id else item
            for item in pool_nodes(current_row)
        ]
        healthy = next(
            (item for item in marked_nodes
             if item.get("enabled") and item.get("status") == "ready" and item.get("endpoint")),
            None,
        )
        await store.update_serverless_proxy({
            **current_row,
            "enabled": bool(healthy),
            "endpoint": healthy.get("endpoint") if healthy else str(current_row.get("endpoint") or ""),
            "nodes": marked_nodes,
        })
        await store.set_serverless_proxy_status(
            "ready" if healthy else "error", "" if healthy else str(exc), enabled=bool(healthy)
        )
        await configure_gateway(await store.get_runtime_config(), force_enabled=bool(healthy))
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
    if not str(row.get("endpoint") or "").strip() and not any(node.get("endpoint") for node in pool_nodes(row)):
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


async def _delete_serverless_node(store: Repository, node_id: str) -> SettingsResponse:
    config = await store.get_runtime_config()
    row = config["serverless_proxy"]
    target = next((item for item in pool_nodes(row) if item.get("id") == node_id), None)
    if target is None:
        raise HTTPException(404, "云函数节点不存在")
    if target.get("provider") == "custom" or not target.get("deployment_id"):
        raise HTTPException(400, "当前节点不是由平台部署的云函数")
    try:
        await run_cloud_operation("destroy", target)
    except ServerlessProxyError as exc:
        await store.set_serverless_proxy_status("error", str(exc))
        raise HTTPException(502, str(exc)) from exc
    _, remaining = remove_pool_node(row, node_id)
    primary = next((item for item in remaining if item.get("endpoint")), None)
    payload = {**row, "nodes": remaining}
    if primary:
        payload.update(primary)
        payload["enabled"] = bool(row.get("enabled"))
        payload["status"] = "ready" if primary.get("status") == "ready" else "configured"
    else:
        payload.update({"enabled": False, "endpoint": "", "deployment_id": "", "status": "not_configured", "last_error": ""})
    await store.update_serverless_proxy(payload)
    config = await store.get_runtime_config()
    await configure_gateway(config)
    return settings_view(config)


@app.delete("/api/v1/settings/serverless-proxy/nodes/{node_id}", response_model=SettingsResponse)
async def delete_serverless_proxy_node(node_id: str) -> SettingsResponse:
    return await _delete_serverless_node(current_repo(), node_id)


@app.delete("/api/v1/settings/serverless-proxy/deployment", response_model=SettingsResponse)
async def delete_serverless_proxy_deployment() -> SettingsResponse:
    store = current_repo()
    row = (await store.get_runtime_config())["serverless_proxy"]
    target = next((item for item in pool_nodes(row) if item.get("id") == f"{row.get('provider')}:{row.get('region')}:{row.get('function_name')}"), None)
    target = target or next((item for item in pool_nodes(row) if item.get("deployment_id")), None)
    if target is None:
        raise HTTPException(400, "当前配置不是由平台部署的云函数")
    return await _delete_serverless_node(store, str(target["id"]))


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
