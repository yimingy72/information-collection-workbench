from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx


SEARCH_URL = "/cloud-tempest/web/searchCompanyV4"
INVEST_URL = "/cloud-company-background/company/investListV2"
PARTNER_URL = "/cloud-company-background/companyV2/dim/holderForWeb"


def clean_text(value: str) -> str:
    return re.sub(r"</?em>", "", value)


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", clean_text(value)).casefold()


@dataclass(frozen=True)
class Company:
    external_id: str
    name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Investment:
    name: str
    external_id: str
    holding_percent: float | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class Shareholder:
    name: str
    holding_percent: float | None
    payload: dict[str, Any]


class ProviderError(RuntimeError):
    pass


def _provider_message(data: dict[str, Any] | None, fallback: str = "provider error") -> str:
    if not isinstance(data, dict):
        return fallback
    return str(data.get("message") or data.get("msg") or fallback)


def _is_login_required(message: str) -> bool:
    text = message.casefold()
    return any(token in text for token in ("请登录", "mustlogin", "需要登陆", "需要登录"))


class AnonymousTianyancha:
    """Tianyancha CAPI equity reader; no Cookie or auth headers are sent."""

    id = "tianyancha"
    label = "天眼查"

    def __init__(
        self,
        base_url: str,
        timeout: float = 20.0,
        retries: int = 2,
        proxy: str = "",
    ) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            trust_env=False,
            proxy=proxy or None,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.60 Safari/537.36"
                ),
                "Accept": "text/html,application/json,application/xhtml+xml, image/jxr, */*",
                "Version": "TYC-Web",
                "Content-Type": "application/json",
                "Origin": "https://www.tianyancha.com",
                "Referer": "https://www.tianyancha.com/",
            },
        )
        self.retries = retries

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.request(method, path, **kwargs)
                if response.status_code in {429, 433}:
                    raise ProviderError(f"Tianyancha rate limited the request (HTTP {response.status_code})")
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ProviderError("Tianyancha returned an invalid response")
                if data.get("state") not in (None, "ok"):
                    message = _provider_message(data)
                    raise ProviderError(message)
                return data
            except (httpx.HTTPError, ValueError, ProviderError) as exc:
                if isinstance(exc, ProviderError) and ("rate limited" in str(exc) or _is_login_required(str(exc))):
                    raise
                if attempt >= self.retries:
                    raise ProviderError(f"Tianyancha request failed: {exc}") from exc
                await asyncio.sleep(0.25 * (2**attempt))
        raise AssertionError("unreachable")

    async def search(self, keyword: str) -> tuple[Company, list[Company]]:
        data = await self._request(
            "POST",
            SEARCH_URL,
            json={
                "key": keyword,
                "pageNum": "1",
                "pageSize": "20",
                "referer": "search",
                "sortType": "0",
                "word": keyword,
            },
        )
        candidates = []
        for item in data.get("data", {}).get("companyList", []):
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("id") or item.get("graphId") or item.get("cid") or "")
            name = clean_text(str(item.get("name") or item.get("companyName") or ""))
            if external_id and name:
                candidates.append(Company(external_id, name, item))
        if not candidates:
            payload = data.get("data") if isinstance(data.get("data"), dict) else {}
            reason = payload.get("searchVersion") or data.get("message") or "no companyList"
            raise ProviderError(f"No Tianyancha company matched {keyword!r}: {reason}")
        normalized = normalize_name(keyword)
        selected = next((item for item in candidates if normalize_name(item.name) == normalized), candidates[0])
        return selected, candidates

    async def investments(self, external_id: str, page: int = 1) -> tuple[list[Investment], int]:
        data = await self._request(
            "POST",
            INVEST_URL,
            params={"_": str(int(time.time()))},
            json={
                "category": "-100",
                "percentLevel": "-100",
                "province": "-100",
                "gid": external_id,
                "pageSize": "100",
                "pageNum": str(page),
            },
        )
        payload = data.get("data", {})
        rows = payload.get("result", []) if isinstance(payload, dict) else []
        total = _first_int(payload, "itemTotal", "count", "total", "totalCount")
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("name") or row.get("companyName") or ""))
            child_id = str(row.get("id") or row.get("graphId") or row.get("cid") or "")
            if name and child_id:
                values.append(Investment(name, child_id, _number(row.get("percent")), row))
        return values, total

    async def shareholders(self, external_id: str, page: int = 1) -> tuple[list[Shareholder], int]:
        data = await self._request(
            "POST",
            PARTNER_URL,
            params={"_": str(int(time.time()))},
            json={
                "percentLevel": "-100",
                "sortField": "capitalAmount",
                "sortType": "-100",
                "gid": external_id,
                "pageSize": "100",
                "pageNum": str(page),
            },
        )
        payload = data.get("data", {})
        rows = payload.get("result", []) if isinstance(payload, dict) else []
        total = _first_int(payload, "itemTotal", "count", "total", "totalCount")
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("name") or row.get("holderName") or row.get("finalHolderName") or ""))
            if name:
                values.append(Shareholder(name, _number(row.get("finalBenefitShares") or row.get("percent")), row))
        return values, total

    async def all_pages(
        self,
        fetch: Callable[[int], Awaitable[tuple[list[Any], int]]],
        page_size: int = 100,
        max_pages: int = 50,
    ) -> list[Any]:
        rows: list[Any] = []
        total = 0
        for page in range(1, max_pages + 1):
            chunk, reported_total = await fetch(page)
            rows.extend(chunk)
            total = max(total, int(reported_total or 0))
            # Prefer the provider's total. Some endpoints cap pageSize below
            # the requested value, so a short page does not necessarily mean
            # the last page.
            if total and len(rows) >= total:
                return rows[:total]
            if not chunk:
                return rows
            if len(chunk) < page_size and not total:
                return rows
        raise ProviderError(f"Tianyancha pagination exceeded {max_pages} pages")


def _first_int(value: Any, *keys: str) -> int:
    if not isinstance(value, dict):
        return len(value) if isinstance(value, list) else 0
    for key in keys:
        try:
            number = int(value.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if number:
            return number
    return 0


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).rstrip("%"))
    except ValueError:
        return None
