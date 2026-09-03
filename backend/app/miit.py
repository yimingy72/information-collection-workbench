from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary
from uuid import UUID, uuid4

import httpx

from app.repository import Repository
from app.serverless_proxy import manual_proxy_urls, miit_proxy_urls, pool_nodes
from app.settings import settings

MAX_PAGES = 50
# YMICP's own implementation documents 26 as the maximum reliable page size.
# Larger values are silently normalized upstream and can destabilize pagination.
PAGE_SIZE = 26
ICP_PAGINATION_RECOVERY_PASSES = 2
# A positive upstream total with an empty page is a transient/route anomaly,
# not a normal multi-page pagination case. Give it one alternate page session
# before returning to the company-level retry loop; repeating three complete
# passes per attempt made a single bad response cost 9 requests.
ICP_EMPTY_RESULT_RECOVERY_PASSES = 1


# Keep one slot below the cloud-function instance concurrency of six.
ICP_BATCH_SIZE = 5
# Maximum logical ICP concurrency after node-pool scaling. The compatibility
# knob can still be lowered by an existing deployment or test.
ICP_CONCURRENCY = 40
ICP_DIRECT_REQUEST_GAP_SECONDS = 0.4
ICP_BATCH_PAUSE_SECONDS = 12.0
ICP_PAGE_TIMEOUT_SECONDS = 10
ICP_COMPANY_TIMEOUT_SECONDS = 30
ICP_COMPANY_MAX_ATTEMPTS = max(1, settings.icp_company_max_attempts)
ICP_COMPANY_RETRY_BACKOFF_SECONDS = max(0.0, settings.icp_company_retry_backoff_seconds)
# In cloud mode this counts actual ICP page requests, not companies. Reaching
# the limit starts a new YMICP page session, which makes YMICP open a fresh
# HTTP-proxy TCP connection and therefore a fresh SeaMoon WebSocket tunnel.
ICP_PROXY_REQUEST_LIMIT = 5
# A WAF response should not discard the current page immediately. Retry it a
# bounded number of times after the route generation has been rotated.
ICP_PROXY_WAF_RETRIES = 2
# A single local gateway URL can front several cloud nodes. Rotate the tunnel
# for non-WAF transport errors too; the gateway may have just selected a bad
# endpoint and returning straight to the company retry wastes the page context.
# One immediate alternate-tunnel retry is enough for transport/timeout errors;
# further recovery is handled by the company-level retry with a fresh session.
# Keeping two page retries here multiplied a single bad company into 9 slow
# requests before the final failure was reported.
ICP_PROXY_ERROR_RETRIES = 1
ICP_MAX_CLOUD_BATCH_SIZE = 40
# Bump this whenever pagination, completeness, or record identity semantics
# change. Old cache rows remain available for audit but can no longer suppress
# a live query after the algorithm changes.
ICP_CACHE_VERSION = "miit-web-page26-record-key-v1"

# Multiple API requests and queue workers share the same direct/cloud route.
# Serialize complete ICP collections per event loop so separate runs cannot
# independently reset the shared request budget and fire at once.
_COLLECTION_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


def _collection_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _COLLECTION_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _COLLECTION_LOCKS[loop] = lock
    return lock


class IcpPageError(RuntimeError):
    """An ICP page request failed."""


