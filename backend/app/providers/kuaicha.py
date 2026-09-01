from __future__ import annotations

import asyncio
from typing import Any

from app.providers.tianyancha import (
    Company,
    Investment,
    ProviderError,
    Shareholder,
    _number,
    clean_text,
    normalize_name,
)
from app.runtime import is_retryable_network_error, make_client


class AnonymousKuaicha:
    """Kuaicha equity reader. No Cookie header is sent."""

    id = "kuaicha"
    label = "快查"
    page_size = 10

    def __init__(self, timeout: float = 20.0, cookie: str = "", proxy: str = "") -> None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.60 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Source": "PC",
            "Origin": "https://www.kuaicha365.com",
            "Referer": "https://www.kuaicha365.com/search-result?",
        }
        self.client = make_client(
            base_url="https://www.kuaicha365.com",
            timeout=timeout,
            cookie=cookie,
            headers=headers,
            proxy=proxy,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.request(method, path, **kwargs)
            except Exception as exc:
                last_error = ProviderError(str(exc))
                if is_retryable_network_error(exc) and attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                break
            self.client.cookies.clear()
            if response.status_code in {401, 403}:
                raise ProviderError(f"快查拒绝访问 (HTTP {response.status_code})")
            if response.status_code == 429:
                last_error = ProviderError("请求过于频繁，请稍候重试")
                if attempt == 0:
                    await asyncio.sleep(0.15)
                continue
            try:
                response.raise_for_status()
                data = response.json()
            except ValueError as exc:
                raise ProviderError("快查返回了非 JSON") from exc
            except Exception as exc:
                last_error = ProviderError(str(exc))
                continue
            if not isinstance(data, dict):
                last_error = ProviderError("快查返回了无效响应")
                continue
            code = data.get("status_code")
            if code not in (None, 0, 200, 2000):
                message = str(data.get("status_msg") or f"快查查询失败 ({code})")
                last_error = ProviderError(message)
                if "过于频繁" in message or code in {4001, "4001"}:
                    continue
                raise last_error
            return data
        raise last_error or ProviderError("快查查询失败")

    async def search(self, keyword: str) -> tuple[Company, list[Company]]:
        data = await self._request(
            "POST",
            "/enterprise_info_app/V1/search/company_search_pc",
            json={"search_conditions": [], "keyword": keyword, "page": 1, "page_size": 20},
        )
        rows = ((data.get("data") or {}).get("list") if isinstance(data.get("data"), dict) else []) or []
        candidates = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("org_id") or item.get("orgid") or item.get("id") or "")
            name = clean_text(str(item.get("org_name") or item.get("name") or item.get("company_name") or ""))
            if external_id and name:
                candidates.append(Company(external_id, name, item))
        if not candidates:
            raise ProviderError(f"快查没有匹配到 {keyword!r}")
        selected = next((item for item in candidates if normalize_name(item.name) == normalize_name(keyword)), candidates[0])
        return selected, candidates

    async def investments(self, external_id: str, page: int = 1) -> tuple[list[Investment], int]:
        data = await self._request(
            "GET",
            "/open/app/v1/pc_enterprise/invest_abroad/list",
            params={"page_size": self.page_size, "org_id": external_id, "is_latest": "1", "page": page},
        )
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        rows = payload.get("list") or []
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("frgn_invest_corp_name") or row.get("name") or ""))
            child_id = str(row.get("frgn_invest_corp_id") or row.get("org_id") or "")
            if name and child_id:
                values.append(Investment(name, child_id, _number(row.get("invest_ratio")), row))
        return values, int(payload.get("total") or len(values))

    async def shareholders(self, external_id: str, page: int = 1) -> tuple[list[Shareholder], int]:
        data = await self._request(
            "GET",
            "/open/app/v1/pc_enterprise/shareholder/latest_announcement",
            params={"page_size": self.page_size, "org_id": external_id, "page": page},
        )
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        rows = payload.get("list") or []
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("shareholder_name") or row.get("name") or ""))
            if name:
                values.append(Shareholder(name, _number(row.get("shareholding_ratio")), row))
        return values, int(payload.get("total") or len(values))

    async def all_pages(self, fetch, page_size: int = 10, max_pages: int = 50):
        rows: list = []
        for page in range(1, max_pages + 1):
            chunk, _ = await fetch(page)
            rows.extend(chunk)
            if len(chunk) < page_size:
                return rows
        raise ProviderError(f"快查分页超过 {max_pages} 页")
