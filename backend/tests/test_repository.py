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


@pytest.mark.asyncio
async def test_results_can_load_all_relationships_without_a_fixed_limit():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.results(uuid4(), None, 0, 0, relationship_limit=None)

    relationship_query = pool.queries[-2][0]
    assert "FROM relationships rel" in relationship_query
    assert "LIMIT $2" not in relationship_query


@pytest.mark.asyncio
async def test_claim_subdomain_run_uses_skip_locked_and_lease():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.claim_subdomain_run()
    query, _ = pool.queries[0]
    assert "FROM subdomain_runs" in query
    assert "FOR UPDATE SKIP LOCKED" in query
    assert "lease_id=gen_random_uuid()" in query


@pytest.mark.asyncio
async def test_subdomain_progress_requires_matching_lease():
    pool = FakePool("UPDATE 0")
    repo = Repository(pool, None)
    with pytest.raises(LeaseLost):
        await repo.update_subdomain_progress(
            uuid4(), 1, 2, 1, "resolving", lease_id=uuid4()
        )
    query, _ = pool.queries[0]
    assert "UPDATE subdomain_runs" in query
    assert "lease_id=$6" in query


@pytest.mark.asyncio
async def test_subdomain_source_cache_uses_unexpired_rows():
    class CachePool(FakePool):
        async def fetchval(self, query, *args):
            self.queries.append((query, args))
            return []

    pool = CachePool()
    repo = Repository(pool, None)
    await repo.get_subdomain_source_cache("example.com", "crt.sh")
    query, args = pool.queries[0]
    assert "expires_at > now()" in query
    assert args == ("example.com", "crt.sh")


@pytest.mark.asyncio
async def test_subdomain_result_upsert_merges_sources():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.add_subdomain_result(
        uuid4(),
        root_domain="example.com",
        hostname="www.example.com",
        ips=["93.184.216.34"],
        canonical_name="",
        wildcard=False,
        http_url="https://www.example.com/",
        http_status=200,
        title="Example",
        sources=["crt.sh"],
    )
    query, _ = pool.queries[0]
    assert "ON CONFLICT(run_id, root_domain, hostname) DO UPDATE" in query
    assert "subdomain_results.sources || EXCLUDED.sources" in query


@pytest.mark.asyncio
async def test_subdomain_results_after_skips_count_query():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.subdomain_results_after(uuid4(), 12, 500)
    assert len(pool.queries) == 1
    query, args = pool.queries[0]
    assert "id>$2 ORDER BY id LIMIT $3" in query
    assert args[1:] == (12, 500)


@pytest.mark.asyncio
async def test_subdomain_events_after_uses_monotonic_stream_cursor():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.subdomain_events_after(uuid4(), 12, 500)
    assert len(pool.queries) == 1
    query, args = pool.queries[0]
    assert "stream_seq>$2" in query
    assert "ORDER BY stream_seq" in query
    assert args[1:] == (12, 500)


@pytest.mark.asyncio
async def test_icp_company_cache_only_loads_fresh_complete_matching_version():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.get_icp_company_caches(["示例公司"], "cache-v1")

    query, args = pool.queries[0]
    assert "expires_at > now()" in query
    assert "complete=TRUE" in query
    assert "saved_total >= reported_total" in query
    assert "query_version=$2" in query
    assert args == (["示例公司"], "cache-v1")


@pytest.mark.asyncio
async def test_icp_company_cache_upsert_replaces_complete_snapshot():
    pool = FakePool()
    repo = Repository(pool, None)
    row = {"domain": "example.com", "serviceLicence": "京ICP备1号"}
    await repo.upsert_icp_company_cache("示例公司", [row], 1, "cache-v1", 3600)

    query, args = pool.queries[0]
    assert "ON CONFLICT(company_name) DO UPDATE" in query
    assert "complete=TRUE" in query
    assert "expires_at=EXCLUDED.expires_at" in query
    assert args[0] == "示例公司"
    assert args[2:] == (1, 1, "cache-v1", 3600)


@pytest.mark.asyncio
async def test_cancel_run_marks_active_work_cancelled_and_releases_lease():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.cancel_run(uuid4())

    query, _ = pool.queries[0]
    assert "status='cancelled'" in query
    assert "status IN ('queued','running')" in query
    assert "heartbeat_at=NULL" in query
    assert "lease_id=NULL" in query


@pytest.mark.asyncio
async def test_collection_event_queries_use_monotonic_stream_cursors():
    class EventPool(FakePool):
        async def fetchrow(self, query, *args):
            self.queries.append((query, args))
            return {"relationship_cursor": 12, "result_cursor": 34}

    pool = EventPool()
    repo = Repository(pool, None)
    cursors = await repo.collection_event_cursors(uuid4())
    assert cursors == (12, 34)
    assert "max(stream_seq)" in pool.queries[0][0]

    pool.queries.clear()
    await repo.collection_events_after(uuid4(), 12, 34, 500)
    relationship_query, relationship_args = pool.queries[0]
    result_query, result_args = pool.queries[1]
    assert "rel.stream_seq>$2" in relationship_query
    assert "ORDER BY rel.stream_seq" in relationship_query
    assert relationship_args[1:] == (12, 500)
    assert "r.stream_seq>$2" in result_query
    assert "r.category='icp'" in result_query
    assert result_args[1:] == (34, 500)


@pytest.mark.asyncio
async def test_cancel_subdomain_run_preserves_results_and_releases_lease():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.cancel_subdomain_run(uuid4())

    query, _ = pool.queries[0]
    assert "status='cancelled'" in query
    assert "status IN ('queued','running')" in query
    assert "lease_id=NULL" in query
    assert "DELETE" not in query


@pytest.mark.asyncio
async def test_subdomain_result_count_reads_persisted_rows():
    class CountPool(FakePool):
        async def fetchval(self, query, *args):
            self.queries.append((query, args))
            return 8

    pool = CountPool()
    repo = Repository(pool, None)
    assert await repo.subdomain_result_count(uuid4()) == 8
    assert "count(*) FROM subdomain_results" in pool.queries[0][0]
