import asyncio
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app.collector import RunSpec, _prioritize_root_name, collect_run
from app.models import CollectionRequest
from app.providers.tianyancha import AnonymousTianyancha, Company, Investment, ProviderError, Shareholder


async def _no_icp(*_args, **_kwargs):
    return []


def test_icp_prioritizes_requested_root_company():
    assert _prioritize_root_name(
        ["北京公司", "小米科技有限责任公司", "成都公司", "北京公司"],
        "小米科技有限责任公司",
    ) == ["小米科技有限责任公司", "北京公司", "成都公司"]


def test_request_validation_and_defaults():
    request = CollectionRequest(keyword="  小米   科技  ")
    assert request.keyword == "小米 科技"
    assert request.depth == 1
    assert request.holding_percent == Decimal("100")
    assert request.fields == ["invest"]
    assert request.providers == ["tianyancha"]


def test_request_rejects_unsupported_branches_and_bad_range():
    with pytest.raises(ValueError):
        CollectionRequest(keyword="公司", include_branches=True)
    with pytest.raises(ValueError):
        CollectionRequest(keyword="公司", depth=6)
    with pytest.raises(ValueError):
        CollectionRequest(keyword="公司", holding_percent=101)
    with pytest.raises(ValueError):
        CollectionRequest(keyword="公司", holding_percent=Decimal("51.999"))


def test_provider_never_sends_credentials():
    provider = AnonymousTianyancha("https://example.test")
    headers = provider.client.headers
    assert "cookie" not in headers
    assert "x-tycid" not in headers
    assert "x-auth-token" not in headers
    assert headers.get("version") == "TYC-Web"
    assert provider.client._trust_env is False
    asyncio.run(provider.close())


def test_holding_filter_and_depth_with_fake_provider(monkeypatch):
    class FakeProvider:
        def __init__(self):
            self.calls = []

        async def search(self, keyword):
            root = Company("1", keyword, {"id": "1", "name": keyword})
            return root, [root]

        async def all_pages(self, fetch):
            rows, _ = await fetch(1)
            return rows

        async def investments(self, external_id, page=1):
            self.calls.append(("invest", external_id))
            if external_id == "1":
                return [
                    Investment("kept", "2", 80, {"id": "2", "name": "kept", "percent": 80}),
                    Investment("filtered", "3", 20, {"id": "3", "name": "filtered", "percent": 20}),
                    Investment("unknown", "5", None, {"id": "5", "name": "unknown"}),
                ], 2
            return [Investment("grandchild", "4", 90, {"id": "4", "name": "grandchild", "percent": 90})], 1

        async def shareholders(self, external_id, page=1):
            self.calls.append(("partner", external_id))
            return [Shareholder("owner", 100, {"name": "owner"})], 1

        @property
        def client(self):
            return httpx.AsyncClient(base_url="https://example.test")

    class FakeRepo:
        def __init__(self):
            self.entities = {}
            self.relationships = []
            self.results = []

        async def upsert_entity(self, provider, external_id, name, payload):
            self.entities.setdefault(external_id, uuid4())
            return self.entities[external_id]

        async def add_result(self, *args):
            self.results.append(args)

        async def add_relationship(self, *args):
            self.relationships.append(args)

        async def heartbeat(self, *args, **kwargs):
            pass

        async def has_results(self, run_id):
            return bool(self.results or self.relationships)

        async def entity_names_for_run(self, run_id):
            names = set()
            for item in self.results:
                payload = item[3] if len(item) > 3 else {}
                if isinstance(payload, dict):
                    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
                    names.add(selected.get("name") or payload.get("name") or "")
            for item in self.relationships:
                pass
            return [name for name in names if name]

    provider, repo = FakeProvider(), FakeRepo()
    import app.collector as collector
    monkeypatch.setattr(collector, "collect_icp", _no_icp)
    monkeypatch.setattr(collector, "collect_icp_from_queue", _no_icp)
    asyncio.run(collect_run(repo, [provider], RunSpec(uuid4(), "root", 2, 51, ["invest"])))
    assert ("partner", "1") not in provider.calls
    assert {item[5] for item in repo.relationships} == {1, 2}
    assert len(repo.relationships) == 2
    invest_payloads = [item[3] for item in repo.results if item[2] == "invest"]
    names = {payload["name"] for payload in invest_payloads}
    assert "filtered" not in names
    assert "unknown" not in names
    assert "kept" in names



