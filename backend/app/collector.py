from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from app.concurrency import drain_queue, gather_cancel_on_error
from app.providers.pagination import IncompletePagination
from app.miit import collect_icp, collect_icp_from_queue
from app.settings import settings
from app.providers.names import provider_label
from app.providers.tianyancha import (
    INVEST_URL,
    Company,
    ProviderError,
)
from app.repository import LeaseLost, Repository
from app.serverless_proxy import ensure_icp_node_pool, prewarm_cloud_nodes, release_icp_node_pool


ICP_HEARTBEAT_SECONDS = 5
ICP_STREAM_POLL_SECONDS = 0.4
# Feed ICP as soon as a handful of names exist. The collector now owns a
# rolling queue instead of waiting for 200 names to finish before the next
# wave can start.
ICP_STREAM_MIN_START = 1
# Traverse investment companies concurrently. The previous BFS visited one
# company at a time, so a 1500-node tree paid full RTT for every node.
INVEST_CONCURRENCY = 20
INVEST_HEARTBEAT_EVERY = 8


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
    names: list[str] | asyncio.Queue,
) -> list[str]:
    async def keepalive() -> None:
        while True:
            await asyncio.sleep(ICP_HEARTBEAT_SECONDS)
            await repo.touch_run(spec.id, lease_id=spec.lease_id)

    if isinstance(names, asyncio.Queue):
        collect_task = asyncio.create_task(collect_icp_from_queue(repo, spec.id, names))
    else:
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
    Each discovered name is claimed once for this run and appended to one
    rolling ICP collector so later companies do not wait for a 200-name wave
    to finish.
    """
    seen: set[str] = set()
    errors: list[str] = []
    scale_errors_seen: set[str] = set()
    pool_released = False
    warmed = False
    incoming: asyncio.Queue[str | None] = asyncio.Queue()
    collector_task: asyncio.Task[list[str]] | None = None
    name_rel_cursor = 0
    name_res_cursor = 0

    def _record_scale_errors(prefix: str, values) -> None:
        for scale_error in map(str, values or []):
            if scale_error not in scale_errors_seen:
                scale_errors_seen.add(scale_error)
                errors.append(prefix + scale_error)

    async def start_collector(discovered_count: int) -> None:
        nonlocal collector_task, warmed
        if collector_task is not None:
            return
        if getattr(repo, "get_runtime_config", None) is not None:
            try:
                # Deep investment trees need the full node pool immediately.
                # Waiting until 160/320 names exist left the first minutes on
                # a single function and blew the 15-minute budget.
                if not producers_done.is_set() and spec.depth >= 3:
                    scale_count = max(
                        discovered_count,
                        settings.icp_auto_scale_max_nodes
                        * settings.icp_auto_scale_companies_per_node,
                    )
                else:
                    scale_count = discovered_count
                scale_result = await ensure_icp_node_pool(repo, scale_count)
                _record_scale_errors(
                    "ICP备案节点自动扩容：",
                    scale_result.get("errors") if isinstance(scale_result, dict) else [],
                )
            except Exception as extra:  # noqa: BLE001 - scaling is best effort
                errors.append(f"ICP备案节点自动扩容失败：{extra}")
            if not warmed:
                warmed = True
                try:
                    warm = await prewarm_cloud_nodes(await repo.get_runtime_config())
                    _record_scale_errors("ICP备案节点预热：", (warm or {}).get("errors") or [])
                except Exception as extra:  # noqa: BLE001 - prewarm is best effort
                    detail = f"ICP备案节点预热失败：{extra}"
                    if detail not in scale_errors_seen:
                        scale_errors_seen.add(detail)
                        errors.append(detail)
        collector_task = asyncio.create_task(
            _collect_icp_with_heartbeat(repo, spec, incoming)
        )

    async def feed(pending: list[str]) -> None:
        for name in pending:
            if name in seen:
                continue
            seen.add(name)
            await incoming.put(name)

    try:
        while True:
            done_before_read = producers_done.is_set()
            entity_changed.clear()
            since = getattr(repo, "entity_names_since", None)
            # Sequence IDs are assigned before commit. A slow transaction can
            # commit below a cursor already observed from a faster writer.
            # Reconcile once against the authoritative set after all writers
            # finish; running discovery still uses cheap incremental reads.
            if done_before_read and getattr(repo, "entity_names_for_run", None) is not None:
                since = None
            if since is not None:
                new_names, name_rel_cursor, name_res_cursor = await since(
                    spec.id, name_rel_cursor, name_res_cursor
                )
                pending = [
                    name for name in _prioritize_root_name(new_names, spec.keyword)
                    if name and name not in seen
                ]
                discovered_count = len(seen) + len(pending)
            else:
                discovered = _prioritize_root_name(
                    await repo.entity_names_for_run(spec.id), spec.keyword
                )
                pending = [name for name in discovered if name not in seen]
                discovered_count = len(discovered)
            ready_to_start = bool(pending) and (
                producers_done.is_set() or discovered_count >= max(1, ICP_STREAM_MIN_START)
            )
            if ready_to_start:
                await start_collector(discovered_count)
                await feed(pending)
            if producers_done.is_set():
                if not done_before_read:
                    # The last producer may have committed while the snapshot
                    # was being read. Drain once after all producers finish.
                    continue
                remaining = [name for name in pending if name not in seen]
                if collector_task is None and remaining:
                    await start_collector(discovered_count)
                if remaining:
                    await feed(remaining)
                if collector_task is not None:
                    await incoming.put(None)
                    try:
                        errors.extend(await collector_task)
                    except LeaseLost:
                        raise
                    except Exception as extra:  # noqa: BLE001 - ICP is best effort
                        errors.append(f"ICP备案：{extra}")
                    collector_task = None
                if not pool_released:
                    pool_released = True
                    if getattr(repo, "get_runtime_config", None) is not None:
                        try:
                            release_result = await release_icp_node_pool(repo, max(len(seen), discovered_count))
                            _record_scale_errors(
                                "ICP备案节点自动缩容：",
                                release_result.get("errors") if isinstance(release_result, dict) else [],
                            )
                        except Exception as extra:  # noqa: BLE001 - scaling is best effort
                            detail = f"ICP备案节点自动缩容失败：{extra}"
                            if detail not in scale_errors_seen:
                                scale_errors_seen.add(detail)
                                errors.append(detail)
                return errors

            change_task = asyncio.create_task(entity_changed.wait())
            done_task = asyncio.create_task(producers_done.wait())
            try:
                done, _ = await asyncio.wait(
                    {change_task, done_task},
                    timeout=ICP_STREAM_POLL_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done_task in done and producers_done.is_set():
                    continue
            finally:
                for task in (change_task, done_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(change_task, done_task, return_exceptions=True)
    finally:
        if collector_task is not None and not collector_task.done():
            try:
                incoming.put_nowait(None)
            except Exception:
                pass
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)


async def collect_run(
    repo: Repository,
    providers: list,
    spec: RunSpec,
    provider_lock: asyncio.Lock | None = None,
) -> list[str]:
    errors: list[str] = []

    async def run_provider(provider) -> list[str]:
        label = _provider_label(provider)
        try:
            return await _collect_one(repo, provider, spec, entity_changed)
        except LeaseLost:
            raise
        except Exception as exc:  # noqa: BLE001 - surface per-source failure
            return [f"{label}：{exc}"]

    # Start the ICP consumer before provider traversal. As soon as the root or
    # an investment child is persisted, it becomes eligible for an ICP lookup;
    # the provider traversal and ICP batches therefore overlap instead of
    # waiting for all investment levels to finish first.
    producers_done = asyncio.Event()
    entity_changed = asyncio.Event()
    icp_task: asyncio.Task[list[str]] | None = None

    async def run_providers() -> list[list[str]]:
        nonlocal icp_task
        # Start ICP only after this run owns the provider lock. Otherwise a
        # second worker can take the ICP collection lock while waiting for
        # providers, and deadlock with the first worker.
        icp_task = asyncio.create_task(
            _collect_icp_as_entities_are_discovered(
                repo, spec, producers_done, entity_changed
            )
        )
        return await gather_cancel_on_error(
            *(run_provider(provider) for provider in providers)
        )

    try:
        if provider_lock is not None:
            async with provider_lock:
                provider_results = await run_providers()
        else:
            provider_results = await run_providers()
    except BaseException:
        producers_done.set()
        if icp_task is not None:
            icp_task.cancel()
            await asyncio.gather(icp_task, return_exceptions=True)
        raise
    finally:
        producers_done.set()

    for item in provider_results:
        errors.extend(item)

    try:
        if icp_task is not None:
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

    # A level barrier preserves shortest-path depth. Letting fast branches
    # run ahead could mark a company visited at the depth limit before its
    # shorter path arrived, permanently dropping that company's descendants.
    frontier: list[tuple[Company, UUID]] = [(selected, root_id)]
    scheduled = {selected.external_id}
    processed = 0
    errors: list[str] = []
    workers = max(1, min(INVEST_CONCURRENCY, 24))
    failed_company_retries = max(0, int(getattr(provider, "failed_company_retries", 0)))
    next_frontier: list[tuple[Company, UUID]] = []

    async def visit(item: tuple[Company, UUID]) -> None:
        nonlocal processed
        company, entity_id = item
        processed += 1
        current_processed = processed
        if current_processed == 1 or current_processed % INVEST_HEARTBEAT_EVERY == 0:
            await repo.heartbeat(spec.id, current_processed, lease_id=spec.lease_id)
        if "invest" not in spec.fields or level >= spec.depth:
            return
        investments = []
        for attempt in range(failed_company_retries + 1):
            try:
                investments = await provider.all_pages(
                    lambda page: provider.investments(company.external_id, page)
                )
                break
            except IncompletePagination as extra:
                # The paginator already retried failed pages. Keep verified
                # records and traverse their children while exposing the gap.
                investments = extra.rows
                errors.append(f"{source}：{company.name} {extra}")
                break
            except ProviderError as extra:
                if attempt >= failed_company_retries or not extra.retryable:
                    retry_note = f"（失败企业已定向重试 {attempt} 次）" if attempt else ""
                    errors.append(f"{source}：{company.name} {extra}{retry_note}")
                    return
                reset = getattr(provider, "reset_after_failure", None)
                if reset is not None:
                    await reset()

        kept = [
            investment for investment in investments
            if investment.holding_percent is not None and investment.holding_percent >= spec.holding_percent
        ]
        if not kept:
            return
        upsert_entities = getattr(repo, "upsert_entities", None)
        if upsert_entities is not None:
            child_ids = await upsert_entities([
                (provider_id, investment.external_id, investment.name, investment.payload)
                for investment in kept
            ])
        else:
            child_ids = [
                await repo.upsert_entity(provider_id, investment.external_id, investment.name, investment.payload)
                for investment in kept
            ]
        result_rows = []
        relationship_rows = []
        queued_children: list[tuple[Company, UUID]] = []
        for investment, child_id in zip(kept, child_ids, strict=True):
            child = Company(investment.external_id, investment.name, investment.payload)
            ref = f"{child.name} {level + 1}级投资 {investment.holding_percent:.2f}% - {company.name}"
            result_rows.append((
                spec.id, child_id, "invest",
                {"name": investment.name, "holding_percent": investment.holding_percent, "source": source},
                _url(provider, INVEST_URL),
                investment.payload,
            ))
            relationship_rows.append((
                spec.id, entity_id, child_id, "invest", investment.holding_percent, level + 1,
                ref, _url(provider, INVEST_URL), {**investment.payload, "source": source},
            ))
            queued_children.append((child, child_id))
        add_results = getattr(repo, "add_results", None)
        if add_results is not None:
            await add_results(result_rows)
        else:
            for row in result_rows:
                await repo.add_result(*row)
        add_relationships = getattr(repo, "add_relationships", None)
        if add_relationships is not None:
            await add_relationships(relationship_rows)
        else:
            for row in relationship_rows:
                await repo.add_relationship(*row)
        if entity_changed is not None and queued_children:
            entity_changed.set()
        for child, child_id in queued_children:
            if child.external_id not in scheduled:
                scheduled.add(child.external_id)
                next_frontier.append((child, child_id))

    for level in range(spec.depth + 1):
        if not frontier:
            break
        next_frontier = []
        queue: asyncio.Queue[tuple[Company, UUID]] = asyncio.Queue()
        for item in frontier:
            queue.put_nowait(item)
        await drain_queue(queue, visit, min(workers, len(frontier)))
        frontier = next_frontier

    await repo.heartbeat(spec.id, processed, processed, lease_id=spec.lease_id)
    return errors


def _url(provider, path: str) -> str:
    base = getattr(getattr(provider, "client", None), "base_url", "")
    return str(base).rstrip("/") + path
