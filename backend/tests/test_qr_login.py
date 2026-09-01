import time
import base64
import datetime
import json

import httpx
import pytest

from app.qr_login import QrSession, _auth_cookie_expiry, _baidu_channel_status, _decode_jsonp, _poll_aiqicha, _poll_kuaicha, _poll_riskbird


def test_decode_jsonp_pending_channel_is_nested_string():
    data = _decode_jsonp('tangram_guid_1({"errno":0,"channel_v":"{\\"status\\":1}"})')
    status, value, _ = _baidu_channel_status(data["channel_v"])
    assert status == 1
    assert value == ""


def test_decode_jsonp_confirmed_channel_returns_bduss():
    data = _decode_jsonp('tangram_guid_1({"errno":0,"channel_v":"{\\"status\\":0,\\"v\\":\\"BDUSS-TEST\\"}"})')
    status, value, _ = _baidu_channel_status(data["channel_v"])
    assert status == 0
    assert value == "BDUSS-TEST"


def test_decode_plain_json_channel():
    data = _decode_jsonp('{"errno":0,"channel_v":"{\\"status\\":0,\\"v\\":\\"BDUSS-TEST\\"}"}')
    status, value, _ = _baidu_channel_status(data["channel_v"])
    assert status == 0
    assert value == "BDUSS-TEST"


def test_decode_dict_channel_keeps_backwards_compatibility():
    status, value, _ = _baidu_channel_status({"status": 0, "v": "BDUSS-TEST"})
    assert status == 0
    assert value == "BDUSS-TEST"


def test_decode_channel_keeps_redirect_url():
    _, _, redirect = _baidu_channel_status({"status": 0, "v": "BDUSS-TEST", "u": "https://www.baidu.com/"})
    assert redirect == "https://www.baidu.com/"


@pytest.mark.asyncio
async def test_aiqicha_poll_scanned_reports_scanned():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/channel/unicast")
        return httpx.Response(200, text='tangram_guid_1({"errno":0,"channel_v":"{\\"status\\":1}"})')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = QrSession(
        provider="aiqicha",
        session_id="s",
        created_at=time.monotonic(),
        payload={"sign": "x", "gid": "y", "callback": "cb"},
        client=client,
    )
    result = await _poll_aiqicha(session)
    assert result.status == "scanned"
    await client.aclose()


@pytest.mark.asyncio
async def test_aiqicha_poll_confirmed_exchanges_cookie():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channel/unicast"):
            return httpx.Response(200, text='tangram_guid_1({"errno":0,"channel_v":"{\\"status\\":0,\\"v\\":\\"BDUSS-TEST\\"}"})')
        if request.url.path.endswith("/qrbdusslogin"):
            return httpx.Response(200, headers={"set-cookie": "BDUSS=abc123; Domain=.baidu.com; Path=/; Max-Age=3600"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = QrSession(
        provider="aiqicha",
        session_id="s",
        created_at=time.monotonic(),
        payload={"sign": "x", "gid": "y", "callback": "cb"},
        client=client,
    )
    result = await _poll_aiqicha(session)
    assert result.status == "success"
    assert "BDUSS=abc123" in result.cookie
    assert result.expires_at is not None


@pytest.mark.asyncio
async def test_riskbird_poll_scanned_is_not_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 10000016, "msg": "已扫码，等待确认"})

    client = httpx.AsyncClient(base_url="https://www.riskbird.com", transport=httpx.MockTransport(handler))
    session = QrSession(
        provider="riskbird",
        session_id="s",
        created_at=time.monotonic(),
        payload={"uuid": "u"},
        client=client,
    )
    result = await _poll_riskbird(session)
    assert result.status == "scanned"
    await client.aclose()


@pytest.mark.asyncio
async def test_riskbird_poll_success_saves_cookie():
    exp = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 3600

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/auth/login"):
            return httpx.Response(200, json={"code": 20000, "msg": "成功"}, headers={"set-cookie": f"token={_jwt_with_exp(exp)}; Path=/; Max-Age=3600"})
        assert request.url.path.endswith("/user/getInfo")
        return httpx.Response(200, json={"code": 0, "data": {"userId": 1}})

    client = httpx.AsyncClient(base_url="https://www.riskbird.com", transport=httpx.MockTransport(handler))
    session = QrSession(
        provider="riskbird",
        session_id="s",
        created_at=time.monotonic(),
        payload={"uuid": "u"},
        client=client,
    )
    result = await _poll_riskbird(session)
    assert result.status == "success"
    assert "token=" in result.cookie
    assert result.expires_at is not None


