from __future__ import annotations

from uuid import uuid4

import pytest

from app import miit


def icp_row(domain: str = "example.com", licence: str = "京ICP备1号") -> dict:
    return {
        "mainId": "main-1",
        "unitName": "示例公司",
        "mainLicence": "京ICP备1号",
        "serviceLicence": licence,
        "domain": domain,
        "natureName": "企业",
        "updateRecordTime": "2026-09-03",
    }


class CacheRepo:
    def __init__(self, caches=None):
        self.caches = caches or {}
        self.results = []
        self.stats = []
        self.cache_writes = []

    async def get_icp_company_caches(self, names, version):
        assert version == miit.ICP_CACHE_VERSION
        return {name: self.caches[name] for name in names if name in self.caches}

    async def add_icp_cache_stats(self, run_id, hits, live):
        self.stats.append((run_id, hits, live))

    async def upsert_entity(self, provider, external_id, name, payload):
        return uuid4()

    async def add_result(self, run_id, entity_id, category, payload, source_url, raw_payload):
        self.results.append((run_id, category, payload, raw_payload))

    async def upsert_icp_company_cache(self, name, rows, total, version, ttl_seconds):
        self.cache_writes.append((name, rows, total, version, ttl_seconds))

    async def get_runtime_config(self):
        return {}


@pytest.mark.asyncio
async def test_fresh_complete_cache_skips_live_request(monkeypatch):
    row = icp_row()
    repo = CacheRepo(
        {
            "示例公司": {
                "company_name": "示例公司",
                "rows": [row],
                "reported_total": 1,
                "saved_total": 1,
                "complete": True,
                "query_version": miit.ICP_CACHE_VERSION,
            }
        }
    )
    run_id = uuid4()

    async def should_not_fetch(*_args, **_kwargs):
        raise AssertionError("完整缓存命中后不应请求 ICP 接口")

    monkeypatch.setattr(miit, "_fetch_page", should_not_fetch)
    errors = await miit._collect_icp(repo, run_id, [" 示例公司 "])

    assert errors == []
    assert repo.stats == [(run_id, 1, 0)]
    assert len(repo.results) == 1
    assert repo.results[0][2]["domain"] == "example.com"
    assert repo.cache_writes == []


def test_cache_requires_exact_complete_deduplicated_snapshot():
    row = icp_row()
    base = {
        "rows": [row],
        "reported_total": 1,
        "saved_total": 1,
        "complete": True,
        "query_version": miit.ICP_CACHE_VERSION,
    }
    assert miit._validated_cached_rows(base, "示例公司") == [row]
    assert miit._validated_cached_rows({**base, "complete": False}, "示例公司") is None
    assert miit._validated_cached_rows({**base, "saved_total": 2}, "示例公司") is None
    assert miit._validated_cached_rows({**base, "reported_total": 2}, "示例公司") is None
    assert miit._validated_cached_rows(
        {**base, "query_version": "old-version"}, "示例公司"
    ) is None
    assert miit._validated_cached_rows(
        {**base, "rows": [row, dict(row)]}, "示例公司"
    ) is None


@pytest.mark.asyncio
async def test_invalid_cache_falls_back_to_live_and_replaces_it(monkeypatch):
    row = icp_row()
    repo = CacheRepo(
        {
            "示例公司": {
                "rows": [row],
                "reported_total": 2,
                "saved_total": 1,
                "complete": True,
                "query_version": miit.ICP_CACHE_VERSION,
            }
        }
    )
    calls = []

    async def fetch(_client, keyword, page, **_kwargs):
        calls.append((keyword, page))
        return {"rows": [row], "pages": 1, "total": 1}

    monkeypatch.setattr(miit, "_fetch_page", fetch)
    monkeypatch.setattr(miit.settings, "miit_api_url", "http://icp.test")
    errors = await miit._collect_icp(repo, uuid4(), ["示例公司"])

    assert errors == []
    assert calls == [("示例公司", 1)]
    assert repo.stats[0][1:] == (0, 1)
    assert len(repo.results) == 1
    assert repo.cache_writes[0][0:4] == (
        "示例公司",
        [row],
        1,
        miit.ICP_CACHE_VERSION,
    )


@pytest.mark.asyncio
async def test_zero_result_uses_shorter_cache_ttl(monkeypatch):
    repo = CacheRepo()

    async def fetch(_client, _keyword, _page, **_kwargs):
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "_fetch_page", fetch)
    monkeypatch.setattr(miit.settings, "miit_api_url", "http://icp.test")
    monkeypatch.setattr(miit.settings, "icp_cache_ttl_hours", 24)
    monkeypatch.setattr(miit.settings, "icp_zero_cache_ttl_hours", 6)
    errors = await miit._collect_icp(repo, uuid4(), ["无备案公司"])

    assert errors == []
    assert repo.results == []
    assert repo.cache_writes == [
        ("无备案公司", [], 0, miit.ICP_CACHE_VERSION, 6 * 3600)
    ]


@pytest.mark.asyncio
async def test_incomplete_result_is_never_cached():
    repo = CacheRepo()
    await miit._store_icp_cache(repo, "示例公司", [icp_row()], reported_total=2)
    assert repo.cache_writes == []


@pytest.mark.asyncio
async def test_company_round_reuses_slot_without_waiting_for_slowest_company():
    import asyncio

    release_slow = asyncio.Event()
    next_started = asyncio.Event()
    started = []

    async def collect(name):
        started.append(name)
        if name == "slow":
            await release_slow.wait()
        if name == "next":
            next_started.set()
        return None

    task = asyncio.create_task(
        miit._run_company_round(["slow", "fast", "next"], 2, collect)
    )
    await asyncio.wait_for(next_started.wait(), timeout=1)
    assert started == ["slow", "fast", "next"]
    assert not task.done()
    release_slow.set()
    assert await task == [("slow", None), ("fast", None), ("next", None)]
