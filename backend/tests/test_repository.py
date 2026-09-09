from uuid import uuid4

import pytest

from app.repository import MIGRATION_LOCK_ID, LeaseLost, Repository


class FakePool:
    def __init__(self, execute_result="UPDATE 1"):
        self.queries = []
        self.execute_result = execute_result

    async def execute(self, query, *args):
        self.queries.append((query, args))
        return self.execute_result

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if "INSERT INTO entities" in query:
            return {"id": uuid4(), "provider": args[0] if args else "", "external_id": args[1] if len(args) > 1 else ""}
        return {"id": uuid4()}

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        if "INSERT INTO entities" in query and len(args) >= 2:
            providers, external_ids = args[0], args[1]
            return [
                {"provider": provider, "external_id": external_id, "id": uuid4()}
                for provider, external_id in zip(providers, external_ids)
            ]
        return []

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        return 0


@pytest.mark.asyncio
async def test_migrate_takes_advisory_lock_before_schema_changes(tmp_path):
    migration = tmp_path / "001_test.sql"
    migration.write_text("SELECT 1;")

    class AsyncContext:
        def __init__(self, value=None):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Connection:
        def __init__(self):
            self.queries = []

        def transaction(self):
            return AsyncContext()

        async def execute(self, query, *args):
            self.queries.append(("execute", query, args))
            return "SELECT 1"

        async def fetch(self, query, *args):
            self.queries.append(("fetch", query, args))
            return []

    connection = Connection()

    class MigrationPool:
        def acquire(self):
            return AsyncContext(connection)

    await Repository(MigrationPool(), tmp_path).migrate()

    assert connection.queries[0] == (
        "execute",
        "SELECT pg_advisory_xact_lock($1)",
        (MIGRATION_LOCK_ID,),
    )
    assert any(query == "SELECT 1;" for _, query, _ in connection.queries)


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
async def test_upsert_entities_batches_unique_rows():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.upsert_entities([
        ("tianyancha", "1", "A", {"id": "1"}),
        ("tianyancha", "2", "B", {"id": "2"}),
        ("tianyancha", "1", "A-dup", {"id": "1"}),
    ])
    query, args = pool.queries[0]
    assert "UNNEST" in query
    assert args[1] == ["1", "2"]


@pytest.mark.asyncio
async def test_add_results_batches_rows():
    pool = FakePool()
    repo = Repository(pool, None)
    run_id = uuid4()
    entity_a, entity_b = uuid4(), uuid4()
    await repo.add_results([
        (run_id, entity_a, "icp", {"domain": "a.com"}, "https://example", {"raw": 1}),
        (run_id, entity_b, "icp", {"domain": "b.com"}, "https://example", {"raw": 2}),
    ])
    query, args = pool.queries[0]
    assert "INSERT INTO results" in query
    assert "UNNEST" in query
    assert args[2] == ["icp", "icp"]


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
async def test_runtime_config_loads_subdomain_api_settings():
    class ConfigPool(FakePool):
        async def fetch(self, query, *args):
            self.queries.append((query, args))
            if "FROM provider_sessions" in query:
                return []
            if "FROM manual_proxy_nodes" in query:
                return []
            return []

        async def fetchrow(self, query, *args):
            self.queries.append((query, args))
            if "FROM serverless_proxy_settings" in query:
                return {"proxy_pool": "cloud"}
            if "FROM subdomain_api_settings" in query:
                return {"fofa_email": "user@example.com", "fofa_key": "k", "hunter_key": "h"}
            return {"id": uuid4()}

    repo = Repository(ConfigPool(), None)
    config = await repo.get_runtime_config()
    assert config["subdomain_api"]["fofa_email"] == "user@example.com"
    assert config["subdomain_api"]["hunter_key"] == "h"


@pytest.mark.asyncio
async def test_subdomain_result_upsert_merges_sources():
    pool = FakePool()
    repo = Repository(pool, None)
    run_id = uuid4()
    await repo.add_subdomain_results(run_id, [
        {
            "root_domain": "example.com",
            "hostname": "www.example.com",
            "ips": ["93.184.216.34"],
            "canonical_name": "",
            "wildcard": False,
            "http_url": "",
            "http_status": None,
            "title": "",
            "sources": ["crt.sh"],
        },
        {
            "root_domain": "example.com",
            "hostname": "api.example.com",
            "ips": ["93.184.216.34"],
            "canonical_name": "",
            "wildcard": False,
            "http_url": "https://api.example.com/",
            "http_status": 200,
            "title": "API",
            "sources": ["DNS字典"],
        },
    ])
    query, args = pool.queries[0]
    assert "INSERT INTO subdomain_results" in query
    assert "UNNEST" in query
    assert "ON CONFLICT(run_id, root_domain, hostname) DO UPDATE" in query
    assert args[2] == ["www.example.com", "api.example.com"]


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
    assert "id>$2" in query
    assert "ORDER BY id LIMIT $3" in query
    assert args[1:] == (12, 500, "", "all")


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


@pytest.mark.asyncio
async def test_subdomain_results_filters_and_returns_matching_counts():
    class ResultsPool(FakePool):
        async def fetchrow(self, query, *args):
            self.queries.append((query, args))
            if "filtered_count" in query:
                return {
                    "all_count": 7,
                    "web_count": 3,
                    "wildcard_count": 2,
                    "filtered_count": 2,
                }
            return {"id": uuid4()}

    run_id = uuid4()
    pool = ResultsPool()
    repo = Repository(pool, None)

    rows, total, counts = await repo.subdomain_results(
        run_id, 20, 0, None, "api", "web"
    )

    assert rows == []
    assert total == 2
    assert counts == {"all": 7, "web": 3, "wildcard": 2}
    rows_query, rows_args = pool.queries[0]
    assert "hostname ILIKE" in rows_query
    assert "http_status IS NOT NULL" in rows_query
    assert rows_args == (run_id, 20, 0, "api", "web")
    counts_query, counts_args = pool.queries[1]
    assert "count(*) FILTER" in counts_query
    assert "filtered_count" in counts_query
    assert counts_args == (run_id, "api", "web")



@pytest.mark.asyncio
async def test_list_history_unions_collection_and_subdomain_runs():
    pool = FakePool()
    repo = Repository(pool, None)
    await repo.list_history(20, 0, "tobacco", "succeeded", "")
    query, args = pool.queries[0]
    assert "UNION ALL" in query
    assert "collection_runs" in query
    assert "subdomain_runs" in query
    assert args == (20, 0, "tobacco", "succeeded", "")


@pytest.mark.asyncio
async def test_delete_history_splits_collection_and_subdomain_ids():
    from uuid import uuid4
    pool = FakePool()
    repo = Repository(pool, None)
    collection_id = uuid4()
    subdomain_id = uuid4()
    await repo.delete_history([
        ("collection", collection_id),
        ("subdomain", subdomain_id),
    ])
    queries = "\n".join(query for query, _ in pool.queries)
    assert "DELETE FROM collection_runs" in queries
    assert "DELETE FROM subdomain_runs" in queries



@pytest.mark.asyncio
async def test_all_collection_rows_returns_complete_snapshot():
    run_id = uuid4()
    pool = FakePool()
    repo = Repository(pool, None)
    rels, icp_rows = await repo.all_collection_rows(run_id)
    queries = [query for query, _args in pool.queries]
    assert any("FROM relationships rel" in query for query in queries)
    assert any("r.category='icp'" in query.replace(" ", "") or "r.category='icp'" in query for query in queries)
    assert not any("LIMIT" in query and "OFFSET" in query for query in queries)
    assert rels == []
    assert icp_rows == []