@pytest.mark.asyncio
async def test_riskbird_poll_expired_and_pending():
    def expired_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 10000014, "msg": "二维码已过期"})

    def pending_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 10000015, "msg": "等待扫码"})

    expired_client = httpx.AsyncClient(base_url="https://www.riskbird.com", transport=httpx.MockTransport(expired_handler))
    expired_session = QrSession(
        provider="riskbird",
        session_id="s",
        created_at=time.monotonic(),
        payload={"uuid": "u"},
        client=expired_client,
    )
    assert (await _poll_riskbird(expired_session)).status == "expired"

    pending_client = httpx.AsyncClient(base_url="https://www.riskbird.com", transport=httpx.MockTransport(pending_handler))
    pending_session = QrSession(
        provider="riskbird",
        session_id="s",
        created_at=time.monotonic(),
        payload={"uuid": "u"},
        client=pending_client,
    )
    assert (await _poll_riskbird(pending_session)).status == "pending"
    await pending_client.aclose()


@pytest.mark.asyncio
async def test_kuaicha_poll_state_machine_saves_cookie():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if request.url.path.endswith("/scan/getInfoNew"):
            body = request.content.decode()
            if "state=1" in body:
                return httpx.Response(200, json={"status": 2, "msg": "已扫码，等待确认"})
            return httpx.Response(200, json={"status": 3, "msg": "登录成功"})
        if "10jqka.com.cn" in request.url.host:
            return httpx.Response(200, headers={"set-cookie": "TOKEN=abc; Path=/; Max-Age=3600"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="https://upass.kuaicha365.com", transport=httpx.MockTransport(handler))
    session = QrSession(
        provider="kuaicha",
        session_id="s",
        created_at=time.monotonic(),
        payload={"qrid": "q", "state": 1},
        client=client,
    )
    first = await _poll_kuaicha(session)
    assert first.status == "pending"
    assert session.payload["state"] == 2
    second = await _poll_kuaicha(session)
    assert second.status == "success"
    assert "TOKEN=abc" in second.cookie
    assert second.expires_at is None


def _jwt_with_exp(exp: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.signature"


@pytest.mark.asyncio
async def test_riskbird_expiry_uses_token_jwt():
    exp = 1787888449

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 20000}, headers={"set-cookie": f"token={_jwt_with_exp(exp)}; Path=/; Max-Age=3600"})

    client = httpx.AsyncClient(base_url="https://www.riskbird.com", transport=httpx.MockTransport(handler))
    await client.get("/")
    assert _auth_cookie_expiry(client, "riskbird") == datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
    await client.aclose()


@pytest.mark.asyncio
async def test_aiqicha_expiry_uses_bduss_not_other_cookies():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={},
            headers=[
                ("set-cookie", "BDUSS=abc; Path=/; Max-Age=3600"),
                ("set-cookie", "BAIDUID=xyz; Path=/; Max-Age=315360000"),
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await client.get("https://passport.baidu.com/")
    result = _auth_cookie_expiry(client, "aiqicha")
    assert result is not None
    assert datetime.datetime.now(datetime.timezone.utc) < result < datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    await client.aclose()


@pytest.mark.asyncio
async def test_kuaicha_expiry_uses_sess_tk_jwt():
    exp = 1790565172

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, headers={"set-cookie": f"sess_tk={_jwt_with_exp(exp)}; Path=/; Max-Age=3600"})

    client = httpx.AsyncClient(base_url="https://upass.kuaicha365.com", transport=httpx.MockTransport(handler))
    await client.get("/")
    assert _auth_cookie_expiry(client, "kuaicha") == datetime.datetime.fromtimestamp(exp, tz=datetime.timezone.utc)
    await client.aclose()
