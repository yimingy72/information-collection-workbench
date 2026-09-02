from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary
from uuid import UUID, uuid4

import httpx

from app.repository import Repository
from app.serverless_proxy import manual_proxy_urls, miit_proxy_urls
from app.settings import settings

MAX_PAGES = 50
# YMICP's own implementation documents 26 as the maximum reliable page size.
# Larger values are silently normalized upstream and can destabilize pagination.
PAGE_SIZE = 26
ICP_PAGINATION_RECOVERY_PASSES = 2


# Keep one slot below the cloud-function instance concurrency of six.
ICP_BATCH_SIZE = 5
# Kept as a compatibility knob for existing deployments/tests that used the
# old concurrency setting. It can only reduce the batch size, never increase it.
ICP_CONCURRENCY = ICP_BATCH_SIZE
ICP_DIRECT_REQUEST_GAP_SECONDS = 0.4
ICP_BATCH_PAUSE_SECONDS = 12.0
ICP_PAGE_TIMEOUT_SECONDS = 10
ICP_COMPANY_TIMEOUT_SECONDS = 30
# In cloud mode this counts actual ICP page requests, not companies. Reaching
# the limit starts a new YMICP page session, which makes YMICP open a fresh
# HTTP-proxy TCP connection and therefore a fresh SeaMoon WebSocket tunnel.
ICP_PROXY_REQUEST_LIMIT = 5
# A WAF response should not discard the current page immediately. Retry it a
# bounded number of times after the route generation has been rotated.
ICP_PROXY_WAF_RETRIES = 2

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
    seen: set[tuple[str, str]],
) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        domain = _clean(row.get("domain"))
        service_licence = _clean(row.get("serviceLicence"))
        key = (domain, service_licence)
        if not domain or key in seen:
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


def _failure(
    name: str,
    started: float,
    attempted_pages: int,
    error: str,
) -> CompanyFailure:
    elapsed = max(0.0, asyncio.get_running_loop().time() - started)
    return CompanyFailure(name, attempted_pages, error, elapsed)


async def _collect_icp(repo: Repository, run_id: UUID, names: list[str]) -> list[str]:
    if not settings.miit_api_url:
        return []
    names = list(dict.fromkeys(_clean(name) for name in names if _clean(name)))
    if not names:
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
    # A single manual proxy is a fixed route: do not apply the cloud tunnel
    # generation counter to it. Otherwise its pagination session would be
    # replaced every five pages even though no new exit IP can be created.
    cloud_scheduler = (
        _IcpCloudRotationScheduler(ICP_PROXY_REQUEST_LIMIT)
        if using_cloud_proxy and route_proxy
        else None
    )
    proxy_pool_scheduler = _IcpProxyPoolScheduler(route_proxies, ICP_PROXY_REQUEST_LIMIT) if len(route_proxies) > 1 else None
    request_scheduler = _IcpRequestScheduler(batch_size, direct=not route_proxies)

    async def collect_company(name: str, client: httpx.AsyncClient) -> CompanyFailure | None:
        started = asyncio.get_running_loop().time()
        attempted_pages = 0
        expected_total: int | None = None
        aggregate_rows: dict[tuple[str, str], dict[str, Any]] = {}
        aggregate_chunks: list[list[Any]] = []
        last_incomplete_reason = ""

        async def save_complete_result() -> None:
            saved_seen: set[tuple[str, str]] = set()
            for page_rows in aggregate_chunks:
                await _save_icp_page(repo, run_id, name, page_rows, saved_seen)

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
                                    else max(0, len(route_proxies) - 1)
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
                            domain = _clean(row.get("domain"))
                            if not domain:
                                continue
                            key = (domain, _clean(row.get("serviceLicence")))
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
                    if pagination_pass >= ICP_PAGINATION_RECOVERY_PASSES:
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
            outcomes: list[CompanyFailure | None] = []
            for batch_start in range(0, len(names), batch_size):
                batch = names[batch_start:batch_start + batch_size]
                if route_proxy or proxy_pool_scheduler is not None:
                    # Proxy-backed mode intentionally allows five logical
                    # company lookups at once. Each route has its own request
                    # budget; the next batch starts after gather.
                    batch_outcomes = await asyncio.gather(
                        *(collect_company(name, client) for name in batch)
                    )
                else:
                    # Direct mode follows the reference strategy: do not fire
                    # five requests at exactly the same instant. The scheduler
                    # inserts the 0.4s gap and the cooldown between request
                    # rounds, keeping the burst predictable.
                    batch_outcomes = []
                    for name in batch:
                        batch_outcomes.append(await collect_company(name, client))
                outcomes.extend(batch_outcomes)

            failed = [item for item in outcomes if item is not None]
    except Exception as exc:  # noqa: BLE001 - ICP is best effort
        return [f"ICP备案查询失败：{_clean(exc) or type(exc).__name__}"]

    if not failed:
        return []

    ok = len(names) - len(failed)
    details = "；".join(
        f"{item.name}：请求页面 {item.attempted_pages} 次，最终错误：{item.error}，实际耗时：{item.elapsed_seconds:.2f} 秒"
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
