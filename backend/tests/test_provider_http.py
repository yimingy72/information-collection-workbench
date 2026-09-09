import json
import asyncio

import httpx
import pytest

import app.providers.tianyancha as tianyancha
from app.providers.tianyancha import AnonymousTianyancha


@pytest.mark.asyncio
async def test_tianyancha_retired_client_closes_when_last_request_finishes(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"state": "ok"})

    provider = AnonymousTianyancha("https://example.test", proxy="http://proxy.test:8080")
    await provider.client.aclose()
    old = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    provider.client = old
    new = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(provider, "_make_client", lambda: new)
    task = asyncio.create_task(provider._request("GET", "/page"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await provider._rotate_proxy()
        assert not old.is_closed
        release.set()
        assert await task == {"state": "ok"}
        assert old.is_closed
        assert provider._retired_clients == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await provider.close()


@pytest.mark.asyncio
async def test_search_parses_exact_candidate_and_full_pagination():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("searchCompanyV4"):
            return httpx.Response(200, json={"data": {"companyList": [
                {"id": "2", "name": "其他公司"},
                {"id": "1", "name": "<em>目标公司</em>"},
            ]}})
        page = int(json.loads(request.content)["pageNum"])
        if page == 1:
            rows = [{"id": str(index), "name": f"子公司{index}", "percent": "60%"} for index in range(100)]
            return httpx.Response(200, json={"state": "ok", "data": {"result": rows}})
        return httpx.Response(200, json={"state": "ok", "data": {
            "result": [{"id": "x", "name": "尾页", "percent": "60%"}],
        }})

    provider = AnonymousTianyancha("https://example.test")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    selected, candidates = await provider.search("目标公司")
    rows = await provider.all_pages(lambda page: provider.investments("1", page))
    assert selected.external_id == "1"
    assert selected.name == "目标公司"
    assert len(candidates) == 2
    assert len(rows) == 101
    assert [request.url.path for request in seen] == [
        "/cloud-tempest/web/searchCompanyV4",
        "/cloud-company-background/company/investListV2",
        "/cloud-company-background/company/investListV2",
    ]
    for request in seen:
        assert "cookie" not in request.headers
        assert "x-tycid" not in request.headers
        assert "x-auth-token" not in request.headers
    await provider.close()


@pytest.mark.asyncio
async def test_all_pages_stops_on_short_page_without_total():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"state": "ok", "data": {
            "result": [{"id": "3", "name": "子公司", "percent": "60%"}],
        }})

    provider = AnonymousTianyancha("https://example.test")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    rows = await provider.all_pages(lambda page: provider.investments("1", page))
    assert len(rows) == 1
    assert len(seen) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_login_wall_rebuilds_proxy_and_retries_same_query_immediately(monkeypatch):
    seen = []
    rotations = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(200, json={"state": "warn", "message": "请登录以使用完整功能"})
        return httpx.Response(200, json={
            "state": "ok",
            "data": {"companyList": [{"id": "1", "name": "目标公司"}]},
        })

    async def rotate_proxy():
        rotations.append(True)

    async def unexpected_sleep(_seconds):
        raise AssertionError("登录墙重试不应等待")

    provider = AnonymousTianyancha("https://example.test", proxy="http://proxy.test:8080")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(provider, "_rotate_proxy", rotate_proxy)
    monkeypatch.setattr(tianyancha.asyncio, "sleep", unexpected_sleep)

    selected, _ = await provider.search("目标公司")

    assert selected.external_id == "1"
    assert len(seen) == 2
    assert len(rotations) == 1
    assert seen[0].content == seen[1].content
    await provider.close()


@pytest.mark.asyncio
async def test_login_wall_exhausts_five_attempts_on_same_query(monkeypatch):
    seen = []
    rotations = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"state": "warn", "message": "请登录以使用完整功能"})

    async def rotate_proxy():
        rotations.append(True)

    provider = AnonymousTianyancha("https://example.test", proxy="http://proxy.test:8080")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(provider, "_rotate_proxy", rotate_proxy)

    with pytest.raises(Exception) as error:
        await provider.search("目标公司")

    assert "failed after 5 attempts" in str(error.value)
    assert "请登录以使用完整功能" in str(error.value)
    assert len(seen) == 5
    assert len(rotations) == 4
    await provider.close()


@pytest.mark.asyncio
async def test_all_pages_uses_reported_total_when_page_is_capped():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(json.loads(request.content)["pageNum"])
        seen.append(page)
        rows = [
            {"id": str(index), "name": f"子公司{index}", "percent": "60%"}
            for index in range((page - 1) * 20, min(page * 20, 52))
        ]
        return httpx.Response(200, json={
            "state": "ok",
            "data": {"result": rows, "total": 52},
        })

    provider = AnonymousTianyancha("https://example.test")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    rows = await provider.all_pages(lambda page: provider.investments("1", page))
    assert len(rows) == 52
    assert seen[0] == 1
    assert set(seen) == {1, 2, 3}
    await provider.close()



@pytest.mark.asyncio
async def test_all_pages_fetches_known_pages_concurrently():
    started = {2: __import__("asyncio").Event(), 3: __import__("asyncio").Event()}
    overlap = False
    calls = []

    async def fetch(page: int):
        nonlocal overlap
        calls.append(page)
        if page == 1:
            return [object()] * 20, 52
        started[page].set()
        other = 3 if page == 2 else 2
        await __import__("asyncio").wait_for(started[other].wait(), timeout=1)
        overlap = True
        count = 20 if page == 2 else 12
        return [object()] * count, 52

    provider = AnonymousTianyancha("https://example.test")
    rows = await provider.all_pages(fetch)
    assert len(rows) == 52
    assert overlap
    assert set(calls) == {1, 2, 3}
    await provider.close()


@pytest.mark.asyncio
async def test_all_pages_retries_only_the_failed_page():
    calls = []

    async def fetch(page: int):
        calls.append(page)
        if page == 1:
            return [object()] * 20, 40
        if page == 2 and calls.count(2) == 1:
            raise tianyancha.ProviderError("page 2 failed")
        return [object()] * 20, 40

    provider = AnonymousTianyancha("https://example.test")
    rows = await provider.all_pages(fetch)
    assert len(rows) == 40
    assert calls == [1, 2, 2]
    await provider.close()
