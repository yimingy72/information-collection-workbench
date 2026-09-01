# -*- coding: utf-8 -*-
"""Single-query routes with an allow-listed internal SeaMoon proxy option."""
import asyncio
import os
import re

from aiohttp import web

from load_config import config
from middlewares import jsondump, wj
from mlog import logger


routes = web.RouteTableDef()
ALLOWED_PROXY = os.getenv("SEAMOON_PROXY_URL", "http://seamoon-gateway:19080").strip()
SESSION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


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
    if proxy and proxy != ALLOWED_PROXY:
        return wj({"code": 101, "msg": "不允许的代理地址"})

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
            return wj(result)
        if result.get("message", "") == "当前访问已被创宇盾拦截":
            logger.warning("当前访问已被创宇盾拦截")
            return wj(result)
        await asyncio.sleep(1)
    return wj(result)


def setup_query_routes(app):
    app.add_routes(routes)
