import asyncio
import httpx
import pytest

from app.providers.aiqicha import AnonymousAiqicha
from app.providers.kuaicha import AnonymousKuaicha
from app.providers.riskbird import AnonymousRiskbird
from app.providers.tianyancha import ProviderError


def _no_auth(headers) -> None:
    lowered = {key.lower() for key in headers}
    assert "cookie" not in lowered
    assert "x-tycid" not in lowered
    assert "x-auth-token" not in lowered
    assert "authorization" not in lowered


@pytest.mark.asyncio
async def test_kuaicha_rate_limit_retries_then_fails():
    seen = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        _no_auth(request.headers)
        seen["count"] += 1
        return httpx.Response(200, json={"status_code": 4001, "status_msg": "请求过于频繁，请稍候重试", "data": None})

    provider = AnonymousKuaicha()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://www.kuaicha365.com", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="请求过于频繁"):
        await provider.search("小米科技有限责任公司")
    assert seen["count"] == 2
    await provider.close()


@pytest.mark.asyncio
async def test_aiqicha_investments_decode_pid_and_parse_holds():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("advanceFilterAjax"):
            return httpx.Response(200, json={
                "status": 0,
                "ddw": 1,
                "data": {"resultList": [{"pid": "14", "entName": "小米科技有限责任公司"}]},
            })
        return httpx.Response(200, json={
            "status": 0,
            "data": {"investRecordData": {"total": 1, "list": [{"entName": "子公司", "pid": "88", "regRate": "100%"}]}},
        })

    provider = AnonymousAiqicha(cookie="BDUSS=1")
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    selected, _ = await provider.search("小米科技有限责任公司")
    rows, total = await provider.investments(selected.external_id)
    assert selected.external_id == "15"
    assert rows[0].name == "子公司" and rows[0].holding_percent == 100
    assert total == 1
    assert seen[1][0].endswith("stockchartAjax")
    assert seen[1][1]["pid"] == "15"
    await provider.close()


@pytest.mark.asyncio
async def test_kuaicha_search_and_get_invest():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        _no_auth(request.headers)
        if request.url.path.endswith("company_search_pc"):
            return httpx.Response(200, json={"status_code": 0, "data": {"list": [
                {"org_id": "9", "org_name": "其他公司"},
                {"org_id": "8", "org_name": "小米科技有限责任公司"},
            ]}})
        return httpx.Response(200, json={"status_code": 0, "data": {"total": 1, "list": [
            {"frgn_invest_corp_name": "子公司", "frgn_invest_corp_id": "88", "invest_ratio": "100%"},
        ]}})

    provider = AnonymousKuaicha()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://www.kuaicha365.com", transport=httpx.MockTransport(handler))
    selected, candidates = await provider.search("小米科技有限责任公司")
    rows, total = await provider.investments(selected.external_id, 1)
    assert selected.external_id == "8"
    assert len(candidates) == 2
    assert rows[0].name == "子公司" and rows[0].holding_percent == 100
    assert total == 1
    assert seen[1][0] == "GET"
    assert seen[1][2]["org_id"] == "8"
    await provider.close()


@pytest.mark.asyncio
async def test_riskbird_tourist_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        _no_auth(request.headers)
        return httpx.Response(200, json={"code": 20000, "msg": "成功", "success": True, "data": {"msg": "访问已达上限", "state": "limit:tourist"}})

    provider = AnonymousRiskbird()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://www.riskbird.com", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="游客访问已达上限"):
        await provider.search("小米科技有限责任公司")
    await provider.close()


@pytest.mark.asyncio
async def test_riskbird_search_uses_order_no():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.read().decode() if request.content else dict(request.url.params)))
        _no_auth(request.headers)
        if request.url.path.endswith("newSearch"):
            return httpx.Response(200, json={"code": 20000, "data": {"list": [
                {"entid": "eMBp0ahTid3", "entName": "小米科技有限责任公司"},
            ]}})
        if request.url.path.endswith("/api/ent/query") or request.url.path.endswith("ent/query"):
            return httpx.Response(200, json={"state": 2, "orderNo": "WEB123"})
        return httpx.Response(200, json={"code": 20000, "data": {"totalCount": 1, "apiData": [
            {"entName": "子公司", "entid": "2", "funderRatio": "80%"},
        ]}})

    provider = AnonymousRiskbird()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(base_url="https://www.riskbird.com", transport=httpx.MockTransport(handler))
    selected, _ = await provider.search("小米科技有限责任公司")
    rows, _ = await provider.investments(selected.external_id)
    assert selected.external_id == "eMBp0ahTid3"
    assert selected.payload["orderNo"] == "WEB123"
    assert rows[0].holding_percent == 80
    assert any("companyInfo/list" in path and "WEB123" in str(body) for _, path, body in seen)
    await provider.close()


