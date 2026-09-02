from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.models import ServerlessProxyNodeView, ServerlessProxyTestResponse, ServerlessProxyView
from app.settings import settings

TEST_TARGET = "https://www.baidu.com/robots.txt"


class ServerlessProxyError(RuntimeError):
    pass


def _row(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("serverless_proxy") or config)


def _json_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    value = row.get("nodes") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = []
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _node_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or f"{row.get('provider') or 'aliyun'}:{row.get('region') or 'cn-hangzhou'}:{row.get('function_name') or 'asset-workbench-seamoon'}")


def _normalise_node(row: dict[str, Any]) -> dict[str, Any]:
    node = dict(row)
    node["id"] = _node_id(node)
    node["enabled"] = bool(node.get("enabled"))
    node["provider"] = str(node.get("provider") or "aliyun")
    node["endpoint"] = str(node.get("endpoint") or "").strip()
    node["region"] = str(node.get("region") or "cn-hangzhou")
    node["function_name"] = str(node.get("function_name") or "asset-workbench-seamoon")
    node["image_uri"] = str(node.get("image_uri") or "")
    node["access_key_id"] = str(node.get("access_key_id") or "")
    node["access_key_secret"] = str(node.get("access_key_secret") or "")
    node["insecure_skip_verify"] = bool(node.get("insecure_skip_verify"))
    node["deployment_id"] = str(node.get("deployment_id") or "")
    node["status"] = str(node.get("status") or ("ready" if node["endpoint"] else "not_configured"))
    node["last_error"] = str(node.get("last_error") or "")
    node["latency_ms"] = int(node["latency_ms"]) if node.get("latency_ms") is not None else None
    node["failure_count"] = int(node.get("failure_count") or 0)
    return node


