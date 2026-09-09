from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.event_hub import (
    COLLECTION_EVENTS_CHANNEL,
    EVENT_CHANNELS,
    SUBDOMAIN_EVENTS_CHANNEL,
    PostgresEventHub,
)


class FakeConnection:
    def __init__(self) -> None:
        self.listeners = {}
        self.added = []
        self.removed = []

    async def add_listener(self, channel, callback):
        self.added.append((channel, callback))
        self.listeners[channel] = callback

    async def remove_listener(self, channel, callback):
        self.removed.append((channel, callback))
        if self.listeners.get(channel) == callback:
            self.listeners.pop(channel)


class FakePool:
    def __init__(self, max_size: int = 2) -> None:
        self.max_size = max_size
        self.connection = FakeConnection()
        self.acquire_count = 0
        self.released = []

    def get_max_size(self):
        return self.max_size

    async def acquire(self):
        self.acquire_count += 1
        return self.connection

    async def release(self, connection):
        self.released.append(connection)


@pytest.mark.asyncio
async def test_event_hub_uses_one_shared_listener_connection():
    pool = FakePool()
    hub = PostgresEventHub(pool)

    await hub.start()
    await hub.start()

    assert hub.started
    assert pool.acquire_count == 1
    assert [channel for channel, _callback in pool.connection.added] == list(EVENT_CHANNELS)

    await hub.close()
    await hub.close()

    assert not hub.started
    assert [channel for channel, _callback in pool.connection.removed] == list(EVENT_CHANNELS)
    assert pool.released == [pool.connection]


@pytest.mark.asyncio
async def test_event_hub_routes_and_coalesces_run_notifications():
    pool = FakePool()
    hub = PostgresEventHub(pool)
    await hub.start()
    run_id = uuid4()
    other_run_id = uuid4()

    async with hub.subscribe(COLLECTION_EVENTS_CHANNEL, run_id) as subscription:
        callback = pool.connection.listeners[COLLECTION_EVENTS_CHANNEL]
        callback(pool.connection, 1, COLLECTION_EVENTS_CHANNEL, str(other_run_id))
        assert not await subscription.wait(0)

        callback(pool.connection, 1, COLLECTION_EVENTS_CHANNEL, str(run_id))
        callback(pool.connection, 1, COLLECTION_EVENTS_CHANNEL, str(run_id))
        assert await subscription.wait(0)
        assert not await subscription.wait(0)

    callback(pool.connection, 1, COLLECTION_EVENTS_CHANNEL, str(run_id))
    assert (COLLECTION_EVENTS_CHANNEL, str(run_id)) not in hub._subscribers
    await hub.close()


@pytest.mark.asyncio
async def test_event_hub_wakes_every_subscriber_for_same_run():
    pool = FakePool()
    hub = PostgresEventHub(pool)
    await hub.start()
    run_id = uuid4()

    async with hub.subscribe(SUBDOMAIN_EVENTS_CHANNEL, run_id) as first:
        async with hub.subscribe(SUBDOMAIN_EVENTS_CHANNEL, run_id) as second:
            callback = pool.connection.listeners[SUBDOMAIN_EVENTS_CHANNEL]
            callback(pool.connection, 1, SUBDOMAIN_EVENTS_CHANNEL, str(run_id))
            assert await first.wait(0)
            assert await second.wait(0)

    await hub.close()


@pytest.mark.asyncio
async def test_event_hub_does_not_exhaust_single_connection_pool():
    pool = FakePool(max_size=1)
    hub = PostgresEventHub(pool)

    with pytest.raises(RuntimeError, match="至少需要 2 个"):
        await hub.start()

    assert pool.acquire_count == 0
    assert not hub.started


def test_event_notification_migration_covers_visible_changes_only():
    sql = (
        Path(__file__).parents[1] / "migrations" / "032_event_notifications.sql"
    ).read_text()

    assert "pg_notify(TG_ARGV[0], event_run_id)" in sql
    assert "AFTER INSERT ON relationships" in sql
    assert "REFERENCING NEW TABLE AS event_rows" in sql
    assert "notify_asset_workbench_result_events('collection_events', 'icp')" in sql
    assert "AFTER INSERT ON subdomain_results" in sql
    assert "AFTER UPDATE ON subdomain_results" in sql
    assert "collection_runs_delete_event_notify" in sql
    assert "subdomain_runs_delete_event_notify" in sql
    assert "heartbeat_at" not in sql
    assert sql.count("'collection_events'") == 4
    assert sql.count("'subdomain_events'") == 4
