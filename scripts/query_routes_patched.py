# -*- coding: utf-8 -*-
"""Single-query routes with an allow-listed internal SeaMoon proxy option."""
import asyncio
import os
import re
from urllib.parse import urlsplit

from aiohttp import web

from load_config import config
from middlewares import jsondump, wj
from mlog import logger


routes = web.RouteTableDef()
ALLOWED_PROXY = os.getenv("SEAMOON_PROXY_URL", "http://seamoon-gateway:19080").strip()
PROXY_CONTROL_TOKEN = os.getenv("SEAMOON_PROXY_CONTROL_TOKEN", "asset-workbench-local").strip()
SESSION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# Keep the YMICP handler just below the backend cold-page timeout (90s).
# Cold SeaMoon sessions need JSL + captcha + query; 48s still 504ed the first
# handshake and forced every lane into a timeout/retry storm.
QUERY_HARD_TIMEOUT_SECONDS = float(os.getenv("YMICP_QUERY_HARD_TIMEOUT_SECONDS", "88"))
_INFLIGHT_QUERIES: dict[tuple[str, str, str, int], asyncio.Task] = {}
_INFLIGHT_LOCK = asyncio.Lock()


def _valid_proxy_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https", "socks4", "socks5", "socks"} and bool(parsed.hostname) and bool(parsed.port)


@jsondump
@routes.view(r'/query/{path}')
async def geturl(request):
    path = request.match_info['path']
    appth = request.app.get('appth', {})
    bappth = request.app.get('bappth', {})

    if path not in appth and path not in bappth:
        return wj({"code": 102, "msg": "不是支持的查询类型"})
    if path not in config.risk_avoidance.allow_type:
        return wj({"code": 102, "msg": "不是支持的查询类型"})

    if request.method == "GET":
        appname = request.query.get("search")
        page_num = request.query.get("pageNum")
        page_size = request.query.get("pageSize")
        session_key = request.query.get("sessionKey", "").strip()
    else:
        data = await request.json()
        appname = data.get("search")
        page_num = data.get("pageNum")
        page_size = data.get("pageSize")
        session_key = str(data.get("sessionKey") or "").strip()

    if not appname:
        return wj({"code": 101, "msg": "参数错误,请指定search参数"})
    if path in appth:
        try:
            page_num = max(1, int(page_num or 1))
            page_size = max(1, min(26, int(page_size or 10)))
        except (TypeError, ValueError):
            return wj({"code": 101, "msg": "分页参数无效"})
    if any(appname.endswith(suffix) for suffix in config.risk_avoidance.prohibit_suffix):
        return wj({"code": 405, "message": "不允许的查询内容"})
    if session_key and not SESSION_KEY_RE.fullmatch(session_key):
        return wj({"code": 101, "msg": "分页会话标识无效"})

    proxy = request.query.get("proxy", "").strip()
    if proxy:
        if not _valid_proxy_url(proxy):
            return wj({"code": 101, "msg": "代理地址无效"})
        # The backend may pass a tested manual proxy. Keep the existing
        # SeaMoon allow-list for ordinary callers, while requiring an
        # internal control token for manually configured routes.
        allowed_host = urlsplit(ALLOWED_PROXY).hostname
        proxy_host = urlsplit(proxy).hostname
        same_gateway = bool(allowed_host) and proxy_host == allowed_host and urlsplit(proxy).port == urlsplit(ALLOWED_PROXY).port
        if proxy != ALLOWED_PROXY and not same_gateway and request.headers.get("X-Workbench-Proxy-Token", "") != PROXY_CONTROL_TOKEN:
            return wj({"code": 101, "msg": "不允许的代理地址"})

    async def run_query():
        result = None
        for _ in range(config.captcha.retry_times):
            if path in appth:
                kwargs = {"proxy": proxy}
                if path == "web" and session_key:
                    kwargs["session_key"] = session_key
                result = await appth[path](appname, page_num, page_size, **kwargs)
            else:
                result = await bappth[path](appname, proxy=proxy)

            if result.get("code", 500) == 200:
                save_history = (
                    getattr(config, 'history', None)
                    and getattr(config.history, 'save_query_history', True)
                )
                if save_history:
                    db = request.app.get("db")
                    if db:
                        result_count = (
                            len(result.get("params", {}).get("list", []))
                            if path in appth
                            else len(result.get("params", []))
                        )
                        db.add_history(path, appname, result_count, result.get("params"))
                return result
            if result.get("message", "") == "当前访问已被创宇盾拦截":
                logger.warning("当前访问已被创宇盾拦截")
                return result
            await asyncio.sleep(1)
        return result

    # Do not cancel an in-flight JSL/captcha handshake just because the HTTP
    # caller hit 34s. A later retry for the same lane/company/page waits on
    # that task instead of starting a second query that trips 创宇盾.
    inflight_key = (session_key or "", path, str(appname), int(page_num or 1) if path in appth else 1)
    async with _INFLIGHT_LOCK:
        task = _INFLIGHT_QUERIES.get(inflight_key)
        if task is None or task.done():
            task = asyncio.create_task(run_query())
            _INFLIGHT_QUERIES[inflight_key] = task
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=max(0.1, QUERY_HARD_TIMEOUT_SECONDS))
    except TimeoutError:
        logger.warning(
            "ICP page timed out after %.1fs: %s page=%s; handshake continues on %s",
            QUERY_HARD_TIMEOUT_SECONDS,
            appname,
            page_num,
            session_key or "no-session",
        )
        return wj({"code": 504, "message": "ICP 页面请求超时"})
    except Exception:
        if not task.done():
            task.cancel()
        raise
    finally:
        async with _INFLIGHT_LOCK:
            current = _INFLIGHT_QUERIES.get(inflight_key)
            if current is task and task.done():
                _INFLIGHT_QUERIES.pop(inflight_key, None)
    return wj(result)


def setup_query_routes(app):
    app.add_routes(routes)