@pytest.mark.asyncio
async def test_aiqicha_captcha_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        _no_auth(request.headers)
        return httpx.Response(302, headers={"location": "https://wappass.baidu.com/static/captcha/tuxing_v2.html"})

    provider = AnonymousAiqicha()
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(ProviderError, match="安全验证"):
        await provider.search("小米科技有限责任公司")
    await provider.close()


def test_default_clients_do_not_send_cookie():
    for provider in (AnonymousAiqicha(), AnonymousKuaicha(), AnonymousRiskbird()):
        assert "cookie" not in {key.lower() for key in provider.client.headers}
        asyncio.run(provider.close())


def test_configured_cookie_is_sent():
    from app.runtime import make_client

    client = make_client(headers={"User-Agent": "test"}, timeout=1, cookie="kc=1")
    try:
        assert client.headers.get("cookie") == "kc=1"
    finally:
        asyncio.run(client.aclose())

    provider = AnonymousKuaicha(cookie="kc=1")
    try:
        assert provider.client.headers.get("cookie") == "kc=1"
    finally:
        asyncio.run(provider.close())


def test_build_providers_applies_session_only_to_login_sources():
    from app.providers.registry import build_providers
    from app.providers.tianyancha import AnonymousTianyancha

    providers = build_providers(
        ["tianyancha", "kuaicha", "riskbird"],
        {
            "proxy_mode": "none",
            "sessions": {
                "kuaicha": {"cookie": "kc=1", "expires_at": None},
                "riskbird": {"cookie": "rb=2", "expires_at": None},
            },
        },
    )
    try:
        tianyancha, kuaicha, riskbird = providers
        assert isinstance(tianyancha, AnonymousTianyancha)
        assert "cookie" not in {key.lower() for key in tianyancha.client.headers}
        assert kuaicha.client.headers.get("cookie") == "kc=1"
        assert riskbird.client.headers.get("cookie") == "rb=2"
    finally:
        async def _close():
            for provider in providers:
                await provider.close()
        asyncio.run(_close())


def test_build_providers_ignore_legacy_proxy_settings_and_connect_directly():
    from app.providers.registry import build_providers

    providers = build_providers(
        ["kuaicha"],
        {
            "proxy_mode": "socks",
            "proxy_url": "127.0.0.1:7891",
            "sessions": {"kuaicha": {"cookie": "kc=1", "expires_at": None}},
        },
    )
    try:
        assert len(providers) == 1
        assert not hasattr(providers[0], "_proxied")
        assert providers[0].client.headers.get("cookie") == "kc=1"
        assert providers[0].client._trust_env is False
    finally:
        asyncio.run(providers[0].close())


def test_unauthenticated_session_providers_are_skipped():
    from app.providers.registry import build_providers, login_required_errors
    from app.providers.tianyancha import AnonymousTianyancha

    errors = login_required_errors(["tianyancha", "aiqicha", "kuaicha"], {"sessions": {}})
    assert errors == ["请先登录爱企查", "请先登录快查"]
    providers = build_providers(["tianyancha", "aiqicha", "kuaicha"], {"sessions": {}})
    try:
        assert len(providers) == 1
        assert isinstance(providers[0], AnonymousTianyancha)
    finally:
        asyncio.run(providers[0].close())


@pytest.mark.asyncio
async def test_aiqicha_proxy_rotates_tunnel_after_fifteen_requests(monkeypatch):
    from app.providers.aiqicha import AIQICHA_PROXY_REQUEST_LIMIT

    provider = AnonymousAiqicha(proxy="http://proxy.test:19080")
    await provider.client.aclose()

    clients: list[httpx.AsyncClient] = []
    generations: list[int] = []

    def make_client() -> httpx.AsyncClient:
        generation = len(clients) + 1

        def handler(_request: httpx.Request) -> httpx.Response:
            generations.append(generation)
            return httpx.Response(200, json={"status": 0, "data": {"list": [{"id": "ok"}]}})

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(provider, "_make_client", make_client)
    provider.client = make_client()

    for _ in range(AIQICHA_PROXY_REQUEST_LIMIT + 1):
        await provider._get("https://aiqicha.baidu.com/test")

    assert generations == [1] * AIQICHA_PROXY_REQUEST_LIMIT + [2]
    assert clients[0].is_closed is True
    assert clients[1].is_closed is False
    await provider.close()


def test_build_providers_routes_aiqicha_through_active_seamoon():
    from app.providers.registry import build_providers
    from app.settings import settings

    providers = build_providers(
        ["aiqicha"],
        {
            "sessions": {"aiqicha": {"cookie": "aqc=1", "expires_at": None}},
            "serverless_proxy": {
                "enabled": True,
                "endpoint": "https://example.test",
            },
        },
    )
    try:
        assert len(providers) == 1
        assert providers[0]._proxy == settings.serverless_proxy_url
        assert providers[0].client.headers.get("cookie") == "aqc=1"
    finally:
        asyncio.run(providers[0].close())
