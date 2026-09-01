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

PID_MAPS = {
    1: str.maketrans("0123456789", "0123547698"),
    2: str.maketrans("0123456789", "0123689457"),
}


def _decode_pid(value: str, ddw: Any) -> str:
    try:
        table = PID_MAPS.get(int(ddw or 0))
    except (TypeError, ValueError):
        table = None
    if not table:
        return str(value)
    return str(value).translate(table)


class AnonymousAiqicha:
    """Aiqicha equity reader. Cookie is sent only when configured."""

    id = "aiqicha"
    label = "爱企查"
    page_size = 10

    def __init__(self, timeout: float = 20.0, cookie: str = "", proxy: str = "") -> None:
        self._timeout = timeout
        self._cookie = cookie
        self._proxy = proxy
        self.client = self._make_client()

    def _make_client(self):
        return make_client(
            timeout=self._timeout,
            follow_redirects=False,
            cookie=self._cookie,
            proxy=self._proxy,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.43"
                ),
                "Accept": "text/html, application/xhtml+xml, image/jxr, */*",
                "Referer": "https://aiqicha.baidu.com/",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _raise_for_challenge(self, response) -> None:
        location = response.headers.get("location", "")
        text_head = response.text[:200]
        if (
            response.status_code in {301, 302, 303, 307, 308}
            or "wappass.baidu.com" in location
            or "tuxing" in location
            or "百度安全验证" in text_head
        ):
            raise ProviderError("爱企查需要安全验证，无法查询")
        if response.status_code in {401, 403, 429}:
            raise ProviderError(f"爱企查拒绝访问 (HTTP {response.status_code})")

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.get(url, params=params)
                self.client.cookies.clear()
                self._raise_for_challenge(response)
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                last_error = exc if isinstance(exc, ProviderError) else ProviderError(str(exc))
                if is_retryable_network_error(exc) and attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                break
            if not isinstance(data, dict):
                last_error = ProviderError("爱企查返回了无效响应")
                break
            status = data.get("status")
            if status not in (None, 0, "0", 200, "200"):
                last_error = ProviderError(str(data.get("msg") or data.get("message") or "爱企查查询失败"))
                break
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            user_type = ((inner.get("limitForward") or {}) if isinstance(inner.get("limitForward"), dict) else {}).get("userType")
            if user_type == "nologin" and not (inner.get("list") or inner.get("resultList")):
                last_error = ProviderError("爱企查未登录且无结果")
                break
            return data
        raise last_error or ProviderError("爱企查查询失败")

    async def search(self, keyword: str) -> tuple[Company, list[Company]]:
        data = await self._get(
            "https://aiqicha.baidu.com/s/advanceFilterAjax",
            params={"q": keyword, "p": "1", "s": "10", "f": "{}"},
        )
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        rows = inner.get("resultList") or inner.get("list") or []
        ddw = data.get("ddw")
        candidates = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("pid") or item.get("entid") or "")
            external_id = _decode_pid(raw_id, ddw)
            name = clean_text(str(item.get("entName") or item.get("name") or ""))
            if external_id and name:
                payload = dict(item)
                payload["pid"] = external_id
                candidates.append(Company(external_id, name, payload))
        if not candidates:
            raise ProviderError(f"爱企查没有匹配到 {keyword!r}")
        selected = next((item for item in candidates if normalize_name(item.name) == normalize_name(keyword)), candidates[0])
        return selected, candidates

    def _parse_investments(self, data: dict[str, Any]) -> tuple[list[Investment], int]:
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        record = payload.get("investRecordData")
        if isinstance(record, dict):
            rows = record.get("list") or []
            total = int(record.get("total") or 0)
        else:
            rows = payload.get("list") or []
            total = int(payload.get("total") or 0)
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("entName") or row.get("name") or ""))
            child_id = str(row.get("pid") or row.get("yid") or row.get("entid") or "")
            if name and child_id:
                values.append(Investment(name, child_id, _number(row.get("regRate") or row.get("proportion")), row))
        return values, total or len(values)

    async def investments(self, external_id: str, page: int = 1) -> tuple[list[Investment], int]:
        params = {"pid": external_id, "p": str(page), "size": str(self.page_size)}
        try:
            data = await self._get("https://aiqicha.baidu.com/relations/stockchartAjax", params=params)
            return self._parse_investments(data)
        except ProviderError as exc:
            if page != 1 or "disconnected" not in str(exc).casefold():
                raise
            data = await self._get("https://aiqicha.baidu.com/relations/relationalMapAjax", params={"pid": external_id})
            return self._parse_investments(data)

    async def shareholders(self, external_id: str, page: int = 1) -> tuple[list[Shareholder], int]:
        data = await self._get(
            "https://aiqicha.baidu.com/detail/sharesAjax",
            params={"pid": external_id, "p": str(page), "size": str(self.page_size)},
        )
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        rows = payload.get("list") or []
        values = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = clean_text(str(row.get("name") or row.get("entName") or ""))
            if name:
                values.append(Shareholder(name, _number(row.get("subRate")), row))
        return values, int(payload.get("total") or len(values))

    async def all_pages(self, fetch, page_size: int = 10, max_pages: int = 50):
        rows: list = []
        for page in range(1, max_pages + 1):
            chunk, total = await fetch(page)
            rows.extend(chunk)
            if not chunk or len(chunk) < page_size or (total and len(rows) >= total):
                return rows
        raise ProviderError(f"爱企查分页超过 {max_pages} 页")