@pytest.mark.asyncio
async def test_investment_traversal_visits_siblings_concurrently(monkeypatch):
    import app.collector as collector

    monkeypatch.setattr(collector, "collect_icp", _no_icp)
    monkeypatch.setattr(collector, "collect_icp_from_queue", _no_icp)
    monkeypatch.setattr(collector, "INVEST_CONCURRENCY", 4)
    started = {"2": asyncio.Event(), "3": asyncio.Event()}
    overlap = False

    class FakeProvider:
        id = "tianyancha"
        label = "天眼查"
        failed_company_retries = 0

        def __init__(self):
            self.client = type("Client", (), {"base_url": "https://example.test"})()

        async def search(self, keyword):
            root = Company("1", keyword, {"id": "1", "name": keyword})
            return root, [root]

        async def all_pages(self, fetch):
            rows, _ = await fetch(1)
            return rows

        async def investments(self, external_id, page=1):
            nonlocal overlap
            if external_id == "1":
                return [
                    Investment("一级企业A", "2", 100, {"id": "2", "name": "一级企业A", "percent": 100}),
                    Investment("一级企业B", "3", 100, {"id": "3", "name": "一级企业B", "percent": 100}),
                ], 2
            started[external_id].set()
            other = "3" if external_id == "2" else "2"
            await asyncio.wait_for(started[other].wait(), timeout=1)
            overlap = True
            return [], 0

    class FakeRepo:
        def __init__(self):
            self.entities = {}
            self.relationships = []
            self.results = []

        async def upsert_entity(self, provider, external_id, name, payload):
            self.entities.setdefault(external_id, uuid4())
            return self.entities[external_id]

        async def add_result(self, *args):
            self.results.append(args)

        async def add_relationship(self, *args):
            self.relationships.append(args)

        async def heartbeat(self, *args, **kwargs):
            pass

        async def has_results(self, run_id):
            return bool(self.results or self.relationships)

        async def entity_names_for_run(self, run_id):
            return ["root", "一级企业A", "一级企业B"]

    errors = await collect_run(
        FakeRepo(), [FakeProvider()], RunSpec(uuid4(), "root", 2, 100, ["invest"])
    )
    assert errors == []
    assert overlap is True


def test_failed_child_company_is_retried_without_requerying_parent(monkeypatch):
    import app.collector as collector

    monkeypatch.setattr(collector, "collect_icp", _no_icp)
    monkeypatch.setattr(collector, "collect_icp_from_queue", _no_icp)

    class FakeProvider:
        id = "tianyancha"
        label = "天眼查"
        failed_company_retries = 1

        def __init__(self):
            self.calls = []
            self.resets = 0
            self.client = type("Client", (), {"base_url": "https://example.test"})()

        async def search(self, keyword):
            root = Company("1", keyword, {"id": "1", "name": keyword})
            return root, [root]

        async def all_pages(self, fetch):
            rows, _ = await fetch(1)
            return rows

        async def investments(self, external_id, page=1):
            self.calls.append(external_id)
            if external_id == "1":
                return [
                    Investment("一级企业A", "2", 100, {"id": "2", "name": "一级企业A", "percent": 100}),
                    Investment("一级企业B", "3", 100, {"id": "3", "name": "一级企业B", "percent": 100}),
                ], 2
            if external_id == "2" and self.calls.count("2") == 1:
                raise ProviderError("请登录以使用完整功能")
            if external_id == "2":
                return [
                    Investment("二级企业A", "4", 100, {"id": "4", "name": "二级企业A", "percent": 100}),
                ], 1
            return [], 0

        async def reset_after_failure(self):
            self.resets += 1

    class FakeRepo:
        def __init__(self):
            self.entities = {}
            self.relationships = []
            self.results = []

        async def upsert_entity(self, provider, external_id, name, payload):
            self.entities.setdefault(external_id, uuid4())
            return self.entities[external_id]

        async def add_result(self, *args):
            self.results.append(args)

        async def add_relationship(self, *args):
            self.relationships.append(args)

        async def heartbeat(self, *args, **kwargs):
            pass

        async def has_results(self, run_id):
            return bool(self.results or self.relationships)

        async def entity_names_for_run(self, run_id):
            return ["根企业", "一级企业A", "一级企业B", "二级企业A"]

    provider = FakeProvider()
    repo = FakeRepo()
    errors = asyncio.run(
        collect_run(repo, [provider], RunSpec(uuid4(), "根企业", 2, 100, ["invest"]))
    )

    assert errors == []
    assert provider.calls[0] == "1"
    assert provider.calls.count("1") == 1
    assert provider.calls.count("2") == 2
    assert provider.calls.count("3") == 1
    assert set(provider.calls) == {"1", "2", "3"}
    assert provider.resets == 1
    assert len(repo.relationships) == 3
    assert {item[2] for item in repo.relationships} == {
        repo.entities["2"], repo.entities["3"], repo.entities["4"]
    }

