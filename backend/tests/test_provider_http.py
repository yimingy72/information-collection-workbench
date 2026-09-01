import json

import httpx
import pytest

from app.providers.tianyancha import AnonymousTianyancha


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
async def test_login_wall_is_not_retried():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"state": "warn", "message": "请登录以使用完整功能"})

    provider = AnonymousTianyancha("https://example.test")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as error:
        await provider.search("目标公司")
    assert "请登录以使用完整功能" in str(error.value)
    assert len(seen) == 1
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
    assert seen == [1, 2, 3]
    await provider.close()