class _IcpCloudRotationScheduler:
    """Count cloud page requests and gate the next logical tunnel generation.

    The backend can force YMICP to create a new HTTP-proxy connection by
    changing the page-session key. This is a real tunnel/session rotation, but
    a single cloud-function endpoint still cannot guarantee a different public
    egress IP because the provider may reuse the same warm instance or NAT.
    """

    def __init__(self, request_limit: int) -> None:
        self.request_limit = max(1, request_limit)
        self.used = 0
        self.active = 0
        self.generation = 0
        self.rotation_pending = False
        self.condition = asyncio.Condition()

    async def acquire(self) -> int:
        async with self.condition:
            while self.rotation_pending or (self.used >= self.request_limit and self.active):
                await self.condition.wait()
            if self.used >= self.request_limit:
                self.used = 0
                self.generation += 1
            generation = self.generation
            self.used += 1
            self.active += 1
            return generation

    async def release(self) -> None:
        async with self.condition:
            self.active = max(0, self.active - 1)
            self.condition.notify_all()

    async def rotate(self, observed_generation: int) -> int:
        async with self.condition:
            if self.generation != observed_generation:
                return self.generation
            self.rotation_pending = True
            while self.active:
                await self.condition.wait()
            if self.generation == observed_generation:
                self.used = 0
                self.generation += 1
            self.rotation_pending = False
            self.condition.notify_all()
            return self.generation


class _IcpProxyPoolScheduler:
    """Round-robin HTTP proxy pool with an independent five-request budget per node."""

    def __init__(self, routes: list[str], request_limit: int) -> None:
        self.routes = list(dict.fromkeys(route for route in routes if route))
        self.request_limit = max(1, request_limit)
        self.used = [0 for _ in self.routes]
        self.generations = [0 for _ in self.routes]
        self.cursor = 0
        self.active = 0
        self.condition = asyncio.Condition()

    async def acquire(self, preferred_index: int | None = None) -> tuple[str, int, int]:
        if not self.routes:
            raise IcpPageError("暂无可用 HTTP 代理")
        async with self.condition:
            while True:
                if not all(used >= self.request_limit for used in self.used):
                    break
                if self.active == 0:
                    self.used = [0 for _ in self.routes]
                    break
                await self.condition.wait()
            candidates: list[int] = []
            if preferred_index is not None and 0 <= preferred_index < len(self.routes):
                candidates.append(preferred_index)
            candidates.extend(
                (self.cursor + offset) % len(self.routes)
                for offset in range(len(self.routes))
                if (self.cursor + offset) % len(self.routes) not in candidates
            )
            for index in candidates:
                if self.used[index] < self.request_limit:
                    self.cursor = (index + 1) % len(self.routes)
                    self.used[index] += 1
                    self.active += 1
                    return self.routes[index], index, self.generations[index]
            raise IcpPageError("暂无可用 HTTP 代理")

    async def release(self) -> None:
        async with self.condition:
            self.active = max(0, self.active - 1)
            self.condition.notify_all()

    async def rotate(self, index: int, generation: int) -> None:
        async with self.condition:
            if 0 <= index < len(self.routes) and self.generations[index] == generation:
                self.used[index] = self.request_limit
                self.generations[index] += 1
            self.condition.notify_all()


class _IcpRequestScheduler:
    """Throttle only direct requests that share one fixed local exit IP."""

    def __init__(self, batch_size: int, *, direct: bool) -> None:
        self.batch_size = max(1, batch_size)
        self.direct = direct
        self.used = 0
        self.lock = asyncio.Lock()

    async def before_request(self) -> None:
        # SeaMoon cloud companies use independent pagination sessions/tunnels
        # and the outer loop already limits cloud concurrency to five. Applying
        # the direct-IP cooldown globally here made an enterprise spend most of
        # its 30-second budget waiting behind unrelated cloud requests.
        if not self.direct:
            return
        async with self.lock:
            if self.used >= self.batch_size:
                await asyncio.sleep(ICP_BATCH_PAUSE_SECONDS)
                self.used = 0
            if self.used:
                await asyncio.sleep(ICP_DIRECT_REQUEST_GAP_SECONDS)
            self.used += 1


@dataclass(frozen=True)
class CompanyFailure:
    name: str
    attempted_pages: int
    error: str
    elapsed_seconds: float
    company_attempts: int = 1


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _icp_reason(data: Any, fallback: str = "ICP 接口异常") -> str:
    if isinstance(data, dict):
        return _clean(data.get("message") or data.get("msg") or data.get("detail")) or fallback
    return fallback


