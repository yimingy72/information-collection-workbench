from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg

from app.collector import RunSpec, collect_run
from app.providers.names import normalize_providers
from app.providers.registry import build_providers, login_required_errors
from app.repository import LeaseLost, Repository, create_pool
from app.serverless_proxy import configure_gateway_for_active_route
from app.settings import settings
from app.subdomains import collect_subdomains

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def wait_for_db() -> asyncpg.Pool:
    for attempt in range(30):
        try:
            return await create_pool(settings.database_url, min_size=1, max_size=10)
        except OSError:
            if attempt == 29:
                raise
            await asyncio.sleep(1)
    raise AssertionError("unreachable")


async def _collect_run_with_heartbeat(
    repo: Repository, spec: RunSpec, providers: list,
) -> list[str]:
    """Keep a run lease alive across provider discovery and ICP collection.

    ICP already has a scoped keepalive, but provider pagination can also take
    longer than the lease window. Without this outer heartbeat the recoverer
    can requeue a still-active run after 120 seconds, causing a second worker
    to duplicate the query and the original worker to finish with ``LeaseLost``.
    """
    interval = max(5.0, min(30.0, settings.worker_lease_seconds / 3))

    async def keepalive() -> None:
        while True:
            await asyncio.sleep(interval)
            await repo.touch_run(spec.id, lease_id=spec.lease_id)

    collect_task = asyncio.create_task(collect_run(repo, providers, spec))
    heartbeat_task = asyncio.create_task(keepalive())
    try:
        done, _ = await asyncio.wait(
            {collect_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if heartbeat_task in done:
            error = heartbeat_task.exception()
            if error is not None:
                raise error
            raise RuntimeError("worker 心跳任务意外结束")
        return collect_task.result()
    finally:
        for task in (collect_task, heartbeat_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(collect_task, heartbeat_task, return_exceptions=True)


async def worker_loop(repo: Repository, provider_lock: asyncio.Lock) -> None:
    while True:
        run = await repo.claim_run(settings.worker_lease_seconds)
        if not run:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        provider_ids = normalize_providers(run["providers"] if "providers" in run.keys() and run["providers"] else [run["provider"]])
        config = await repo.get_runtime_config()
        login_errors = login_required_errors(provider_ids, config)
        providers = []
        spec = RunSpec(
            id=run["id"], keyword=run["keyword"], depth=run["depth"],
            holding_percent=float(run["holding_percent"]), fields=run["fields"],
            providers=provider_ids, lease_id=run["lease_id"],
        )
        try:
            await configure_gateway_for_active_route(config)
            providers = build_providers(provider_ids, config)
            if not providers:
                await repo.finish(spec.id, "failed", "；".join(login_errors) or "请先登录对应数据源", lease_id=spec.lease_id)
                continue
            async with provider_lock:
                errors = list(login_errors)
                errors.extend(await _collect_run_with_heartbeat(repo, spec, providers))
            status = "partial" if errors else "succeeded"
            await repo.finish(spec.id, status, "；".join(errors) if errors else None, lease_id=spec.lease_id)
        except asyncio.CancelledError:
            raise
        except LeaseLost:
            log.warning("lost lease for collection run %s", spec.id)
        except Exception as exc:  # noqa: BLE001 - persist worker failure per run
            log.exception("collection run %s failed", spec.id)
            try:
                status = "partial" if await repo.has_results(spec.id) else "failed"
                await repo.finish(spec.id, status, str(exc), lease_id=spec.lease_id)
            except LeaseLost:
                log.warning("lost lease while finishing collection run %s", spec.id)
        finally:
            for provider in providers:
                close = getattr(provider, "close", None)
                if close:
                    await close()


async def subdomain_worker_loop(repo: Repository) -> None:
    while True:
        run = await repo.claim_subdomain_run()
        if not run:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        run_id = run["id"]
        lease_id = run["lease_id"]
        try:
            warnings = await collect_subdomains(
                repo,
                run_id,
                list(run["domains"] or []),
                dict(run["options"] or {}),
                lease_id=lease_id,
            )
            await repo.finish_subdomain_run(
                run_id, "partial" if warnings else "succeeded", warnings, None,
                lease_id=lease_id,
            )
        except asyncio.CancelledError:
            raise
        except LeaseLost:
            log.warning("lost lease for subdomain run %s", run_id)
        except Exception as exc:  # noqa: BLE001 - persist failure for UI/history
            log.exception("subdomain run %s failed", run_id)
            try:
                await repo.finish_subdomain_run(
                    run_id, "failed", [], str(exc), lease_id=lease_id
                )
            except LeaseLost:
                log.warning("lost lease while finishing subdomain run %s", run_id)


async def recover_loop(repo: Repository) -> None:
    while True:
        await repo.recover_stale(settings.worker_lease_seconds)
        await asyncio.sleep(max(5.0, settings.worker_lease_seconds / 2))


async def run_worker() -> None:
    pool = await wait_for_db()
    repo = Repository(pool, Path(__file__).parent.parent / "migrations")
    await repo.migrate()
    lock = asyncio.Lock()
    workers = [asyncio.create_task(recover_loop(repo))]
    workers.extend(
        asyncio.create_task(worker_loop(repo, lock))
        for _ in range(max(1, settings.worker_concurrency))
    )
    workers.extend(
        asyncio.create_task(subdomain_worker_loop(repo))
        for _ in range(max(1, settings.subdomain_worker_concurrency))
    )
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
