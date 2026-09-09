import asyncio
from collections import Counter

import httpx
import pytest

from app.providers.aiqicha import AnonymousAiqicha
from app.providers.kuaicha import AnonymousKuaicha
from app.providers.riskbird import AnonymousRiskbird
from app.providers.tianyancha import AnonymousTianyancha, Investment, ProviderError
from app.providers.pagination import IncompletePagination, all_pages, reported_total
from app.providers.pagination import ProviderRateLimited
from app.settings import settings


def investment(index):
    return Investment(str(index), str(index), 100, {"id": str(index)})


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", [
    lambda: AnonymousTianyancha("https://example.test"),
    AnonymousAiqicha, AnonymousKuaicha, AnonymousRiskbird,
])
async def test_all_providers_follow_authoritative_total_with_capped_pages(factory):
    calls = []

    async def fetch(page):
        calls.append(page)
        return [investment(i) for i in range((page - 1) * 2, min(page * 2, 7))], 7

    provider = factory()
    try:
        result = await provider.all_pages(fetch)
        assert [item.external_id for item in result] == [str(i) for i in range(7)]
        assert sorted(calls) == [1, 2, 3, 4]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_overlapping_pages_do_not_satisfy_total():
    async def fetch(page):
        return [investment(1), investment(2)], 4

    with pytest.raises(IncompletePagination, match="2/4") as error:
        await all_pages(fetch, label="test", page_size=2)
    assert len(error.value.rows) == 2


@pytest.mark.asyncio
async def test_one_recovery_pass_can_complete_overlapping_pages():
    calls = Counter()

    async def fetch(page):
        calls[page] += 1
        ids = [1, 2] if page == 1 or calls[page] == 1 else [3, 4]
        return [investment(i) for i in ids], 4

    result = await all_pages(fetch, label="test", page_size=2)
    assert {item.external_id for item in result} == {"1", "2", "3", "4"}
    assert calls == {1: 2, 2: 2}


@pytest.mark.asyncio
async def test_failed_page_is_retried_without_losing_other_pages():
    calls = Counter()

    async def fetch(page):
        calls[page] += 1
        if page == 2:
            raise ProviderError("unavailable")
        return [investment(page)], 3

    with pytest.raises(IncompletePagination, match="第 2 页") as error:
        await all_pages(fetch, label="test", page_size=1)
    assert {item.external_id for item in error.value.rows} == {"1", "3"}
    assert calls == {1: 1, 2: 2, 3: 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("totals", [(2, 3, 3), (3, 2, 2)])
async def test_changing_totals_never_succeed(totals):
    async def fetch(page):
        return [investment(page)], totals[page - 1]

    with pytest.raises(IncompletePagination, match="总数不一致"):
        await all_pages(fetch, label="test", page_size=1)


@pytest.mark.asyncio
async def test_more_than_fifty_pages_are_not_truncated():
    async def fetch(page):
        return [investment(page)], 61

    assert len(await all_pages(fetch, label="test", page_size=1)) == 61


@pytest.mark.asyncio
async def test_repeated_page_without_total_is_an_error():
    async def fetch(page):
        return [investment(1)], None

    with pytest.raises(IncompletePagination, match="分页重复"):
        await all_pages(fetch, label="test", page_size=1)


def test_missing_total_is_distinct_from_zero_and_invalid_total():
    assert reported_total({}, "total") is None
    assert reported_total({"total": 0}, "total") == 0
    with pytest.raises(ProviderError):
        reported_total({"total": -1}, "total")


@pytest.mark.asyncio
async def test_request_budget_is_shared_across_provider_instances_and_pages(monkeypatch):
    monkeypatch.setattr(settings, "provider_request_concurrency", 3)
    monkeypatch.setattr(settings, "provider_page_concurrency", 4)
    active = maximum = 0

    async def handler(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.005)
            return httpx.Response(200, json={"state": "ok"})
        finally:
            active -= 1

    providers = [AnonymousTianyancha("https://example.test") for _ in range(2)]
    for provider in providers:
        await provider.client.aclose()
        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")

    async def collect(provider):
        async def fetch(page):
            await provider._request("GET", "/page")
            return [investment(page)], 12
        return await provider.all_pages(fetch)

    try:
        results = await asyncio.gather(*(collect(provider) for provider in providers))
        assert [len(items) for items in results] == [12, 12]
        assert maximum == 3
        assert active == 0
    finally:
        for provider in providers:
            await provider.close()


@pytest.mark.asyncio
async def test_rate_limit_does_not_rotate_proxy_and_cools_down_other_pages(monkeypatch):
    requests = 0

    def handler(request):
        nonlocal requests
        requests += 1
        return httpx.Response(429, headers={"retry-after": "60"})

    async def rotate():
        raise AssertionError("429 must not rotate proxies")

    provider = AnonymousTianyancha("https://example.test", proxy="http://proxy.test:8080")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(provider, "_rotate_proxy", rotate)
    try:
        for _ in range(2):
            with pytest.raises(ProviderRateLimited):
                await provider._request("GET", "/page")
        assert requests == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_rate_limited_pagination_stops_scheduling_remaining_windows(monkeypatch):
    monkeypatch.setattr(settings, "provider_page_concurrency", 2)
    calls = []

    async def fetch(page):
        calls.append(page)
        if page > 1:
            raise ProviderRateLimited("limited")
        return [investment(1)], 100_000

    with pytest.raises(IncompletePagination, match="限流冷却") as error:
        await all_pages(fetch, label="test", page_size=1)
    assert calls == [1, 2, 3]
    assert len(error.value.rows) == 1
