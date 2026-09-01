from __future__ import annotations

import base64
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.runtime import make_client

logger = logging.getLogger(__name__)

QR_TTL = 180
QR_POLL_TIMEOUT = 10.0
BAIDU_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.60 Safari/537.36"
)


@dataclass
class QrSession:
    provider: str
    session_id: str
    created_at: float
    payload: dict[str, Any]
    client: httpx.AsyncClient = field(repr=False)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.session_id}"

    def expired(self) -> bool:
        return time.monotonic() - self.created_at > QR_TTL


@dataclass
class QrPollResult:
    status: str
    cookie: str = ""
    expires_at: datetime | None = None


_sessions: dict[str, QrSession] = {}


def _now() -> float:
    return time.monotonic()


def _cookie_string(client: httpx.AsyncClient) -> str:
    parts: list[str] = []
    for cookie in client.cookies.jar:
        if cookie.value is not None and str(cookie.value):
            parts.append(f"{cookie.name}={cookie.value}")
    return "; ".join(parts)


def _cookie_expiry(cookie: Any) -> datetime | None:
    expires = getattr(cookie, "expires", None)
    if not expires:
        return None
    try:
        return datetime.fromtimestamp(float(expires), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _jwt_expiry(value: str) -> datetime | None:
    try:
        payload_b64 = value.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = int(payload.get("exp") or 0)
        if exp:
            return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        pass
    return None


def _auth_cookie_expiry(client: httpx.AsyncClient, provider: str) -> datetime | None:
    for cookie in client.cookies.jar:
        if provider == "aiqicha" and cookie.name == "BDUSS":
            return _cookie_expiry(cookie)
        if provider == "riskbird" and cookie.name == "token":
            return _jwt_expiry(cookie.value or "")
        if provider == "kuaicha" and cookie.name == "sess_tk":
            return _jwt_expiry(cookie.value or "")
    return None


async def _close_session(session: QrSession) -> None:
    await session.client.aclose()


async def start_qr(provider: str) -> dict[str, Any]:
    if provider == "aiqicha":
        return await _start_aiqicha()
    if provider == "riskbird":
        return await _start_riskbird()
    if provider == "kuaicha":
        return await _start_kuaicha()
    raise ValueError("不支持的数据源")


async def _start_aiqicha() -> dict[str, Any]:
    client = make_client(
        timeout=20,
        follow_redirects=False,
        headers={
            "User-Agent": BAIDU_UA,
            "Referer": "https://passport.baidu.com/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )
    try:
        response = await client.get("https://passport.baidu.com/v2/api/getqrcode", params={"lp": "pc"})
        response.raise_for_status()
        data = response.json()
        sign = str(data.get("sign") or "")
        raw_url = str(data.get("imgurl") or "")
        if not sign or not raw_url:
            raise RuntimeError("爱企查二维码参数获取失败")
        image_url = raw_url if raw_url.startswith("http") else f"https://{raw_url}"
        image = await client.get(image_url)
        if image.status_code != 200 or not image.content:
            raise RuntimeError("爱企查二维码图片获取失败")
        session = QrSession(
            provider="aiqicha",
            session_id=secrets.token_urlsafe(24),
            created_at=_now(),
            payload={
                "sign": sign,
                "gid": uuid.uuid4().hex.upper(),
                "callback": f"tangram_guid_{secrets.randbits(32)}",
            },
            client=client,
        )
        _sessions[session.key] = session
        return {
            "session_id": session.session_id,
            "image_base64": base64.b64encode(image.content).decode(),
            "expires_in": QR_TTL,
        }
    except Exception:
        await client.aclose()
        raise


async def _start_riskbird() -> dict[str, Any]:
    client = make_client(
        base_url="https://www.riskbird.com",
        timeout=20,
        headers={
            "User-Agent": BAIDU_UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.riskbird.com",
            "Referer": "https://www.riskbird.com/",
            "app-device": "WEB",
        },
    )
    try:
        home = await client.get("/")
        home.raise_for_status()
        qr_uuid = uuid.uuid4().hex.upper()
        image = await client.get(
            "/riskbird-api/createQrCode",
            params={"uuid": qr_uuid},
        )
        if image.status_code != 200 or not image.content:
            raise RuntimeError("风鸟二维码图片获取失败")
        session = QrSession(
            provider="riskbird",
            session_id=secrets.token_urlsafe(24),
            created_at=_now(),
            payload={"uuid": qr_uuid},
            client=client,
        )
        _sessions[session.key] = session
        return {
            "session_id": session.session_id,
            "image_base64": base64.b64encode(image.content).decode(),
            "expires_in": QR_TTL,
        }
    except Exception:
        await client.aclose()
        raise


async def _start_kuaicha() -> dict[str, Any]:
    login_referer = (
        "https://upass.kuaicha365.com/login?"
        "isIframe=1&source=qdc_web&main=11&detail=3&pannel=2&redir="
    )
    client = make_client(
        base_url="https://upass.kuaicha365.com",
        timeout=20,
        headers={
            "User-Agent": BAIDU_UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://upass.kuaicha365.com",
            "Referer": login_referer,
        },
    )
    try:
        code = await client.get("/scan/creatCode")
        code.raise_for_status()
        data = code.json()
        qrid = str(data.get("qrid") or "")
        if not qrid:
            raise RuntimeError("快查二维码参数获取失败")
        image = await client.get("/scan/creatImg", params={"qrid": qrid, "source": "qdc_web"})
        if image.status_code != 200 or not image.content:
            raise RuntimeError("快查二维码图片获取失败")
        session = QrSession(
            provider="kuaicha",
            session_id=secrets.token_urlsafe(24),
            created_at=_now(),
            payload={"qrid": qrid, "state": 1},
            client=client,
        )
        _sessions[session.key] = session
        return {
            "session_id": session.session_id,
            "image_base64": base64.b64encode(image.content).decode(),
            "expires_in": QR_TTL,
        }
    except Exception:
        await client.aclose()
        raise


async def poll_qr(provider: str, session_id: str) -> QrPollResult:
    session = _sessions.get(f"{provider}:{session_id}")
    if session is None:
        raise KeyError("登录会话不存在或已过期")
    if session.expired():
        _sessions.pop(session.key, None)
        await _close_session(session)
        return QrPollResult(status="expired")
    try:
        if provider == "aiqicha":
            return await _poll_aiqicha(session)
        if provider == "riskbird":
            return await _poll_riskbird(session)
        if provider == "kuaicha":
            return await _poll_kuaicha(session)
        _sessions.pop(session.key, None)
        await _close_session(session)
        return QrPollResult(status="failed")
    except httpx.TimeoutException:
        return QrPollResult(status="pending")
    except Exception:
        _sessions.pop(session.key, None)
        await _close_session(session)
        return QrPollResult(status="failed")


def _decode_jsonp(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    start, end = stripped.find("("), stripped.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("百度扫码轮询返回了无法解析的内容")
    return json.loads(stripped[start + 1 : end])


def _baidu_channel_status(channel_raw: Any) -> tuple[int, str, str]:
    channel = channel_raw
    if isinstance(channel, str):
        try:
            channel = json.loads(channel)
        except (TypeError, ValueError):
            channel = {}
    if not isinstance(channel, dict):
        return -1, "", ""
    status = int(channel.get("status", -1))
    value = str(channel.get("v") or "")
    redirect = str(channel.get("u") or "")
    return status, value, redirect


async def _poll_aiqicha(session: QrSession) -> QrPollResult:
    params = {
        "channel_id": session.payload["sign"],
        "qrloginfrom": "pc",
        "gid": session.payload["gid"],
        "callback": session.payload["callback"],
        "apiver": "v3",
        "tt": str(int(time.time() * 1000)),
        "tpl": "pc",
    }
    response = await session.client.get(
        "https://passport.baidu.com/channel/unicast",
        params=params,
        timeout=QR_POLL_TIMEOUT,
    )
    response.raise_for_status()
    text = response.text.strip()
    if not text:
        return QrPollResult(status="pending")
    data = _decode_jsonp(text)
    status, bduss, redirect = _baidu_channel_status(data.get("channel_v"))
    if status == 1:
        return QrPollResult(status="scanned")
    if status != 0:
        return QrPollResult(status="pending")
    if not bduss:
        return QrPollResult(status="failed")
    result = await _exchange_baidu(session, bduss, redirect)
    if result.status == "success":
        _sessions.pop(session.key, None)
        await _close_session(session)
    return result


async def _exchange_baidu(session: QrSession, bduss: str, redirect: str = "") -> QrPollResult:
    now = str(int(time.time() * 1000))
    params = {
        "bduss": bduss,
        "u": redirect or "https://passport.baidu.com/",
        "tpl": "pc",
        "v": now,
        "tt": now,
        "loginVersion": "v4",
        "qrcode": "1",
        "apiver": "v3",
    }
    response = await session.client.get(
        "https://passport.baidu.com/v3/login/main/qrbdusslogin",
        params=params,
        follow_redirects=True,
        timeout=20,
    )
    response.raise_for_status()
    cookie = _cookie_string(session.client)
    if not cookie or "BDUSS" not in cookie:
        return QrPollResult(status="failed")
    return QrPollResult(status="success", cookie=cookie, expires_at=_auth_cookie_expiry(session.client, session.provider))


async def _poll_riskbird(session: QrSession) -> QrPollResult:
    response = await session.client.post(
        "/api/auth/login",
        json={"type": "qrcode", "qrCode": session.payload["uuid"]},
        timeout=QR_POLL_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return QrPollResult(status="failed")
    code = int(data.get("code") or 0)
    if code == 20000:
        try:
            info = await session.client.get("/riskbird-api/user/getInfo", timeout=10)
            info.raise_for_status()
        except Exception:
            _sessions.pop(session.key, None)
            await _close_session(session)
            return QrPollResult(status="failed")
        cookie = _cookie_string(session.client)
        if not cookie:
            return QrPollResult(status="failed")
        _sessions.pop(session.key, None)
        await _close_session(session)
        return QrPollResult(status="success", cookie=cookie, expires_at=_auth_cookie_expiry(session.client, session.provider))
    if code == 10000016:
        return QrPollResult(status="scanned")
    if code == 10000014:
        _sessions.pop(session.key, None)
        await _close_session(session)
        return QrPollResult(status="expired")
    if code == 10000015:
        return QrPollResult(status="pending")
    return QrPollResult(status="failed")


async def _poll_kuaicha(session: QrSession) -> QrPollResult:
    response = await session.client.post(
        "/scan/getInfoNew",
        data={
            "qrid": session.payload["qrid"],
            "state": session.payload.get("state", 1),
            "source": "pc_web",
            "page_source": "web_screen",
            "request_type": "login",
        },
        timeout=QR_POLL_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return QrPollResult(status="failed")
    status = int(data.get("status") or 0)
    if session.payload.get("last_status") != status:
        logger.warning(
            "kuaicha qr %s state %s -> %s",
            session.payload["qrid"],
            session.payload.get("state", 1),
            data,
        )
        session.payload["last_status"] = status
    if status == 0:
        _sessions.pop(session.key, None)
        await _close_session(session)
        return QrPollResult(status="failed")
    if status == 1:
        return QrPollResult(status="pending")
    if status == 2:
        session.payload["state"] = 2
        return QrPollResult(status="pending")
    if status == 3:
        try:
            await session.client.get("http://www.10jqka.com.cn", timeout=10)
        except httpx.HTTPError:
            pass
        cookie = _cookie_string(session.client)
        _sessions.pop(session.key, None)
        await _close_session(session)
        if not cookie:
            return QrPollResult(status="failed")
        return QrPollResult(status="success", cookie=cookie, expires_at=_auth_cookie_expiry(session.client, session.provider))
    return QrPollResult(status="failed")


async def cancel_qr(provider: str, session_id: str) -> None:
    session = _sessions.pop(f"{provider}:{session_id}", None)
    if session is not None:
        await _close_session(session)