def _http_error(response: httpx.Response) -> IcpPageError:
    status = response.status_code
    if status == 521:
        return IcpPageError("HTTP 521：上游 Web 服务不可用")
    return IcpPageError(f"HTTP {status}")


async def _fetch_page(
    client: httpx.AsyncClient,
    keyword: str,
    page: int,
    timeout_seconds: float = ICP_PAGE_TIMEOUT_SECONDS,
    route_proxy: str = "",
    session_key: str = "",
) -> dict[str, Any]:
    """Fetch one ICP page, optionally asking the local ICP service to use SeaMoon."""
    timeout_seconds = max(0.01, min(float(timeout_seconds), ICP_PAGE_TIMEOUT_SECONDS))
    params = {"search": keyword, "pageNum": page, "pageSize": PAGE_SIZE}
    if route_proxy:
        params["proxy"] = route_proxy
    if session_key:
        params["sessionKey"] = session_key
    timeout = httpx.Timeout(
        timeout_seconds,
        connect=timeout_seconds,
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )

    try:
        async with asyncio.timeout(timeout_seconds):
            headers = (
                {"X-Workbench-Proxy-Token": settings.icp_proxy_control_token}
                if route_proxy
                else None
            )
            response = await client.get(
                "/query/web", params=params, timeout=timeout, headers=headers
            )
            if response.status_code >= 400:
                raise _http_error(response)
            text = response.text.strip()
            if not text:
                raise IcpPageError("ICP 接口返回空响应")
            try:
                data = response.json()
            except ValueError as exc:
                raise IcpPageError("ICP 接口返回非 JSON") from exc
            if not isinstance(data, dict) or data.get("code") not in (200, 0):
                raise IcpPageError(_icp_reason(data, "ICP JSON 业务状态未成功"))
            payload = data.get("params")
            if not isinstance(payload, dict):
                raise IcpPageError("ICP JSON 业务响应无效")
            rows = payload.get("list")
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise IcpPageError("ICP JSON 列表字段无效")
            try:
                pages = max(1, int(payload.get("pages") or 1))
            except (TypeError, ValueError) as exc:
                raise IcpPageError("ICP JSON 分页字段无效") from exc
            total_value = payload.get("total")
            try:
                total = max(0, int(total_value)) if total_value is not None else None
            except (TypeError, ValueError) as exc:
                raise IcpPageError("ICP JSON 总数字段无效") from exc
            return {"rows": rows, "pages": pages, "total": total}
    except IcpPageError:
        raise
    except httpx.TimeoutException as exc:
        raise IcpPageError(f"请求超时（不超过 {timeout_seconds:g} 秒）") from exc
    except TimeoutError as exc:
        raise IcpPageError(f"请求硬超时（不超过 {timeout_seconds:g} 秒）") from exc
    except httpx.HTTPError as exc:
        raise IcpPageError(f"HTTP 请求异常：{_clean(exc)}") from exc
    except Exception as exc:  # noqa: BLE001 - normalize upstream failures
        raise IcpPageError(f"ICP 查询接口异常：{_clean(exc) or type(exc).__name__}") from exc


async def _save_icp_page(
    repo: Repository,
    run_id: UUID,
    requested_name: str,
    rows: list[Any],
    seen: set[tuple[str, ...]],
) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        domain = _clean(row.get("domain"))
        service_licence = _clean(row.get("serviceLicence"))
        key = _icp_row_key(row, requested_name)
        if key is None or key in seen:
            continue
        seen.add(key)
        unit_name = _clean(row.get("unitName")) or requested_name
        main_licence = _clean(row.get("mainLicence"))
        external_id = str(row.get("mainId") or f"{unit_name}:{main_licence}")
        entity_id = await repo.upsert_entity(
            "miit", external_id, unit_name, {"main_licence": main_licence}
        )
        payload = {
            "unit_name": unit_name,
            "main_licence": main_licence,
            "service_licence": service_licence,
            "domain": domain,
            "nature_name": _clean(row.get("natureName")),
            "update_time": _clean(row.get("updateRecordTime")),
            "source": "ICP备案",
        }
        await repo.add_result(
            run_id, entity_id, "icp", payload, f"{settings.miit_api_url}/query/web", row
        )


