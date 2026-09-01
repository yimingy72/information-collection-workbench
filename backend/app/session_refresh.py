from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.repository import Repository
from app.runtime import make_client

logger = logging.getLogger(__name__)

REFRESH_MARGIN_SECONDS = 5 * 60
RISKBIRD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.60 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.riskbird.com",
    "Referer": "https://www.riskbird.com/",
    "app-device": "WEB",
}


def _cookie_value(cookie: str, name: str) -> str:
    for part in cookie.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            if key == name:
                return value
    return ""


def _replace_cookie(cookie: str, name: str, value: str) -> str:
    parts: list[str] = []
    replaced = False
    for part in cookie.split(";"):
        part = part.strip()
        if not part:
            continue
        if part.startswith(f"{name}="):
            parts.append(f"{name}={value}")
            replaced = True
        else:
            parts.append(part)
    if not replaced:
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def _jwt_expiry(cookie: str, name: str) -> datetime | None:
    token = _cookie_value(cookie, name)
    if not token:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = int(payload.get("exp") or 0)
        if exp:
            return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        pass
    return None


async def _refresh_riskbird(cookie: str) -> tuple[str, datetime | None] | None:
    client = make_client(
        base_url="https://www.riskbird.com",
        timeout=20,
        cookie=cookie,
        headers=RISKBIRD_HEADERS,
    )
    try:
        response = await client.post("/riskbird-api/user/refreshToken", json={})
        response.raise_for_status()
        data = response.json()
        token = ((data.get("data") or {}).get("data") or {}).get("token") or ""
        if not token:
            return None
        refreshed = _replace_cookie(cookie, "token", token)
        return refreshed, _jwt_expiry(refreshed, "token")
    finally:
        await client.aclose()


async def refresh_provider_sessions(repo: Repository, provider_ids: list[str] | None = None) -> dict[str, Any]:
    config = await repo.get_runtime_config()
    sessions = config.get("sessions") or {}
    now = datetime.now(timezone.utc)
    for provider in provider_ids or ["riskbird"]:
        row = sessions.get(provider) or {}
        cookie = str(row.get("cookie") or "").strip()
        if not cookie:
            continue
        expires = row.get("expires_at")
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires > now + timedelta(seconds=REFRESH_MARGIN_SECONDS):
                continue
        if provider == "riskbird":
            result = await _refresh_riskbird(cookie)
            if result is None:
                continue
            new_cookie, new_expiry = result
            await repo.upsert_provider_session("riskbird", new_cookie, new_expiry)
            logger.info("riskbird session refreshed, expires %s", new_expiry)
    return await repo.get_runtime_config()
