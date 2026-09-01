from __future__ import annotations

import json
import re
import time

import aiohttp
import quickjs

HOME_URL = "https://beian.miit.gov.cn/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.41 "
    "Safari/537.36 Edg/101.0.1210.32"
)
CACHE_TTL_SECONDS = 600

_script_re = re.compile(r"<script>(.*?)</script>", re.S)
_cache: dict[str, tuple[float, str]] = {}
_ignored_cookie_attrs = {"max-age", "path", "samesite", "secure", "httponly"}


def _cookie_dict(cookie: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        key = key.strip()
        if key.lower() in _ignored_cookie_attrs:
            continue
        result[key] = value.strip()
    return result


def _join_cookies(cookies: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def _eval_script(script: str, current_cookie: str) -> str:
    context = quickjs.Context()
    prelude = f"""
var document={{cookie:{json.dumps(current_cookie)}, createElement:function(){{return {{style:{{}},setAttribute:function(){{}}}}}}, getElementById:function(){{return null}}, querySelector:function(){{return null}}, body:{{appendChild:function(){{}}}}}};
var location={{href:'',pathname:'/',search:'',reload:function(){{}}}};
var navigator={{userAgent:{json.dumps(USER_AGENT)}}};
var setTimeout=function(fn){{fn();return 0;}}, clearTimeout=function(){{}}, setInterval=function(){{}}, clearInterval=function(){{}};
var window=globalThis;
"""
    context.eval(prelude)
    context.eval(script)
    return context.eval("document.cookie")


async def solve_clearance(proxy: str = "") -> str:
    now = time.time()
    cached = _cache.get("clearance")
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    cookies: dict[str, str] = {}
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    async with aiohttp.ClientSession() as session:
        for _ in range(5):
            if cookies:
                headers["Cookie"] = _join_cookies(cookies)
            async with session.get(HOME_URL, headers=headers, proxy=proxy or None, timeout=15) as response:
                text = await response.text()
                set_cookie = response.headers.get("set-cookie")
                if set_cookie:
                    cookies.update(_cookie_dict(set_cookie))
                if response.status == 200:
                    result = _join_cookies(cookies)
                    _cache["clearance"] = (now, result)
                    return result
                scripts = _script_re.findall(text)
                if not scripts:
                    break
                try:
                    doc_cookie = _eval_script(scripts[0], _join_cookies(cookies))
                    cookies.update(_cookie_dict(doc_cookie))
                except Exception:
                    break

    result = _join_cookies(cookies)
    if result:
        _cache["clearance"] = (now, result)
    return result
