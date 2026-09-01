from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models import ServerlessProxyTestResponse, ServerlessProxyView
from app.settings import settings

TEST_TARGET = "https://www.baidu.com/robots.txt"


class ServerlessProxyError(RuntimeError):
    pass


def _row(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("serverless_proxy") or config)


def serverless_proxy_view(config: dict[str, Any]) -> ServerlessProxyView:
    row = _row(config)
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
    )


def active_proxy_url(config: dict[str, Any]) -> str:
    row = _row(config)
    if not row.get("enabled") or not str(row.get("endpoint") or "").strip():
        return ""
    return settings.serverless_proxy_url


def miit_proxy_url(config: dict[str, Any]) -> str:
    return settings.serverless_proxy_miit_url if active_proxy_url(config) else ""


def validate_saved_config(config: dict[str, Any]) -> None:
    row = _row(config)
    endpoint = str(row.get("endpoint") or "").strip()
    if row.get("enabled") and not endpoint:
        raise ServerlessProxyError("启用云函数代理前，请先填写函数地址或完成云端部署")
    if endpoint:
        parsed = urlsplit(endpoint)
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
    payload = {
        "enabled": enabled,
        "endpoint": str(row.get("endpoint") or "").strip(),
        "insecure_skip_verify": bool(row.get("insecure_skip_verify")),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.put(f"{settings.serverless_proxy_admin_url.rstrip('/')}/config", json=payload)
            response.raise_for_status()
    except Exception as exc:
        if enabled:
            raise ServerlessProxyError(f"本地 SeaMoon 网关不可用：{exc}") from exc


async def test_serverless_proxy(config: dict[str, Any]) -> ServerlessProxyTestResponse:
    row = _row(config)
    validate_saved_config({**row, "enabled": True})
    endpoint = str(row.get("endpoint") or "").strip()
    verify = not bool(row.get("insecure_skip_verify"))
    try:
        await configure_gateway(row, force_enabled=True)
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
                elapsed = max(0, round((time.monotonic() - started) * 1000))
                return ServerlessProxyTestResponse(
                    latency_ms=elapsed,
                    endpoint=endpoint,
                    target=TEST_TARGET,
                )
            except Exception as exc:  # noqa: BLE001 - cold starts can fail transiently
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(attempt + 1)
        raise last_error or ServerlessProxyError("云函数代理未返回有效响应")
    except ServerlessProxyError:
        raise
    except Exception as exc:
        raise ServerlessProxyError(f"云函数代理测试失败：{exc}") from exc
    finally:
        if not bool(row.get("enabled")):
            await configure_gateway(row, force_enabled=False)


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
