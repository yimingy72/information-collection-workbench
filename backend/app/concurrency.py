"""Bounded workers whose failures and cancellation cannot strand a queue."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar
from weakref import WeakKeyDictionary

T = TypeVar("T")
_semaphores: WeakKeyDictionary = WeakKeyDictionary()


def shared_semaphore(name: str, concurrency: int) -> asyncio.Semaphore:
    """One budget per process/event loop, including simultaneous runs."""
    budgets = _semaphores.setdefault(asyncio.get_running_loop(), {})
    key = (name, concurrency)
    if key not in budgets:
        budgets[key] = asyncio.Semaphore(concurrency)
    return budgets[key]


async def gather_cancel_on_error(*awaitables):
    tasks = [asyncio.create_task(item) for item in awaitables]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def supervise(producer: Awaitable[None], workers: list[Awaitable[None]]) -> None:
    """Run a producer/drainer and workers as one lifetime; propagate failures."""
    tasks = [asyncio.create_task(worker) for worker in workers]
    driver = asyncio.create_task(producer)
    try:
        pending = {*tasks, driver}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if driver in done:
                return
    finally:
        for task in [driver, *tasks]:
            if not task.done():
                task.cancel()
        await asyncio.gather(driver, *tasks, return_exceptions=True)


async def drain_queue(
    queue: asyncio.Queue[T], process: Callable[[T], Awaitable[None]], concurrency: int,
) -> None:
    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                await process(item)
            finally:
                queue.task_done()

    await supervise(queue.join(), [worker() for _ in range(max(1, concurrency))])
