"""Shared pagination: bounded requests, stable identities, explicit completeness."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any
from weakref import WeakKeyDictionary

from app.concurrency import shared_semaphore
from app.settings import settings


class ProviderError(RuntimeError):
    retryable = True


class ProviderRateLimited(ProviderError):
    retryable = False

    def __init__(self, message: str, retry_after: str = "60") -> None:
        super().__init__(message)
        try:
            self.delay = min(900.0, max(1.0, float(retry_after)))
        except ValueError:
            self.delay = 60.0


_cooldowns: WeakKeyDictionary = WeakKeyDictionary()


class IncompletePagination(ProviderError):
    def __init__(self, message: str, rows: list[Any]) -> None:
        super().__init__(message)
        self.rows = rows


def bounded_request(function):
    """Share a source's budget across companies, pages and provider instances."""
    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        async with shared_semaphore(f"provider:{self.id}", settings.provider_request_concurrency):
            loop = asyncio.get_running_loop()
            cooldowns = _cooldowns.setdefault(loop, {})
            remaining = cooldowns.get(self.id, 0) - loop.time()
            if remaining > 0:
                raise ProviderRateLimited(f"{self.label}请求频率受限，约 {max(1, int(remaining))} 秒后可重试", str(remaining))
            try:
                return await function(self, *args, **kwargs)
            except ProviderRateLimited as exc:
                cooldowns[self.id] = max(cooldowns.get(self.id, 0), loop.time() + exc.delay)
                raise
    return wrapped


def reported_total(payload: dict, *keys: str) -> int | None:
    for key in keys:
        if payload.get(key) is not None and payload[key] != "":
            try:
                total = int(payload[key])
            except (TypeError, ValueError) as exc:
                raise ProviderError(f"上游总数格式异常：{key}") from exc
            if total < 0:
                raise ProviderError(f"上游总数不能为负数：{key}")
            return total
    return None


async def all_pages(
    fetch: Callable[[int], Awaitable[tuple[list[Any], int | None]]],
    *, label: str, page_size: int, max_pages: int | None = None,
) -> list[Any]:
    page_size = max(1, page_size)
    width = max(1, settings.provider_page_concurrency)
    rows: dict[tuple, Any] = {}
    pages: dict[int, list[Any]] = {}
    expected: int | None = None
    errors: dict[int, str] = {}
    inconsistent = False
    rate_limited = False

    def merge(page: int, chunk: list[Any]) -> int:
        before = len(rows)
        for index, item in enumerate(chunk):
            external_id = getattr(item, "external_id", None)
            name = getattr(item, "name", None)
            # Provider records always have an ID or a shareholder name.
            key = ("id", external_id) if external_id else (
                ("name", name) if name else ("position", page, index)
            )
            rows[key] = item
        return len(rows) - before

    async def load(page: int) -> tuple[int, list[Any], int | None, ProviderError | None]:
        for attempt in range(2):
            try:
                chunk, total = await fetch(page)
                return page, chunk, total, None
            except ProviderError as exc:
                if attempt or not exc.retryable:
                    return page, [], None, exc
        raise AssertionError("unreachable")

    async def read(numbers: list[int]) -> None:
        nonlocal expected, inconsistent, rate_limited
        # Allocate only one window, even for a source reporting millions of rows.
        tasks = [asyncio.create_task(load(page)) for page in numbers]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for page, chunk, total, error in results:
            if error:
                errors[page] = str(error)
                rate_limited = rate_limited or not error.retryable
                continue
            errors.pop(page, None)
            pages[page] = chunk
            merge(page, chunk)
            if total is not None:
                if expected is not None and total != expected:
                    inconsistent = True
                expected = max(expected or 0, total)

    def fail(reason: str) -> None:
        count = f"{len(rows)}/{expected}" if expected is not None else str(len(rows))
        raise IncompletePagination(f"{label}分页结果不完整（已获取 {count} 条）：{reason}", list(rows.values()))

    await read([1])
    if errors:
        fail(errors[1])
    effective_size = len(pages[1]) or page_size
    page = 2
    if expected is not None:
        while page <= max(1, (expected + effective_size - 1) // effective_size):
            last = (expected + effective_size - 1) // effective_size
            if max_pages is not None:
                last = min(last, max_pages)
                if page > last:
                    fail(f"超过显式页数限制 {max_pages}")
            numbers = list(range(page, min(last + 1, page + width)))
            await read(numbers)
            if rate_limited:
                fail("来源限流冷却，未读取的分页需要稍后重查")
            page = numbers[-1] + 1
        # A changing total must never be disguised by slicing the accumulated rows.
        if inconsistent:
            fail("不同分页返回的总数不一致")
        if errors:
            fail("；".join(f"第 {p} 页：{error}" for p, error in errors.items()))
        if len(rows) < expected:
            # One bounded recovery pass merges overlapping page snapshots.
            for start in range(1, page, width):
                await read(list(range(start, min(page, start + width))))
                if rate_limited:
                    break
            if inconsistent or errors or len(rows) != expected:
                fail("分页重叠、缺页或总数变化，补全后仍未对齐")
        if len(rows) != expected:
            fail("唯一记录数与上游总数不一致")
        return list(rows.values())

    # With no authoritative total, only a terminal short/empty page can end
    # traversal; a repeated full page is an anomaly, never a success signal.
    while len(pages[page - 1]) >= page_size:
        if max_pages is not None and page > max_pages:
            fail(f"超过显式页数限制 {max_pages}")
        before = len(rows)
        await read([page])
        if errors:
            fail(errors[page])
        if expected is not None:
            fail("分页过程中才返回总数，需要重新核验")
        if pages[page] and len(rows) == before:
            fail("分页重复且没有新增记录")
        page += 1
    return list(rows.values())
