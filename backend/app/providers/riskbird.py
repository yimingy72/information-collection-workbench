from __future__ import annotations

import asyncio
from typing import Any

import httpx

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


class AnonymousRiskbird:
    """Riskbird equity reader. Cookie is sent only when configured."""

    id = "riskbird"
    label = "风鸟"
    page_size = 100

    def __init__(self, timeout: float = 20.0, cookie: str = "", proxy: str = "") -> None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.60 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "App-Device": "WEB",
            "Origin": "https://www.riskbird.com",
            "Referer": "https://www.riskbird.com/ent/",
        }
        self.client = make_client(
            base_url="https://www.riskbird.com",
            timeout=timeout,
            cookie=cookie,
            headers=headers,
            proxy=proxy,
        )
        self._orders: dict[str, str] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        extra_headers = kwargs.pop("headers", None)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.request(method, path, headers=extra_headers, **kwargs)
            except Exception as exc:
                last_error = ProviderError(str(exc))
                if is_retryable_network_error(exc) and attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                break
            self.client.cookies.clear()
            if response.status_code in {401, 403}:
                raise ProviderError(f"风鸟拒绝访问 (HTTP {response.status_code})")
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = ProviderError(f"风鸟服务暂时不可用 (HTTP {response.status_code})")
                if attempt == 0:
                    await asyncio.sleep(0.15)
                continue
            try:
                response.raise_for_status()
                data = response.json()
            except Exception as cop:
                last_error = ProviderError(str(cop))
                continue
            if not isinstance(data, dict):
                last_error = ProviderError("风鸟返回了无效响应")
                continue
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            state = inner.get("state")
            if state == "limit:tourist":
                raise ProviderError("风鸟游客访问已达上限，无法查询")
            if state == "limit:auth":
                raise ProviderError("风鸟今日查询次数已达上限")
            if data.get("code") not in (None, 20000, 200, 0, "20000") and path != "/api/ent/query":
                last_error = ProviderError(str(data.get("msg") or inner.get("msg") or "风鸟查询失败"))
                continue
            return data
        raise last_error or ProviderError("风鸟查询失败")

    async def _order_no(self, entid: str) -> str:
        data = await self._request("GET", "/api/ent/query", params={"entId": entid})
        order = str(data.get("orderNo") or "")
        if not order and isinstance(data.get("basicResult"), dict):
            order = str(data["basicResult"].get("orderNo") or "")
        if not order and isinstance(data.get("detailResult"), dict):
            order = str(data["detailResult"].get("orderNo") or "")
        if not order:
            raise ProviderError("风鸟没有返回查询单号")
        return order

    async def search(self, keyword: str) -> tuple[Company, list[Company]]:
        data = await self._request(
            "POST",
            "/riskbird-api/newSearch",
            json={
                "searchKey": keyword,
                "pageNo": "1",
                "range": "10",
                "referer": "search",
                "queryType": "1",
                "selectConditionData": '{"status":"","sort_field":""}',
            },
        )
        rows = ((data.get("data") or {}).get("list") if isinstance(data.get("data"), dict) else []) or []
        candidates = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            entid = str(item.get("entid") or item.get("entId") or item.get("id") or "")
            name = clean_text(str(item.get("entName") or item.get("ENTNAME") or item.get("name") or ""))
            if entid and name:
                payload = dict(item)
                payload["entid"] = entid
                candidates.append(Company(entid, name, payload))
        if not candidates:
            raise ProviderError(f"风鸟没有匹配到 {keyword!r}")
        selected = next((item for item in candidates if normalize_name(item.name) == normalize_name(keyword)), candidates[0])
        order_no = await self._order_no(selected.external_id)
        selected.payload["orderNo"] = order_no
        self._orders[selected.external_id] = order_no
        return selected, candidates

    async def _list(self, external_id: str, extract_type: str, page: int) -> dict[str, Any]:
        order_no = self._orders.get(external_id) or external_id
        if not order_no.startswith("WEB"):
            order_no = await self._order_no(external_id)
            self._orders[external_id] = order_no
        return await self._request(
            "POST",
            "/riskbird-api/companyInfo/list",
            headers={"Xs-Content-Type": "application/json"},
            json={
                "filterCnd": "1",
                "page": str(page),
                "size": str(self.page_size),
                "orderNo": order_no,
                "extractType": extract_type,
                "sortField": "",
            },
        )

    async def investments(self, external_id: str, page: int = 1) -> tuple[list[Investment], int]:
        data = await self._list(external_id, "companyInvest", page)
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        rows = payload.get("apiData") or []
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("entName") or row.get("name") or ""))
            child_id = str(row.get("entid") or row.get("orderNo") or row.get("entId") or "")
            if name and child_id:
                values.append(Investment(name, child_id, _number(row.get("funderRatio") or row.get("fundedRatio")), row))
        return values, int(payload.get("totalCount") or len(values))

    async def shareholders(self, external_id: str, page: int = 1) -> tuple[list[Shareholder], int]:
        data = await self._list(external_id, "shareHolder", page)
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        rows = payload.get("apiData") or []
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("shaName") or row.get("name") or ""))
            if name:
                values.append(Shareholder(name, _number(row.get("fundedRatio")), row))
        return values, int(payload.get("totalCount") or len(values))

    async def all_pages(self, fetch, page_size: int = 100, max_pages: int = 50):
        rows: list = []
        for page in range(1, max_pages + 1):
            chunk, _ = await fetch(page)
            rows.extend(chunk)
            if len(chunk) < page_size:
                return rows
        raise ProviderError(f"风鸟分页超过 {max_pages} 页")