def pool_nodes(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return persisted pool nodes, with a backwards-compatible legacy node."""
    row = _row(config)
    nodes = [_normalise_node(item) for item in _json_nodes(row)]
    if nodes:
        return nodes
    if str(row.get("endpoint") or "").strip():
        return [_normalise_node(row)]
    return []


def upsert_pool_node(config: dict[str, Any], node: dict[str, Any]) -> list[dict[str, Any]]:
    incoming = _normalise_node(node)
    nodes = pool_nodes(config)
    replaced = False
    result: list[dict[str, Any]] = []
    for item in nodes:
        if item["id"] == incoming["id"]:
            result.append({**item, **incoming})
            replaced = True
        else:
            result.append(item)
    if not replaced:
        result.append(incoming)
    return result


def remove_pool_node(config: dict[str, Any], node_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    nodes = pool_nodes(config)
    removed = next((item for item in nodes if item["id"] == node_id), None)
    return removed, [item for item in nodes if item["id"] != node_id]


def manual_proxy_url(row: dict[str, Any]) -> str:
    scheme = str(row.get("scheme") or "http").strip().lower()
    host = str(row.get("host") or "").strip()
    port = int(row.get("port") or 0)
    username = str(row.get("username") or "")
    password = str(row.get("password") or "")
    if not host or not port:
        return ""
    auth = ""
    if username or password:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return urlunsplit((scheme, f"{auth}{host}:{port}", "", "", ""))


def manual_proxy_urls(config: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    # Runtime config contains both ``serverless_proxy`` and ``manual_proxies``.
    # _row() intentionally unwraps the former, so do not use it here or the
    # manual pool would silently disappear from real query execution.
    values = config.get("manual_proxies") if "manual_proxies" in config else _row(config).get("manual_proxies")
    values = values or []
    if not isinstance(values, list):
        return urls
    for item in values:
        if not isinstance(item, dict) or not item.get("enabled") or item.get("status") != "ready":
            continue
        value = manual_proxy_url(item)
        if value and value not in urls:
            urls.append(value)
    return urls


def _manual_or_cloud_routes(config: dict[str, Any], cloud_route: str) -> list[str]:
    # Manual routes are an explicit operator choice. Once at least one tested
    # manual proxy is ready, use that pool exclusively; the cloud gateway is
    # only the fallback when no manual route is available. This prevents one
    # query from silently mixing direct HTTP proxies and SeaMoon tunnels.
    manual = manual_proxy_urls(config)
    if manual:
        return manual
    return [cloud_route] if _gateway_endpoints(config) else []


def active_proxy_urls(config: dict[str, Any]) -> list[str]:
    return _manual_or_cloud_routes(config, settings.serverless_proxy_url)


def miit_proxy_urls(config: dict[str, Any]) -> list[str]:
    return _manual_or_cloud_routes(config, settings.serverless_proxy_miit_url)


def _gateway_endpoints(config: dict[str, Any], *, force_enabled: bool = False) -> list[str]:
    row = _row(config)
    if not force_enabled and not bool(row.get("enabled")):
        return []
    endpoints: list[str] = []
    for node in pool_nodes(row):
        if not node["endpoint"] or (not force_enabled and not node["enabled"]):
            continue
        if node["status"] == "error" and not force_enabled:
            continue
        if node["endpoint"] not in endpoints:
            endpoints.append(node["endpoint"])
    if not endpoints and str(row.get("endpoint") or "").strip():
        endpoints.append(str(row["endpoint"]).strip())
    return endpoints


def serverless_proxy_view(config: dict[str, Any]) -> ServerlessProxyView:
    row = _row(config)
    nodes = pool_nodes(row)
    views = [
        ServerlessProxyNodeView(
            id=node["id"],
            enabled=node["enabled"],
            provider=node["provider"],
            endpoint=node["endpoint"],
            region=node["region"],
            function_name=node["function_name"],
            image_uri=node["image_uri"],
            access_key_id=node["access_key_id"],
            has_access_key_secret=bool(node["access_key_secret"]),
            insecure_skip_verify=node["insecure_skip_verify"],
            deployment_id=node["deployment_id"],
            status=node["status"],
            last_error=node["last_error"],
            latency_ms=node["latency_ms"],
            failure_count=node["failure_count"],
            updated_at=node.get("updated_at"),
        )
        for node in nodes
    ]
    return ServerlessProxyView(
        enabled=bool(row.get("enabled")),
        provider=str(row.get("provider") or "aliyun"),
        endpoint=str(row.get("endpoint") or ""),
        region=str(row.get("region") or "cn-hangzhou"),
        function_name=str(row.get("function_name") or "asset-workbench-seamoon"),
        image_uri=str(row.get("image_uri") or ""),
        access_key_id=str(row.get("access_key_id") or ""),
        has_access_key_secret=bool(str(row.get("access_key_secret") or "")),
        insecure_skip_verify=bool(row.get("insecure_skip_verify")),
        deployment_id=str(row.get("deployment_id") or ""),
        status=str(row.get("status") or "not_configured"),
        last_error=str(row.get("last_error") or ""),
        local_proxy_url=settings.serverless_proxy_url,
        updated_at=row.get("updated_at"),
        nodes=views,
    )


def active_proxy_url(config: dict[str, Any]) -> str:
    values = active_proxy_urls(config)
    return values[0] if values else ""


def miit_proxy_url(config: dict[str, Any]) -> str:
    values = miit_proxy_urls(config)
    return values[0] if values else ""


def validate_saved_config(config: dict[str, Any]) -> None:
    row = _row(config)
    endpoint = str(row.get("endpoint") or "").strip()
    if row.get("enabled") and not endpoint and not _gateway_endpoints({**row, "enabled": True}):
        raise ServerlessProxyError("启用云函数代理前，请先填写函数地址或完成云端部署")
    for candidate in [endpoint, *[node["endpoint"] for node in pool_nodes(row)]]:
        if not candidate:
            continue
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https", "ws", "wss"} or not parsed.netloc:
            raise ServerlessProxyError("云函数地址必须是有效的 HTTP(S) 或 WebSocket 地址")


def validate_deploy_config(config: dict[str, Any]) -> None:
    row = _row(config)
    provider = str(row.get("provider") or "")
    if provider not in {"aliyun", "tencent"}:
        raise ServerlessProxyError("其他云仅支持接入已部署的函数地址")
    if not str(row.get("access_key_id") or "").strip() or not str(row.get("access_key_secret") or "").strip():
        raise ServerlessProxyError("部署云函数需要 AccessKey ID 和 AccessKey Secret")
    if not str(row.get("region") or "").strip() or not str(row.get("function_name") or "").strip():
        raise ServerlessProxyError("部署云函数需要地域和函数名称")


async def configure_gateway(config: dict[str, Any], *, force_enabled: bool | None = None) -> None:
    row = _row(config)
    enabled = bool(row.get("enabled")) if force_enabled is None else force_enabled
    endpoints = _gateway_endpoints(row, force_enabled=enabled)
    payload = {
        "enabled": enabled and bool(endpoints),
        "endpoint": endpoints[0] if endpoints else str(row.get("endpoint") or "").strip(),
        "endpoints": endpoints,
        "insecure_skip_verify": bool(row.get("insecure_skip_verify")),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.put(f"{settings.serverless_proxy_admin_url.rstrip('/')}/config", json=payload)
            response.raise_for_status()
    except Exception as exc:
        if enabled:
            raise ServerlessProxyError(f"本地 SeaMoon 网关不可用：{exc}") from exc


async def configure_gateway_for_active_route(config: dict[str, Any]) -> None:
    """Configure SeaMoon only when the active query route is cloud-backed.

    Ready manual proxies intentionally take exclusive precedence over the cloud
    gateway. Do not make a query depend on a local SeaMoon admin socket that is
    irrelevant to the selected route (and may be stopped in manual-only mode).
    """
    if manual_proxy_urls(config):
        return
    await configure_gateway(config)


async def test_serverless_proxy(config: dict[str, Any]) -> ServerlessProxyTestResponse:
    row = _row(config)
    validate_saved_config({**row, "enabled": True})
    nodes = pool_nodes(row)
    endpoint_nodes = [node for node in nodes if node.get("endpoint")]
    if not endpoint_nodes and str(row.get("endpoint") or "").strip():
        endpoint_nodes = [_normalise_node(row)]
    if not endpoint_nodes:
        raise ServerlessProxyError("没有可测试的云函数节点")
    verify = not bool(row.get("insecure_skip_verify"))
    endpoint_label = ", ".join(node["endpoint"] for node in endpoint_nodes)
    latencies: list[int] = []
    failures: list[str] = []
    try:
        for node in endpoint_nodes:
            endpoint = node["endpoint"]
            await configure_gateway(
                {**row, "endpoint": endpoint, "nodes": [{**node, "enabled": True}]},
                force_enabled=True,
            )
            last_error: Exception | None = None
            for attempt in range(3):
                started = time.monotonic()
                timeout = httpx.Timeout(20.0, connect=15.0, read=20.0, write=20.0, pool=5.0)
                try:
                    async with httpx.AsyncClient(
                        proxy=settings.serverless_proxy_url,
                        timeout=timeout,
                        follow_redirects=True,
                        trust_env=False,
                        verify=verify,
                    ) as client:
                        response = await client.get(TEST_TARGET)
                        response.raise_for_status()
                    latencies.append(max(0, round((time.monotonic() - started) * 1000)))
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001 - cold starts can fail transiently
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(attempt + 1)
            if last_error is not None:
                failures.append(f"{node['region']}：{last_error}")

        if not latencies:
            detail = failures[0] if failures else "云函数代理未返回有效响应"
            raise ServerlessProxyError(detail)
        return ServerlessProxyTestResponse(
            latency_ms=round(sum(latencies) / len(latencies)),
            endpoint=endpoint_label,
            target=TEST_TARGET,
            tested_nodes=len(endpoint_nodes),
            successful_nodes=len(latencies),
        )
    except ServerlessProxyError:
        raise
    except Exception as exc:
        raise ServerlessProxyError(f"云函数代理测试失败：{exc}") from exc
    finally:
        await configure_gateway(row, force_enabled=bool(row.get("enabled")))


async def run_cloud_operation(action: str, config: dict[str, Any]) -> dict[str, str]:
    row = _row(config)
    validate_deploy_config(row)
    binary = Path(settings.seamoon_core_binary)
    if not binary.is_file():
        raise ServerlessProxyError(f"SeaMoon 核心程序不存在：{binary}")
    payload = {
        "provider": row["provider"],
        "access_key_id": row["access_key_id"],
        "access_key_secret": row["access_key_secret"],
        "region": row["region"],
        "function_name": row["function_name"],
        "image_uri": row.get("image_uri") or "",
        "deployment_id": row.get("deployment_id") or "",
    }
    process = await asyncio.create_subprocess_exec(
        str(binary), "cloud", action,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload).encode()),
            timeout=150 if action == "deploy" else 60,
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ServerlessProxyError("云平台操作超时") from exc
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip().splitlines()
        detail = message[-1] if message else "未知错误"
        detail = detail.removeprefix("error: ")
        raise ServerlessProxyError(f"云平台操作失败：{detail}")
    try:
        result = json.loads(stdout)
    except ValueError as exc:
        raise ServerlessProxyError("SeaMoon 核心程序返回了无效结果") from exc
    if not isinstance(result, dict):
        raise ServerlessProxyError("SeaMoon 核心程序返回了无效结果")
    return {str(key): str(value) for key, value in result.items() if value is not None}