@pytest.mark.asyncio
async def test_icp_starts_while_investment_traversal_is_running(monkeypatch):
    import app.collector as collector

    investment_started = asyncio.Event()
    icp_started = asyncio.Event()
    observed_overlap = False
    icp_names = []

    async def fake_icp(_repo, _run_id, names):
        if hasattr(names, "get"):
            while True:
                item = await names.get()
                if item is None:
                    break
                icp_names.append(item)
                icp_started.set()
        else:
            icp_names.extend(names)
            icp_started.set()
        return []

    monkeypatch.setattr(collector, "collect_icp", fake_icp)
    monkeypatch.setattr(collector, "collect_icp_from_queue", fake_icp)
    monkeypatch.setattr(collector, "ICP_STREAM_MIN_START", 1)

    class FakeProvider:
        id = "tianyancha"
        label = "天眼查"

        async def search(self, keyword):
            root = Company("root", keyword, {"id": "root", "name": keyword})
            return root, [root]

        async def all_pages(self, fetch):
            rows, _ = await fetch(1)
            return rows

        async def investments(self, external_id, page=1):
            nonlocal observed_overlap
            investment_started.set()
            await asyncio.wait_for(icp_started.wait(), timeout=1)
            observed_overlap = True
            return [], 0

        @property
        def client(self):
            return httpx.AsyncClient(base_url="https://example.test")

    class FakeRepo:
        def __init__(self):
            self.results = []

        async def upsert_entity(self, provider, external_id, name, payload):
            return uuid4()

        async def add_result(self, *args):
            self.results.append(args)

        async def add_relationship(self, *args):
            pass

        async def heartbeat(self, *args, **kwargs):
            pass

        async def has_results(self, run_id):
            return bool(self.results)

        async def entity_names_for_run(self, run_id):
            return ["根企业"] if self.results else []

    errors = await asyncio.wait_for(
        collector.collect_run(
            FakeRepo(),
            [FakeProvider()],
            RunSpec(uuid4(), "根企业", 1, 51, ["invest"]),
        ),
        timeout=2,
    )

    assert errors == []
    assert observed_overlap is True
    assert icp_names == ["根企业"]


def test_collect_run_queries_providers_in_parallel(monkeypatch):
    import time
    import app.collector as collector

    monkeypatch.setattr(collector, "collect_icp", _no_icp)
    monkeypatch.setattr(collector, "collect_icp_from_queue", _no_icp)

    class SlowProvider:
        def __init__(self, provider_id, delay):
            self.id = provider_id
            self.label = provider_id
            self.delay = delay
            self.started = None
            self.finished = None

        async def search(self, keyword):
            self.started = time.perf_counter()
            await asyncio.sleep(self.delay)
            self.finished = time.perf_counter()
            root = Company(provider_id := self.id, keyword, {"id": provider_id, "name": keyword})
            return root, [root]

        async def all_pages(self, fetch):
            rows, _ = await fetch(1)
            return rows

        async def investments(self, external_id, page=1):
            return [], 0

        @property
        def client(self):
            return httpx.AsyncClient(base_url="https://example.test")

    class FakeRepo:
        def __init__(self):
            self.results = []
            self.relationships = []

        async def upsert_entity(self, provider, external_id, name, payload):
            return uuid4()

        async def add_result(self, *args):
            self.results.append(args)

        async def add_relationship(self, *args):
            self.relationships.append(args)

        async def heartbeat(self, *args, **kwargs):
            pass

        async def has_results(self, run_id):
            return True

        async def entity_names_for_run(self, run_id):
            return ["root"]

    first, second = SlowProvider("tianyancha", 0.25), SlowProvider("kuaicha", 0.25)
    started = time.perf_counter()
    asyncio.run(collect_run(FakeRepo(), [first, second], RunSpec(uuid4(), "root", 1, 100, ["invest"])))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.45
    assert first.started is not None and second.started is not None
    assert abs(first.started - second.started) < 0.1


@pytest.mark.asyncio
async def test_icp_keepalive_refreshes_run_lease(monkeypatch):
    import app.collector as collector

    touches = []

    class Repo:
        async def touch_run(self, run_id, lease_id=None):
            touches.append((run_id, lease_id))

    async def slow_icp(_repo, _run_id, names):
        if hasattr(names, "get"):
            while True:
                item = await names.get()
                if item is None:
                    break
        await asyncio.sleep(0.035)
        return []

    monkeypatch.setattr(collector, "ICP_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(collector, "collect_icp", slow_icp)
    monkeypatch.setattr(collector, "collect_icp_from_queue", slow_icp)
    spec = RunSpec(uuid4(), "root", 1, 100, ["invest"], lease_id=uuid4())

    assert await collector._collect_icp_with_heartbeat(Repo(), spec, ["root"]) == []
    assert touches
    assert all(item == (spec.id, spec.lease_id) for item in touches)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 500, 502, 521])
