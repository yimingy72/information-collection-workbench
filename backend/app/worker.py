from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg

from app.collector import RunSpec, collect_run
from app.providers.names import normalize_providers
from app.providers.registry import build_providers, login_required_errors
from app.repository import LeaseLost, Repository, create_pool
from app.serverless_proxy import configure_gateway
from app.settings import settings

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
            await configure_gateway(config)
            providers = build_providers(provider_ids, config)
            if not providers:
                await repo.finish(spec.id, "failed", "；".join(login_errors) or "请先登录对应数据源", lease_id=spec.lease_id)
                continue
            async with provider_lock:
                errors = list(login_errors)
                errors.extend(await collect_run(repo, providers, spec))
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
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
