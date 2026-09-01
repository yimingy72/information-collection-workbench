from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

SESSION_PROVIDERS = ("aiqicha", "kuaicha", "riskbird")


def is_retryable_network_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return any(token in text for token in (
        "timed out",
        "timeout",
        "connection reset",
        "server disconnected",
        "503",
        "service unavailable",
    ))


def merge_cookie(*parts: str) -> str:
    values: list[str] = []
    for part in parts:
        text = " ".join(str(part or "").replace("\n", " ").split()).strip().strip(";")
        if text:
            values.append(text)
    return "; ".join(values)


def cookie_active(session: dict[str, Any] | None) -> bool:
    if not session:
        return False
    if not str(session.get("cookie") or "").strip():
        return False
    expires = session.get("expires_at")
    if expires is None:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)


def session_cookie(config: dict[str, Any], provider_id: str) -> str:
    session = (config.get("sessions") or {}).get(provider_id)
    if not cookie_active(session):
        return ""
    return str(session.get("cookie") or "").strip()


def make_client(
    *,
    headers: dict[str, str],
    timeout: float,
    cookie: str = "",
    base_url: str | None = None,
    follow_redirects: bool = True,
    proxy: str = "",
) -> httpx.AsyncClient:
    request_headers = dict(headers)
    merged = merge_cookie(cookie)
    if merged:
        request_headers["Cookie"] = merged
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "headers": request_headers,
        "follow_redirects": follow_redirects,
        "trust_env": False,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


def session_login_gaps(provider_ids: list[str], config: dict[str, Any]) -> list[str]:
    return [
        provider_id
        for provider_id in provider_ids
        if provider_id in SESSION_PROVIDERS and not session_cookie(config, provider_id)
    ]