def _icp_row_key(row: dict[str, Any], requested_name: str = "") -> tuple[str, ...] | None:
    """Build a stable ICP identity even when the upstream domain is blank.

    MIIT legitimately returns备案 records whose ``domain`` is empty (for
    example, a record identified only by ``mainLicence``). Treating those rows
    as absent made a successful ``total=1, list=[...]`` response look like an
    incomplete ``0/1`` response and needlessly consumed all retry attempts.
    """
    domain = _clean(row.get("domain"))
    service_licence = _clean(row.get("serviceLicence"))
    if domain or service_licence:
        return ("domain", domain, service_licence)
    main_licence = _clean(row.get("mainLicence"))
    if main_licence:
        return ("mainLicence", main_licence)
    main_id = _clean(row.get("mainId"))
    if main_id:
        return ("mainId", main_id)
    unit_name = _clean(row.get("unitName")) or _clean(requested_name)
    return ("unitName", unit_name) if unit_name else None


def _validated_cached_rows(
    cache: Any, requested_name: str
) -> list[dict[str, Any]] | None:
    """Return a cache snapshot only when its completeness proof still holds."""
    try:
        value = dict(cache)
        if not value.get("complete") or value.get("query_version") != ICP_CACHE_VERSION:
            return None
        reported_total = int(value["reported_total"])
        saved_total = int(value["saved_total"])
        raw_rows = value.get("rows")
    except (KeyError, TypeError, ValueError):
        return None
    if (
        reported_total < 0
        or saved_total != reported_total
        or not isinstance(raw_rows, list)
        or len(raw_rows) != saved_total
    ):
        return None

    unique_rows: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        key = _icp_row_key(row, requested_name)
        if key is not None:
            unique_rows.setdefault(key, row)
    if len(unique_rows) != reported_total:
        return None
    return list(unique_rows.values())


async def _restore_icp_cache(
    repo: Repository, run_id: UUID, names: list[str]
) -> tuple[list[str], list[str]]:
    """Copy fresh complete snapshots into this run and return hits/misses.

    Cache access is deliberately best-effort: an unavailable or malformed
    cache falls back to the authoritative live query instead of losing data.
    """
    if not settings.icp_cache_enabled:
        return [], names
    get_caches = getattr(repo, "get_icp_company_caches", None)
    if get_caches is None:
        return [], names
    try:
        caches = await get_caches(names, ICP_CACHE_VERSION)
    except Exception:  # noqa: BLE001 - cache failure must not block live ICP
        return [], names

    hits: list[str] = []
    misses: list[str] = []
    for name in names:
        cached_rows = _validated_cached_rows(caches.get(name), name) if caches.get(name) else None
        if cached_rows is None:
            misses.append(name)
            continue
        try:
            await _save_icp_page(repo, run_id, name, cached_rows, set())
        except Exception:  # noqa: BLE001 - retry this company through live ICP
            misses.append(name)
            continue
        hits.append(name)
    return hits, misses


async def _store_icp_cache(
    repo: Repository,
    company_name: str,
    rows: list[dict[str, Any]],
    reported_total: int | None,
) -> None:
    """Persist only an authoritative, exactly complete company snapshot."""
    if not settings.icp_cache_enabled or reported_total is None:
        return
    if reported_total < 0 or len(rows) != reported_total:
        return
    upsert_cache = getattr(repo, "upsert_icp_company_cache", None)
    if upsert_cache is None:
        return
    ttl_hours = (
        settings.icp_zero_cache_ttl_hours
        if reported_total == 0
        else settings.icp_cache_ttl_hours
    )
    try:
        await upsert_cache(
            company_name,
            rows,
            reported_total,
            ICP_CACHE_VERSION,
            max(1, int(ttl_hours * 3600)),
        )
    except Exception:  # noqa: BLE001 - a completed query must survive cache failure
        return


