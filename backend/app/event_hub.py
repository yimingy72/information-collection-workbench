from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator, Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

COLLECTION_EVENTS_CHANNEL = "collection_events"
SUBDOMAIN_EVENTS_CHANNEL = "subdomain_events"
EVENT_CHANNELS = (COLLECTION_EVENTS_CHANNEL, SUBDOMAIN_EVENTS_CHANNEL)


class EventSubscription:
    """A coalescing wake-up signal for one run's SSE stream."""

    def __init__(self, queue: asyncio.Queue[None]) -> None:
        self._queue = queue

    async def wait(self, timeout: float) -> bool:
        """Wait for a notification, returning False when the fallback timer expires."""
        if timeout <= 0:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return False
            return True
        try:
            await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return False
        return True


class PostgresEventHub:
    """Fan out PostgreSQL notifications without one DB connection per SSE client."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._connection: Any | None = None
        self._subscribers: dict[
            tuple[str, str], set[asyncio.Queue[None]]
        ] = defaultdict(set)
        # Keep one stable bound-method object for asyncpg's add/remove calls.
        self._listener_callback = self._on_notification

    @property
    def started(self) -> bool:
        return self._connection is not None

    async def start(self) -> None:
        if self._connection is not None:
            return
        get_max_size = getattr(self._pool, "get_max_size", None)
        if callable(get_max_size) and get_max_size() <= 1:
            raise RuntimeError("事件监听至少需要 2 个数据库连接")

        connection = await self._pool.acquire()
        added_channels: list[str] = []
        try:
            for channel in EVENT_CHANNELS:
                await connection.add_listener(channel, self._listener_callback)
                added_channels.append(channel)
        except BaseException:
            for channel in reversed(added_channels):
                try:
                    await connection.remove_listener(channel, self._listener_callback)
                except Exception:  # noqa: BLE001 - preserve the startup exception
                    logger.exception("移除 PostgreSQL 事件监听失败：%s", channel)
            await self._pool.release(connection)
            raise
        self._connection = connection

    async def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        for channel in EVENT_CHANNELS:
            try:
                await connection.remove_listener(channel, self._listener_callback)
            except Exception:  # noqa: BLE001 - release the shared connection regardless
                logger.exception("移除 PostgreSQL 事件监听失败：%s", channel)
        try:
            await self._pool.release(connection)
        except Exception:  # noqa: BLE001 - shutdown must remain best effort
            logger.exception("释放 PostgreSQL 事件监听连接失败")

    @asynccontextmanager
    async def subscribe(
        self, channel: str, run_id: UUID | str
    ) -> AsyncIterator[EventSubscription]:
        if channel not in EVENT_CHANNELS:
            raise ValueError(f"unsupported event channel: {channel}")
        key = (channel, str(run_id))
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._subscribers[key].add(queue)
        try:
            yield EventSubscription(queue)
        finally:
            subscribers = self._subscribers.get(key)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(key, None)

    def _on_notification(
        self,
        _connection: asyncpg.Connection,
        _pid: int,
        channel: str,
        payload: str,
    ) -> None:
        for queue in tuple(self._subscribers.get((channel, payload), ())):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                # One queued signal is enough: the next DB read consumes every
                # row after the stream cursor, so repeated writes can coalesce.
                pass
