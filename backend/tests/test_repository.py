from uuid import uuid4

import pytest

from app.repository import LeaseLost, Repository


class FakePool:
    def __init__(self, execute_result="UPDATE 1"):
        self.queries = []
        self.execute_result = execute_result

    async def execute(self, query, *args):
        self.queries.append((query, args))
        return self.execute_result

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        return {"id": uuid4()}

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return []

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        return 0


@pytest.mark.asyncio
async def test_recover_stale_uses_lease_cutoff():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.recover_stale(120)
    query, args = pool.queries[0]
    assert "status='running'" in query
    assert "heartbeat_at" in query
    assert "lease_id=NULL" in query
    assert args == (120,)


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_and_lease():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.claim_run(120)
    query, _ = pool.queries[0]
    assert "FOR UPDATE SKIP LOCKED" in query
    assert "status='queued'" in query
    assert "lease_id=gen_random_uuid()" in query


@pytest.mark.asyncio
async def test_heartbeat_requires_matching_lease():
    pool = FakePool("UPDATE 0")
    repo = Repository(pool, None)
    with pytest.raises(LeaseLost):
        await repo.heartbeat(uuid4(), 3, lease_id=uuid4())
    query, _ = pool.queries[0]
    assert "lease_id=$4" in query
    assert "status='running'" in query


@pytest.mark.asyncio
async def test_entity_upsert_does_not_overwrite_name():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.upsert_entity("tianyancha-anonymous", "1", "old", {"id": "1"})
    query, _ = pool.queries[0]
    assert "DO UPDATE SET name = entities.name" in query


@pytest.mark.asyncio
async def test_touch_run_refreshes_lease_without_changing_progress():
    pool = FakePool()
    repo = Repository(pool, None)
    lease_id = uuid4()

    await repo.touch_run(uuid4(), lease_id=lease_id)

    query, args = pool.queries[0]
    assert "SET heartbeat_at=now()" in query
    assert "progress" not in query
    assert "lease_id=$2" in query
    assert args[1] == lease_id