async def _record_icp_cache_stats(
    repo: Repository, run_id: UUID, cache_hits: int, live_queries: int
) -> None:
    add_stats = getattr(repo, "add_icp_cache_stats", None)
    if add_stats is None:
        return
    try:
        await add_stats(run_id, cache_hits, live_queries)
    except Exception:  # noqa: BLE001 - metrics must never affect collection
        return


def _failure(
    name: str,
    started: float,
    attempted_pages: int,
    error: str,
) -> CompanyFailure:
    elapsed = max(0.0, asyncio.get_running_loop().time() - started)
    return CompanyFailure(name, attempted_pages, error, elapsed)


async def _collect_icp(repo: Repository, run_id: UUID, names: list[str]) -> list[str]:
    names = list(dict.fromkeys(_clean(name) for name in names if _clean(name)))
    if not names:
        return []
    cache_hits, names = await _restore_icp_cache(repo, run_id, names)
    await _record_icp_cache_stats(repo, run_id, len(cache_hits), len(names))
    if not names or not settings.miit_api_url:
        return []
    get_runtime_config = getattr(repo, "get_runtime_config", None)
    runtime_config = await get_runtime_config() if get_runtime_config else {}
    manual_routes = manual_proxy_urls(runtime_config)
    route_proxies = miit_proxy_urls(runtime_config)
    using_manual_proxy = bool(manual_routes)
    using_cloud_proxy = not using_manual_proxy and bool(
        runtime_config.get("serverless_proxy", {}).get("enabled")
    ) and bool(route_proxies)
    route_proxy = route_proxies[0] if len(route_proxies) == 1 else ""
    route_label = (
        "手动 HTTP 代理"
        if using_manual_proxy
        else "云函数代理"
        if using_cloud_proxy
        else "直连"
    )
    batch_size = max(1, min(ICP_BATCH_SIZE, ICP_CONCURRENCY))
    ready_cloud_nodes = [
        item for item in pool_nodes(runtime_config.get("serverless_proxy") or {})
        if item.get("enabled") and item.get("status") in {"ready", "deployed"}
        and item.get("endpoint")
    ]
    if using_cloud_proxy:
        # One FC instance safely carries five logical ICP requests. The local
        # gateway round-robins connections across ready nodes, so scale the
        # logical batch and rotation budget with the pool instead of keeping a
        # hidden five-request ceiling after adding regions.
        node_count = max(1, len(ready_cloud_nodes))
        batch_size = min(
            ICP_MAX_CLOUD_BATCH_SIZE,
            max(1, ICP_CONCURRENCY),
            5 * node_count,
        )
    if using_manual_proxy:
        # A manual proxy is one fixed public exit. Run at most one active
        # company per ready node; sending five companies through one node only
        # makes their captcha/auth work contend and hit the page hard timeout.
        batch_size = min(batch_size, len(manual_routes))
    # A single manual proxy is a fixed route: do not apply the cloud tunnel
    # generation counter to it. Otherwise its pagination session would be
    # replaced every five pages even though no new exit IP can be created.
    cloud_scheduler = (
        _IcpCloudRotationScheduler(
            max(ICP_PROXY_REQUEST_LIMIT, batch_size)
        )
        if using_cloud_proxy and route_proxy
        else None
    )
    proxy_pool_scheduler = _IcpProxyPoolScheduler(route_proxies, ICP_PROXY_REQUEST_LIMIT) if len(route_proxies) > 1 else None
    request_scheduler = _IcpRequestScheduler(batch_size, direct=not route_proxies)

    async def collect_company_once(name: str, client: httpx.AsyncClient) -> CompanyFailure | None:
        started = asyncio.get_running_loop().time()
        attempted_pages = 0
        expected_total: int | None = None
        aggregate_rows: dict[tuple[str, ...], dict[str, Any]] = {}
        aggregate_chunks: list[list[Any]] = []
        last_incomplete_reason = ""

        async def save_complete_result() -> None:
            complete_rows = list(aggregate_rows.values())
            saved_seen: set[tuple[str, ...]] = set()
            for page_rows in aggregate_chunks:
                await _save_icp_page(repo, run_id, name, page_rows, saved_seen)
            await _store_icp_cache(repo, name, complete_rows, expected_total)

        try:
            async with asyncio.timeout(ICP_COMPANY_TIMEOUT_SECONDS):
                for pagination_pass in range(ICP_PAGINATION_RECOVERY_PASSES + 1):
                    # Every pass gets a fresh upstream pagination context. All
                    # pages inside the pass keep one YMICP/SeaMoon session. The
                    # MIIT endpoint occasionally overlaps valid pages even in a
                    # stable session, so bounded passes are unioned by record key
                    # until the authoritative total is reached.
                    session_key = uuid4().hex
                    page = 1
                    total_pages = 1
                    pass_pages: int | None = None
                    pass_total: int | None = None
                    inconsistent = False

                    page_proxy_retries = 0
                    company_route_index: int | None = None
                    while page <= min(total_pages, MAX_PAGES):
                        elapsed = asyncio.get_running_loop().time() - started
                        remaining = ICP_COMPANY_TIMEOUT_SECONDS - elapsed
                        if remaining <= 0:
                            return _failure(
                                name,
                                started,
                                attempted_pages,
                                f"单企业{route_label}查询总预算达到 {ICP_COMPANY_TIMEOUT_SECONDS:g} 秒",
                            )

                        try:
                            fetch_options: dict[str, Any] = {
                                "timeout_seconds": min(ICP_PAGE_TIMEOUT_SECONDS, remaining),
                                "session_key": session_key,
                            }
                            request_generation: int | None = None
                            request_route_proxy = route_proxy
                            request_route_index: int | None = None
                            proxy_request_claimed = False
                            try:
                                if cloud_scheduler is not None:
                                    request_generation = await cloud_scheduler.acquire()
                                    proxy_request_claimed = True
                                    request_route_proxy = route_proxy
                                elif proxy_pool_scheduler is not None:
                                    request_route_proxy, request_route_index, request_generation = await proxy_pool_scheduler.acquire(company_route_index)
                                    company_route_index = request_route_index
                                    proxy_request_claimed = True
                                else:
                                    await request_scheduler.before_request()
                                if request_route_proxy:
                                    fetch_options["route_proxy"] = request_route_proxy
                                    if request_generation is not None:
                                        # A changed session key makes YMICP
                                        # discard the prior aiohttp page
                                        # session and open a new HTTP-proxy
                                        # tunnel for this request. A single
                                        # manual route has no generation and
                                        # must keep the original page session.
                                        suffix = (
                                            request_generation
                                            if request_route_index is None
                                            else f"{request_route_index}_{request_generation}"
                                        )
                                        fetch_options["session_key"] = f"{session_key}_{suffix}"
                                attempted_pages += 1
                                chunk = await _fetch_page(client, name, page, **fetch_options)
                            finally:
                                if proxy_request_claimed:
                                    if cloud_scheduler is not None:
                                        await cloud_scheduler.release()
                                    elif proxy_pool_scheduler is not None:
                                        await proxy_pool_scheduler.release()
                        except IcpPageError as exc:
                            if (
                                (cloud_scheduler is not None or proxy_pool_scheduler is not None)
                                and request_generation is not None
                                and page_proxy_retries < (
                                    ICP_PROXY_WAF_RETRIES
                                    if "创宇盾" in str(exc)
                                    else ICP_PROXY_ERROR_RETRIES
                                )
                            ):
                                page_proxy_retries += 1
                                if cloud_scheduler is not None:
                                    await cloud_scheduler.rotate(request_generation)
                                elif proxy_pool_scheduler is not None and request_route_index is not None:
                                    await proxy_pool_scheduler.rotate(request_route_index, request_generation)
                                    company_route_index = None
                                continue
                            if expected_total is not None and len(aggregate_rows) == expected_total:
                                await save_complete_result()
                                return None
                            detail = str(exc) or "ICP 页面请求失败"
                            if page_proxy_retries:
                                action = "已重建隧道" if "创宇盾" in detail else "已切换代理"
                                detail = f"{detail}（{action} {page_proxy_retries} 次）"
                            return _failure(name, started, attempted_pages, detail)

                        page_proxy_retries = 0
                        reported_pages = max(1, int(chunk["pages"] or 1))
                        reported_total = chunk.get("total")
                        if reported_total is not None:
                            reported_total = int(reported_total)
                            if expected_total is None:
                                expected_total = reported_total
                            elif reported_total != expected_total:
                                inconsistent = True
                                last_incomplete_reason = (
                                    f"不同分页返回的总数不一致（{expected_total} / {reported_total}）"
                                )
                                break
                            if pass_total is None:
                                pass_total = reported_total
                            elif reported_total != pass_total:
                                inconsistent = True
                                last_incomplete_reason = (
                                    f"同一次分页返回的总数不一致（{pass_total} / {reported_total}）"
                                )
                                break

                        if pass_pages is None:
                            pass_pages = reported_pages
                            total_pages = reported_pages
                            if total_pages > MAX_PAGES:
                                return _failure(
                                    name,
                                    started,
                                    attempted_pages,
                                    f"上游报告 {total_pages} 页，超过安全上限 {MAX_PAGES} 页",
                                )
                        elif reported_pages != pass_pages:
                            inconsistent = True
                            last_incomplete_reason = (
                                f"同一次分页返回的页数不一致（{pass_pages} / {reported_pages}）"
                            )
                            break

                        page_rows = chunk["rows"]
                        aggregate_chunks.append(page_rows)
                        for row in page_rows:
                            if not isinstance(row, dict):
                                continue
                            key = _icp_row_key(row, name)
                            if key is None:
                                continue
                            aggregate_rows.setdefault(key, row)

                        if expected_total is not None:
                            if len(aggregate_rows) == expected_total:
                                await save_complete_result()
                                return None
                            if len(aggregate_rows) > expected_total:
                                return _failure(
                                    name,
                                    started,
                                    attempted_pages,
                                    f"上游报告 {expected_total} 条，但分页合并得到 "
                                    f"{len(aggregate_rows)} 条，结果不一致",
                                )

                        if page >= total_pages:
                            break
                        page += 1

                    if not inconsistent and expected_total is None:
                        await save_complete_result()
                        return None

                    if not last_incomplete_reason:
                        last_incomplete_reason = (
                            f"上游报告 {expected_total} 条，分页合并后仅获取 "
                            f"{len(aggregate_rows)} 条"
                        )
                    recovery_passes = ICP_PAGINATION_RECOVERY_PASSES
                    if expected_total and not aggregate_rows:
                        recovery_passes = min(
                            recovery_passes,
                            ICP_EMPTY_RESULT_RECOVERY_PASSES,
                        )
                    if pagination_pass >= recovery_passes:
                        break

                if expected_total is not None:
                    detail = (
                        f"上游报告 {expected_total} 条，分页合并后仅获取 "
                        f"{len(aggregate_rows)} 条，结果不完整"
                    )
                else:
                    detail = last_incomplete_reason or "分页结果不完整"
                return _failure(name, started, attempted_pages, detail)
        except TimeoutError:
            return _failure(
                name,
                started,
                attempted_pages,
                f"单企业{route_label}查询总预算达到 {ICP_COMPANY_TIMEOUT_SECONDS:g} 秒",
            )
        except Exception as exc:  # noqa: BLE001 - isolate one enterprise
            return _failure(name, started, attempted_pages, str(exc) or "ICP 查询失败")

    try:
        async with httpx.AsyncClient(
            base_url=settings.miit_api_url,
            timeout=ICP_PAGE_TIMEOUT_SECONDS,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            # Cloud mode limits active companies to five. Each company's page
            # pass has its own SeaMoon tunnel and session affinity; direct-mode
            # WAF pacing is handled by the scheduler above.
            # Retry in rounds so successful companies leave the queue while
            # failed companies get a fresh session after a short cooldown.
            pending = list(names)
            last_failures: dict[str, CompanyFailure] = {}
            attempt_counts: dict[str, int] = {name: 0 for name in names}
            attempted_pages: dict[str, int] = {name: 0 for name in names}
            elapsed_seconds: dict[str, float] = {name: 0.0 for name in names}

            for attempt_index in range(ICP_COMPANY_MAX_ATTEMPTS):
                round_outcomes: list[tuple[str, CompanyFailure | None]] = []
                for batch_start in range(0, len(pending), batch_size):
                    batch = pending[batch_start:batch_start + batch_size]
                    # Keep the request scheduler as the single gate for a
                    # fixed direct IP, but run independent companies concurrently
                    # behind it. This preserves the 0.4s inter-request gap and
                    # five-request cooldown while allowing request latency to be
                    # pipelined. Serialising the whole company batch made a
                    # normal direct run pay one network round-trip per company
                    # on top of the WAF pacing (the main source of the observed
                    # ~50 minute run for 1,280 names).
                    #
                    # Proxy-backed mode uses the same bounded gather. Manual
                    # proxy pools reduce ``batch_size`` to their ready-node
                    # count, while cloud mode stays at five logical companies.
                    batch_results = await asyncio.gather(
                        *(collect_company_once(name, client) for name in batch)
                    )
                    round_outcomes.extend(zip(batch, batch_results, strict=True))

                retry_names: list[str] = []
                for name, outcome in round_outcomes:
                    attempt_counts[name] += 1
                    if outcome is None:
                        last_failures.pop(name, None)
                        continue
                    attempted_pages[name] += outcome.attempted_pages
                    elapsed_seconds[name] += outcome.elapsed_seconds
                    last_failures[name] = outcome
                    retry_names.append(name)

                pending = retry_names
                if not pending or attempt_index + 1 >= ICP_COMPANY_MAX_ATTEMPTS:
                    break
                await asyncio.sleep(
                    ICP_COMPANY_RETRY_BACKOFF_SECONDS * (attempt_index + 1)
                )

            failed: list[CompanyFailure] = []
            for name in names:
                last_failure = last_failures.get(name)
                if last_failure is None:
                    continue
                attempts = attempt_counts[name]
                retries = max(0, attempts - 1)
                detail = last_failure.error
                if retries:
                    detail = f"{detail}（企业级自动重试 {retries} 次后仍失败）"
                failed.append(
                    CompanyFailure(
                        name=name,
                        attempted_pages=attempted_pages[name],
                        error=detail,
                        elapsed_seconds=elapsed_seconds[name],
                        company_attempts=attempts,
                    )
                )
    except Exception as exc:  # noqa: BLE001 - ICP is best effort
        return [f"ICP备案查询失败：{_clean(exc) or type(exc).__name__}"]

    if not failed:
        return []

    ok = len(names) - len(failed)
    details = "；".join(
        f"{item.name}：企业尝试 {item.company_attempts} 次，"
        f"请求页面 {item.attempted_pages} 次，"
        f"最终错误：{item.error}，实际耗时：{item.elapsed_seconds:.2f} 秒"
        for item in failed[:3]
    )
    if len(failed) > 3:
        details += f"；其余 {len(failed) - 3} 家失败详情见任务日志"
    if ok:
        return [f"ICP备案：{ok} 家查询完成，{len(failed)} 家失败；{details}"]
    return [f"ICP备案：{len(failed)} 家企业查询失败；{details}"]


async def collect_icp(repo: Repository, run_id: UUID, names: list[str]) -> list[str]:
    """Collect ICP records with one process-wide route schedule per loop."""
    async with _collection_lock():
        return await _collect_icp(repo, run_id, names)