async def test_icp_direct_page_status_errors(status):
    import app.miit as miit

    class Client:
        async def get(self, *_args, **_kwargs):
            return httpx.Response(status)

    with pytest.raises(miit.IcpPageError, match=f"HTTP {status}"):
        await miit._fetch_page(Client(), "测试企业", 1)


@pytest.mark.asyncio
async def test_icp_direct_page_sets_timeout_and_omits_proxy_parameter():
    import app.miit as miit

    calls = []

    class Client:
        async def get(self, *args, **kwargs):
            calls.append((args, kwargs))
            return httpx.Response(200, json={"code": 200, "params": {"list": [], "pages": 1}})

    result = await miit._fetch_page(
        Client(), "测试企业", 2, 0.5, session_key="same-company"
    )

    assert result == {"rows": [], "pages": 1, "total": None}
    assert calls[0][1]["params"] == {
        "search": "测试企业",
        "pageNum": 2,
        "pageSize": 26,
        "sessionKey": "same-company",
    }
    assert "proxy" not in calls[0][1]["params"]
    timeout = calls[0][1]["timeout"]
    assert timeout.connect == 0.5
    assert timeout.read == 0.5


@pytest.mark.asyncio
async def test_icp_direct_page_hard_timeout(monkeypatch):
    import app.miit as miit

    class HangingClient:
        async def get(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    with pytest.raises(miit.IcpPageError, match="硬超时"):
        await miit._fetch_page(HangingClient(), "测试企业", 1, 0.01)


@pytest.mark.asyncio
async def test_icp_direct_company_total_timeout_has_details(monkeypatch):
    import app.miit as miit

    class Repo:
        pass

    async def hangs(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(miit, "ICP_COMPANY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(miit, "ICP_COMPANY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(miit, "_fetch_page", hangs)

    errors = await miit.collect_icp(Repo(), uuid4(), ["测试企业"])

    assert len(errors) == 1
    assert "请求页面 1 次" in errors[0]
    assert "直连查询总预算达到 0.01 秒" in errors[0]
    assert "实际耗时：" in errors[0]


@pytest.mark.asyncio
async def test_icp_direct_multi_page_saves_each_page_without_proxy_lookup(monkeypatch):
    import app.miit as miit

    fetch_calls = []
    saved_pages = []

    class Repo:
        async def next_query_proxy(self, *_args, **_kwargs):
            raise AssertionError("直连模式不应读取代理池")

    async def fake_fetch(
        _client, keyword, page, timeout_seconds=10, session_key=""
    ):
        fetch_calls.append((keyword, page, timeout_seconds, session_key))
        return {
            "rows": [{"domain": f"{page}.example", "serviceLicence": str(page)}],
            "pages": 2,
        }

    async def fake_save(_repo, _run_id, _name, page_rows, _seen):
        saved_pages.append(page_rows)

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)
    monkeypatch.setattr(miit, "_save_icp_page", fake_save)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    page_timeout = miit.ICP_PAGE_TIMEOUT_SECONDS
    assert [(item[0], item[1], item[2]) for item in fetch_calls] == [
        ("测试企业", 1, page_timeout),
        ("测试企业", 2, page_timeout),
    ]
    assert fetch_calls[0][3]
    assert fetch_calls[0][3] == fetch_calls[1][3]
    assert len(saved_pages) == 2


@pytest.mark.asyncio
async def test_icp_direct_failure_retries_company_until_exhausted(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def next_query_proxy(self, *_args, **_kwargs):
            raise AssertionError("直连模式不应读取代理池")

        async def update_query_proxy_probe(self, *_args, **_kwargs):
            raise AssertionError("直连失败不应更新代理状态")

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, session_key=""
    ):
        calls.append((page, timeout_seconds, session_key))
        raise miit.IcpPageError("HTTP 521：上游 Web 服务不可用")

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_COMPANY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(miit, "ICP_COMPANY_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    errors = await miit.collect_icp(Repo(), uuid4(), ["测试企业"])
    assert len(errors) == 1
    assert "HTTP 521" in errors[0]
    assert "企业尝试 3 次" in errors[0]
    assert "请求页面 3 次" in errors[0]
    assert "企业级自动重试 2 次后仍失败" in errors[0]
    assert len(calls) == 3
    assert all(call[0:2] == (1, miit.ICP_PAGE_TIMEOUT_SECONDS) for call in calls)
    assert len({call[2] for call in calls}) == 3


@pytest.mark.asyncio
async def test_icp_direct_failure_recovers_on_later_company_attempt(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        pass

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, session_key=""
    ):
        calls.append((page, timeout_seconds, session_key))
        if len(calls) < 3:
            raise miit.IcpPageError("HTTP 521：上游 Web 服务不可用")
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_COMPANY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(miit, "ICP_COMPANY_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert len(calls) == 3
    assert len({call[2] for call in calls}) == 3


@pytest.mark.asyncio
async def test_icp_company_retry_only_requeues_failed_names(monkeypatch):
    import app.miit as miit

    calls = []
    counts = {"失败企业": 0, "成功企业": 0}

    class Repo:
        pass

    async def fake_fetch(
        _client, keyword, page, timeout_seconds=10, session_key=""
    ):
        calls.append((keyword, page, session_key))
        counts[keyword] += 1
        if keyword == "失败企业" and counts[keyword] == 1:
            raise miit.IcpPageError("HTTP 521：上游 Web 服务不可用")
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 2)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_DIRECT_REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_COMPANY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(miit, "ICP_COMPANY_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(
        Repo(), uuid4(), ["失败企业", "成功企业"]
    ) == []
    assert counts == {"失败企业": 2, "成功企业": 1}
    failed_sessions = {
        session for keyword, _page, session in calls if keyword == "失败企业"
    }
    assert len(failed_sessions) == 2


@pytest.mark.asyncio
async def test_icp_retries_an_incomplete_pagination_with_a_new_affinity_session(monkeypatch):
    import app.miit as miit

    calls = []
    saved = []

    class Repo:
        pass

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, session_key=""
    ):
        calls.append((session_key, page))
        pass_index = len({key for key, _page in calls})
        if pass_index == 1:
            rows = {
                1: [("a.example", "A"), ("b.example", "B")],
                2: [("a.example", "A"), ("b.example", "B")],
            }[page]
        else:
            rows = {
                1: [("a.example", "A"), ("b.example", "B")],
                2: [("c.example", "C"), ("d.example", "D")],
            }[page]
        return {
            "rows": [
                {"domain": domain, "serviceLicence": licence}
                for domain, licence in rows
            ],
            "pages": 2,
            "total": 4,
        }

    async def fake_save(_repo, _run_id, _name, rows, seen):
        for row in rows:
            key = (row["domain"], row["serviceLicence"])
            if key not in seen:
                seen.add(key)
                saved.append(key)

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_DIRECT_REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)
    monkeypatch.setattr(miit, "_save_icp_page", fake_save)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert len({key for key, _page in calls}) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[2][0] == calls[3][0]
    assert calls[0][0] != calls[2][0]
    assert saved == [
        ("a.example", "A"),
        ("b.example", "B"),
        ("c.example", "C"),
        ("d.example", "D"),
    ]


@pytest.mark.asyncio
async def test_icp_incomplete_pagination_is_reported_and_not_saved(monkeypatch):
    import app.miit as miit

    calls = []
    saved = []

    class Repo:
        pass

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, session_key=""
    ):
        calls.append((session_key, page))
        return {
            "rows": [{"domain": "same.example", "serviceLicence": "A"}],
            "pages": 2,
            "total": 4,
        }

    async def fake_save(*args, **kwargs):
        saved.append((args, kwargs))

    monkeypatch.setattr(miit, "ICP_PAGINATION_RECOVERY_PASSES", 1)
    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_COMPANY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_DIRECT_REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)
    monkeypatch.setattr(miit, "_save_icp_page", fake_save)

    errors = await miit.collect_icp(Repo(), uuid4(), ["测试企业"])

    assert len(errors) == 1
    assert "上游报告 4 条" in errors[0]
    assert "仅获取 1 条" in errors[0]
    assert "结果不完整" in errors[0]
    assert len({key for key, _page in calls}) == 2
    assert saved == []


