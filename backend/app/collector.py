from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from app.miit import collect_icp
from app.providers.names import provider_label
from app.providers.tianyancha import (
    INVEST_URL,
    Company,
    ProviderError,
)
from app.repository import LeaseLost, Repository


ICP_HEARTBEAT_SECONDS = 30
ICP_STREAM_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class RunSpec:
    id: UUID
    keyword: str
    depth: int
    holding_percent: float
    fields: list[str]
    providers: list[str] = field(default_factory=lambda: ["tianyancha"])
    lease_id: UUID | None = None


def _provider_label(provider) -> str:
    return getattr(provider, "label", provider_label(getattr(provider, "id", "")))


async def _collect_icp_with_heartbeat(
    repo: Repository,
    spec: RunSpec,
    names: list[str],
) -> list[str]:
    async def keepalive() -> None:
        while True:
            await asyncio.sleep(ICP_HEARTBEAT_SECONDS)
            await repo.touch_run(spec.id, lease_id=spec.lease_id)

    collect_task = asyncio.create_task(collect_icp(repo, spec.id, names))
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
            raise RuntimeError("ICP 心跳任务意外结束")
        return collect_task.result()
    finally:
        for task in (collect_task, heartbeat_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(collect_task, heartbeat_task, return_exceptions=True)


def _prioritize_root_name(names: list[str], root_name: str) -> list[str]:
    """Query the requested company before alphabetically ordered investments."""
    root_name = " ".join(str(root_name or "").split())
    unique = list(dict.fromkeys(" ".join(str(name or "").split()) for name in names))
    unique = [name for name in unique if name]
    if root_name and root_name in unique:
        return [root_name, *(name for name in unique if name != root_name)]
    return unique


async def _collect_icp_as_entities_are_discovered(
    repo: Repository,
    spec: RunSpec,
    producers_done: asyncio.Event,
    entity_changed: asyncio.Event,
) -> list[str]:
    """Query ICP while providers are still discovering investment entities.

    The repository query intentionally excludes ``icp`` results, otherwise an
    ICP response could feed its own unit names back into this queue forever.
    Each discovered name is claimed once for this run; ``collect_icp`` keeps
    the existing per-batch concurrency, page timeout, deduplication, and route
    rotation rules.
    """
    seen: set[str] = set()
    errors: list[str] = []
    while True:
        discovered = _prioritize_root_name(
            await repo.entity_names_for_run(spec.id), spec.keyword
        )
        pending = [name for name in discovered if name not in seen]
        if pending:
            seen.update(pending)
            try:
                errors.extend(await _collect_icp_with_heartbeat(repo, spec, pending))
            except LeaseLost:
                raise
            except Exception as exc:  # noqa: BLE001 - ICP is best effort
                errors.append(f"ICP备案：{exc}")
            continue
        if producers_done.is_set():
            return errors

        # Wait for a provider to persist another entity, or for all providers
        # to finish. The timeout is only a safety net for repositories that do
        # not expose an event-backed notification; normal discovery wakes this
        # consumer immediately.
        entity_changed.clear()
        change_task = asyncio.create_task(entity_changed.wait())
        done_task = asyncio.create_task(producers_done.wait())
        try:
            done, _ = await asyncio.wait(
                {change_task, done_task},
                timeout=ICP_STREAM_POLL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done_task in done and producers_done.is_set():
                return errors
        finally:
            for task in (change_task, done_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(change_task, done_task, return_exceptions=True)


async def collect_run(repo: Repository, providers: list, spec: RunSpec) -> list[str]:
    errors: list[str] = []

    async def run_provider(provider) -> list[str]:
        label = _provider_label(provider)
        try:
            return await _collect_one(repo, provider, spec, entity_changed)
        except Exception as exc:  # noqa: BLE001 - surface per-source failure
            return [f"{label}：{exc}"]

    # Start the ICP consumer before provider traversal. As soon as the root or
    # an investment child is persisted, it becomes eligible for an ICP lookup;
    # the provider traversal and ICP batches therefore overlap instead of
    # waiting for all investment levels to finish first.
    producers_done = asyncio.Event()
    entity_changed = asyncio.Event()
    icp_task = asyncio.create_task(
        _collect_icp_as_entities_are_discovered(
            repo, spec, producers_done, entity_changed
        )
    )
    try:
        provider_results = await asyncio.gather(
            *(run_provider(provider) for provider in providers)
        )
    except BaseException:
        producers_done.set()
        icp_task.cancel()
        await asyncio.gather(icp_task, return_exceptions=True)
        raise
    finally:
        producers_done.set()

    for item in provider_results:
        errors.extend(item)

    try:
        errors.extend(await icp_task)
    except LeaseLost:
        raise
    except Exception as exc:  # noqa: BLE001 - ICP is best effort
        errors.append(f"ICP备案：{exc}")

    if not await repo.has_results(spec.id):
        raise ProviderError("；".join(errors) or "没有查询到结果")
    return errors


async def _collect_one(
    repo: Repository,
    provider,
    spec: RunSpec,
    entity_changed: asyncio.Event | None = None,
) -> list[str]:
    source = _provider_label(provider)
    provider_id = getattr(provider, "id", "tianyancha")
    selected, candidates = await provider.search(spec.keyword)
    root_id = await repo.upsert_entity(provider_id, selected.external_id, selected.name, selected.payload)
    await repo.add_result(
        spec.id, root_id, "company_selection",
        {"selected": selected.payload, "candidates": [candidate.payload for candidate in candidates], "source": source},
        _url(provider, "/search"),
        {"selected": selected.payload, "candidates": [candidate.payload for candidate in candidates]},
    )
    if entity_changed is not None:
        entity_changed.set()

    queue: list[tuple[Company, UUID, int]] = [(selected, root_id, 0)]
    queued: set[str] = {f"{provider_id}:{selected.external_id}"}
    completed: set[str] = set()
    counted: set[str] = set()
    retry_counts: dict[str, int] = {}
    failed_company_retries = max(0, int(getattr(provider, "failed_company_retries", 0)))
    processed = 0
    errors: list[str] = []
    while queue:
        company, entity_id, level = queue.pop(0)
        visit_key = f"{provider_id}:{company.external_id}"
        queued.discard(visit_key)
        if visit_key in completed:
            continue
        if visit_key not in counted:
            counted.add(visit_key)
            processed += 1
        await repo.heartbeat(spec.id, processed, lease_id=spec.lease_id)

        if "invest" not in spec.fields or level >= spec.depth:
            completed.add(visit_key)
            continue
        try:
            investments = await provider.all_pages(lambda page, current=company: provider.investments(current.external_id, page))
        except ProviderError as exc:
            retries = retry_counts.get(visit_key, 0)
            if retries < failed_company_retries:
                retry_counts[visit_key] = retries + 1
                reset = getattr(provider, "reset_after_failure", None)
                if reset is not None:
                    await reset()
                if visit_key not in queued:
                    queue.append((company, entity_id, level))
                    queued.add(visit_key)
                continue
            completed.add(visit_key)
            retry_note = f"（失败企业已定向重试 {retries} 次）" if retries else ""
            errors.append(f"{source}：{company.name} {exc}{retry_note}")
            continue
        completed.add(visit_key)
        for investment in investments:
            if investment.holding_percent is None or investment.holding_percent < spec.holding_percent:
                continue
            child = Company(investment.external_id, investment.name, investment.payload)
            child_id = await repo.upsert_entity(provider_id, child.external_id, child.name, child.payload)
            ref = f"{child.name} {level + 1}级投资 {investment.holding_percent:.2f}% - {company.name}"
            await repo.add_result(
                spec.id, child_id, "invest",
                {"name": investment.name, "holding_percent": investment.holding_percent, "source": source},
                _url(provider, INVEST_URL),
                investment.payload,
            )
            await repo.add_relationship(
                spec.id, entity_id, child_id, "invest", investment.holding_percent, level + 1,
                ref, _url(provider, INVEST_URL), {**investment.payload, "source": source},
            )
            if entity_changed is not None:
                entity_changed.set()
            child_key = f"{provider_id}:{child.external_id}"
            if child_key not in completed and child_key not in queued:
                queue.append((child, child_id, level + 1))
                queued.add(child_key)

    await repo.heartbeat(spec.id, processed, processed, lease_id=spec.lease_id)
    return errors


def _url(provider, path: str) -> str:
    base = getattr(getattr(provider, "client", None), "base_url", "")
    return str(base).rstrip("/") + path
