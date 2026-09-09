import asyncio

import pytest

from app.concurrency import drain_queue


@pytest.mark.asyncio
async def test_failed_worker_does_not_strand_remaining_queue():
    queue = asyncio.Queue()
    for i in range(20):
        queue.put_nowait(i)
    active = 0

    async def process(i):
        nonlocal active
        active += 1
        try:
            await asyncio.sleep(0)
            if i == 0:
                raise RuntimeError("database unavailable")
            await asyncio.Event().wait()
        finally:
            active -= 1

    with pytest.raises(RuntimeError, match="database unavailable"):
        await asyncio.wait_for(drain_queue(queue, process, 3), 1)
    assert active == 0


@pytest.mark.asyncio
async def test_cancel_drains_worker_lifetimes_before_returning():
    queue = asyncio.Queue()
    for i in range(10):
        queue.put_nowait(i)
    started = asyncio.Event()
    active = 0

    async def process(i):
        nonlocal active
        active += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    task = asyncio.create_task(drain_queue(queue, process, 3))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0