@pytest.mark.asyncio
async def test_icp_accepts_valid_record_without_domain(monkeypatch):
    import app.miit as miit

    saved = []

    class Repo:
        pass

    async def fake_fetch(
        _client, _keyword, _page, timeout_seconds=10, session_key=""
    ):
        return {
            "rows": [{
                "domain": "",
                "serviceLicence": "",
                "mainId": 250000032407,
                "mainLicence": "滇ICP备17004651号",
                "unitName": "云南省烟草公司楚雄州公司",
            }],
            "pages": 1,
            "total": 1,
        }

    async def fake_save(_repo, _run_id, _name, rows, _seen):
        saved.extend(rows)

    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)
    monkeypatch.setattr(miit, "_save_icp_page", fake_save)

    assert await miit.collect_icp(Repo(), uuid4(), ["云南省烟草公司楚雄州公司"]) == []
    assert len(saved) == 1
    assert saved[0]["mainLicence"] == "滇ICP备17004651号"


@pytest.mark.asyncio
async def test_icp_positive_total_empty_page_uses_fast_recovery_budget(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        pass

    async def fake_fetch(
        _client, _keyword, _page, timeout_seconds=10, session_key=""
    ):
        calls.append(session_key)
        return {"rows": [], "pages": 1, "total": 1}

    monkeypatch.setattr(miit, "ICP_PAGINATION_RECOVERY_PASSES", 2)
    monkeypatch.setattr(miit, "ICP_EMPTY_RESULT_RECOVERY_PASSES", 1)
    monkeypatch.setattr(miit, "ICP_COMPANY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_DIRECT_REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    errors = await miit.collect_icp(Repo(), uuid4(), ["测试企业"])

    assert len(calls) == 2
    assert len(set(calls)) == 2
    assert "上游报告 1 条" in errors[0]


@pytest.mark.asyncio
async def test_icp_serverless_proxy_is_forwarded_to_local_icp_service(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {
                "serverless_proxy": {
                    "enabled": True,
                    "endpoint": "https://example.test",
                }
            }

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((page, timeout_seconds, route_proxy, session_key))
        return {"rows": [], "pages": 1}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert len(calls) == 1
    assert calls[0][0:3] == (1, miit.ICP_PAGE_TIMEOUT_SECONDS, miit.settings.serverless_proxy_miit_url)
    assert calls[0][3]


def test_cloud_proxy_is_used_by_tianyancha_when_configured():
    import app.providers.registry as registry

    config = {
        "sessions": {},
        "serverless_proxy": {
            "enabled": True,
            "endpoint": "https://example.test",
        },
    }
    providers = registry.build_providers(["tianyancha"], config)
    try:
        assert len(providers) == 1
        assert providers[0].client._mounts
    finally:
        asyncio.run(providers[0].close())


@pytest.mark.asyncio
async def test_icp_single_manual_proxy_keeps_one_pagination_session(monkeypatch):
    import app.miit as miit

    calls = []
    manual = "http://user:pass@manual.example:8080"

    class Repo:
        async def get_runtime_config(self):
            return {
                "serverless_proxy": {
                    "enabled": True,
                    "endpoint": "https://cloud.example",
                },
                "manual_proxies": [{
                    "scheme": "http",
                    "host": "manual.example",
                    "port": 8080,
                    "username": "user",
                    "password": "pass",
                    "enabled": True,
                    "status": "ready",
                }],
            }

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((page, timeout_seconds, route_proxy, session_key))
        return {"rows": [], "pages": 2}

    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert len(calls) == 2
    assert all(call[2] == manual for call in calls)
    assert calls[0][3] == calls[1][3]
    assert not calls[0][3].endswith("_0")


@pytest.mark.asyncio
async def test_icp_single_manual_proxy_runs_one_company_at_a_time(monkeypatch):
    import app.miit as miit

    active = 0
    maximum = 0
    routes = []

    class Repo:
        async def get_runtime_config(self):
            return {
                "manual_proxies": [{
                    "scheme": "http",
                    "host": "manual.example",
                    "port": 8080,
                    "username": "user",
                    "password": "pass",
                    "enabled": True,
                    "status": "ready",
                }],
            }

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        routes.append(route_proxy)
        await asyncio.sleep(0.01)
        active -= 1
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(
        Repo(), uuid4(), [f"企业{i}" for i in range(4)]
    ) == []
    assert maximum == 1
    assert routes == ["http://user:pass@manual.example:8080"] * 4


@pytest.mark.asyncio
async def test_icp_manual_proxy_pool_parallelism_matches_ready_nodes(monkeypatch):
    import app.miit as miit

    active = 0
    maximum = 0
    routes = []

    class Repo:
        async def get_runtime_config(self):
            return {
                "manual_proxies": [
                    {
                        "scheme": "http",
                        "host": f"manual{index}.example",
                        "port": 8080,
                        "username": "",
                        "password": "",
                        "enabled": True,
                        "status": "ready",
                    }
                    for index in range(2)
                ],
            }

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        routes.append(route_proxy)
        await asyncio.sleep(0.01)
        active -= 1
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(
        Repo(), uuid4(), [f"企业{i}" for i in range(4)]
    ) == []
    assert maximum == 2
    assert set(routes) == {
        "http://manual0.example:8080",
        "http://manual1.example:8080",
    }


@pytest.mark.asyncio
async def test_icp_cloud_scheduler_does_not_apply_direct_ip_cooldown(monkeypatch):
    import app.miit as miit

    async def unexpected_sleep(_seconds):
        raise AssertionError("云函数请求不应使用直连出口的全局冷却")

    monkeypatch.setattr(miit.asyncio, "sleep", unexpected_sleep)
    scheduler = miit._IcpRequestScheduler(5, direct=False)
    for _ in range(12):
        await scheduler.before_request()
    assert scheduler.used == 0


@pytest.mark.asyncio
async def test_icp_cloud_queries_are_processed_one_company_per_node(monkeypatch):
    import app.miit as miit

    active = 0
    maximum = 0
    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": True, "endpoint": "https://example.test"}}

    async def fake_fetch(
        _client, keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        calls.append((keyword, page, route_proxy))
        await asyncio.sleep(0.01)
        active -= 1
        return {"rows": [], "pages": 1}

    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    errors = await miit.collect_icp(Repo(), uuid4(), [f"企业{i}" for i in range(12)])

    assert errors == []
    assert maximum == 1
    assert len(calls) == 12
    assert all(call[2] == miit.settings.serverless_proxy_miit_url for call in calls)


@pytest.mark.asyncio
async def test_icp_direct_queries_keep_five_request_burst_and_gap(monkeypatch):
    import app.miit as miit

    started = []

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": False, "endpoint": ""}}

    async def fake_fetch(
        _client, keyword, page, timeout_seconds=10, session_key=""
    ):
        started.append(keyword)
        return {"rows": [], "pages": 1}

    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_DIRECT_REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), [f"企业{i}" for i in range(12)]) == []
    assert started == [f"企业{i}" for i in range(12)]


@pytest.mark.asyncio
async def test_icp_direct_queries_pipeline_independent_companies(monkeypatch):
    import app.miit as miit

    active = 0
    maximum = 0

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": False, "endpoint": ""}}

    async def fake_fetch(
        _client, _keyword, _page, timeout_seconds=10, session_key=""
    ):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "ICP_DIRECT_REQUEST_GAP_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(
        Repo(), uuid4(), [f"企业{i}" for i in range(5)]
    ) == []
    assert maximum == 5


@pytest.mark.asyncio
async def test_icp_cloud_rotation_scheduler_pauses_without_changing_generation():
    import app.miit as miit

    scheduler = miit._IcpCloudRotationScheduler(5, pause_seconds=0)
    generations = []
    for _ in range(6):
        generation = await scheduler.acquire()
        generations.append(generation)
        await scheduler.release()

    assert generations == [0, 0, 0, 0, 0, 0]
    assert scheduler.used == 1
    assert scheduler.active == 0
    assert scheduler.generation == 0


@pytest.mark.asyncio
async def test_icp_cloud_reuses_session_key_after_five_actual_requests(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": True, "endpoint": "https://example.test"}}

    async def fake_fetch(
        _client, keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((keyword, page, route_proxy, session_key))
        return {"rows": [], "pages": 1}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), [f"企业{i}" for i in range(6)]) == []
    assert len(calls) == 6
    assert {item[3] for item in calls} == {"lane_0_0"}


@pytest.mark.asyncio
async def test_icp_cloud_waf_retries_current_page_after_rotating_session(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": True, "endpoint": "https://example.test"}}

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((page, route_proxy, session_key))
        if len(calls) == 1:
            raise miit.IcpPageError("当前访问已被创宇盾拦截")
        return {"rows": [], "pages": 1}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == 1
    assert calls[0][1] == calls[1][1] == miit.settings.serverless_proxy_miit_url
    assert calls[0][2] != calls[1][2]


@pytest.mark.asyncio
async def test_icp_cloud_waf_exhaustion_enters_company_retry(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {
                "serverless_proxy": {
                    "enabled": True,
                    "endpoint": "https://example.test",
                }
            }

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((page, route_proxy, session_key))
        if len(calls) <= 3:
            raise miit.IcpPageError("当前访问已被创宇盾拦截")
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_COMPANY_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(miit, "ICP_COMPANY_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert len(calls) == 4
    assert all(call[0] == 1 for call in calls)
    assert all(call[1] == miit.settings.serverless_proxy_miit_url for call in calls)
    assert [call[2] for call in calls] == ["lane_0_0", "lane_0_1", "lane_0_2", "lane_0_2"]


@pytest.mark.asyncio
async def test_icp_cloud_concurrent_waf_failover_does_not_deadlock(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": True, "endpoint": "https://example.test"}}

    async def fake_fetch(
        _client, keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((keyword, session_key))
        if session_key.endswith("_0"):
            raise miit.IcpPageError("当前访问已被创宇盾拦截")
        return {"rows": [], "pages": 1}

    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    monkeypatch.setattr(miit, "ICP_BATCH_PAUSE_SECONDS", 0)
    errors = await asyncio.wait_for(
        miit.collect_icp(Repo(), uuid4(), [f"企业{i}" for i in range(5)]),
        timeout=2,
    )

    assert errors == []
    assert len(calls) == 6
    assert calls[0][1].endswith("_0")
    assert all(item[1].endswith("_1") for item in calls[1:])


@pytest.mark.asyncio
async def test_icp_proxy_pool_skips_pause_when_burst_already_covers_waf_window():
    import app.miit as miit

    scheduler = miit._IcpProxyPoolScheduler(["http://a"], 2, pause_seconds=0.05)
    first = await scheduler.acquire()
    await scheduler.release()
    await asyncio.sleep(0.06)
    second = await scheduler.acquire()
    await scheduler.release()
    started = asyncio.get_running_loop().time()
    third = await scheduler.acquire()
    await scheduler.release()
    elapsed = asyncio.get_running_loop().time() - started
    assert [first[1], second[1], third[1]] == [0, 0, 0]
    assert elapsed < 0.02


@pytest.mark.asyncio
async def test_icp_cloud_scheduler_only_pauses_remaining_waf_window():
    import app.miit as miit

    scheduler = miit._IcpCloudRotationScheduler(2, pause_seconds=0.05)
    first = await scheduler.acquire()
    await scheduler.release()
    await asyncio.sleep(0.02)
    second = await scheduler.acquire()
    await scheduler.release()
    started = asyncio.get_running_loop().time()
    third = await scheduler.acquire()
    await scheduler.release()
    elapsed = asyncio.get_running_loop().time() - started
    assert [first, second, third] == [0, 0, 0]
    assert 0.02 <= elapsed < 0.05


@pytest.mark.asyncio
async def test_icp_cloud_timeout_keeps_same_lane(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": True, "endpoint": "https://example.test"}}

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((page, session_key))
        if len(calls) == 1:
            raise miit.IcpPageError("请求硬超时（不超过 36 秒）")
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert [item[1] for item in calls] == ["lane_0_0", "lane_0_0"]


@pytest.mark.asyncio
async def test_icp_cloud_timeout_does_not_start_company_budget(monkeypatch):
    import app.miit as miit

    calls = []

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": True, "endpoint": "https://example.test"}}

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        calls.append((page, session_key, timeout_seconds))
        if len(calls) <= 2:
            raise miit.IcpPageError("请求硬超时（不超过 36 秒）")
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "ICP_COMPANY_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)

    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert [item[1] for item in calls] == ["lane_0_0", "lane_0_0", "lane_0_0"]
    assert calls[0][2] == miit.ICP_PAGE_TIMEOUT_SECONDS
    assert all(item[2] == miit.ICP_WARM_PAGE_TIMEOUT_SECONDS for item in calls[1:])


@pytest.mark.asyncio
async def test_icp_cloud_timeout_does_not_reacquire_lane(monkeypatch):
    import app.miit as miit

    acquire_calls = []
    fetch_calls = []

    class CountingScheduler:
        def __init__(self, *args, **kwargs):
            self.used = [0]
            self.generations = [0]
            self.active = 0

        async def acquire(self, preferred_index=None):
            acquire_calls.append(preferred_index)
            self.active += 1
            self.used[0] += 1
            return "http://seamoon-gateway:19080", 0, 0

        async def release(self):
            self.active = max(0, self.active - 1)

        async def rotate(self, index, generation):
            self.generations[index] += 1

    class Repo:
        async def get_runtime_config(self):
            return {"serverless_proxy": {"enabled": True, "endpoint": "https://example.test"}}

    async def fake_fetch(
        _client, _keyword, page, timeout_seconds=10, route_proxy="", session_key=""
    ):
        fetch_calls.append(session_key)
        if len(fetch_calls) == 1:
            raise miit.IcpPageError("请求硬超时（不超过 36 秒）")
        return {"rows": [], "pages": 1, "total": 0}

    monkeypatch.setattr(miit, "_IcpProxyPoolScheduler", CountingScheduler)
    monkeypatch.setattr(miit, "ICP_CONCURRENCY", 1)
    monkeypatch.setattr(miit, "_fetch_page", fake_fetch)
    assert await miit.collect_icp(Repo(), uuid4(), ["测试企业"]) == []
    assert acquire_calls == [None]
    assert fetch_calls == ["lane_0_0", "lane_0_0"]
