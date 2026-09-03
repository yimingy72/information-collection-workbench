import pytest

from app.serverless_proxy import (
    ServerlessProxyError,
    active_proxy_url,
    configure_gateway_for_active_route,
    miit_proxy_url,
    _gateway_endpoints,
    serverless_proxy_view,
    validate_deploy_config,
    validate_saved_config,
)
from app.settings import settings


def config(**overrides):
    row = {
        "enabled": False,
        "provider": "aliyun",
        "endpoint": "",
        "region": "cn-hangzhou",
        "function_name": "asset-workbench-seamoon",
        "image_uri": "",
        "access_key_id": "",
        "access_key_secret": "",
        "insecure_skip_verify": False,
        "deployment_id": "",
        "status": "not_configured",
        "last_error": "",
        "updated_at": None,
    }
    row.update(overrides)
    return {"serverless_proxy": row}


def test_view_never_exposes_access_key_secret():
    view = serverless_proxy_view(config(access_key_id="ak", access_key_secret="secret"))
    assert view.access_key_id == "ak"
    assert view.has_access_key_secret is True
    assert "access_key_secret" not in view.model_dump()


def test_active_proxy_requires_enabled_endpoint():
    assert active_proxy_url(config(endpoint="https://example.test")) == ""
    active = config(enabled=True, endpoint="https://example.test")
    assert active_proxy_url(active) == settings.serverless_proxy_url
    assert miit_proxy_url(active) == settings.serverless_proxy_miit_url


def test_manual_routes_take_priority_over_cloud_route():
    manual = "http://user:pass@manual.example:8080"
    active = config(
        enabled=True,
        endpoint="https://example.test",
        manual_proxies=[{
            "scheme": "http",
            "host": "manual.example",
            "port": 8080,
            "username": "user",
            "password": "pass",
            "enabled": True,
            "status": "ready",
        }],
    )
    assert active_proxy_url(active) == manual
    assert miit_proxy_url(active) == manual


<<<<<<< HEAD
=======
def test_gateway_endpoint_pool_excludes_disabled_or_failed_nodes():
    runtime = config(
        enabled=True,
        endpoint="https://legacy.example",
        nodes=[
            {
                "id": "aliyun:cn-hangzhou:asset-workbench-seamoon",
                "enabled": True,
                "status": "ready",
                "endpoint": "https://hangzhou.example",
            },
            {
                "id": "aliyun:cn-qingdao:asset-workbench-seamoon",
                "enabled": False,
                "status": "error",
                "endpoint": "https://qingdao.example",
            },
        ],
    )

    assert _gateway_endpoints(runtime) == ["https://hangzhou.example"]
    assert _gateway_endpoints(runtime, force_enabled=True) == [
        "https://hangzhou.example",
        "https://qingdao.example",
    ]


@pytest.mark.asyncio
async def test_configure_gateway_keeps_failed_nodes_out_when_enabled(monkeypatch):
    import app.serverless_proxy as proxy

    calls = []

    async def fake_put(*_args, **_kwargs):
        calls.append(_kwargs["json"])

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def put(self, *_args, **kwargs):
            await fake_put(json=kwargs["json"])

            class Response:
                def raise_for_status(self):
                    return None

            return Response()

    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    await proxy.configure_gateway(
        config(
            enabled=True,
            nodes=[
                {"endpoint": "https://healthy.example", "enabled": True, "status": "ready"},
                {"endpoint": "https://dead.example", "enabled": False, "status": "error"},
            ],
        )
    )

    assert calls[0]["enabled"] is True
    assert calls[0]["endpoints"] == ["https://healthy.example"]


>>>>>>> 00b6672 (优化ICP节点调度并同步手动代理规则)

@pytest.mark.asyncio
async def test_active_route_does_not_require_seamoon_when_manual_proxy_is_ready(monkeypatch):
    import app.serverless_proxy as proxy

    calls = []

    async def fake_configure(config, **kwargs):
        calls.append((config, kwargs))

    monkeypatch.setattr(proxy, "configure_gateway", fake_configure)
    runtime = {
        "serverless_proxy": config(enabled=True, endpoint="https://example.test")["serverless_proxy"],
        "manual_proxies": [{
            "scheme": "http",
            "host": "manual.example",
            "port": 8080,
            "username": "user",
            "password": "pass",
            "enabled": True,
            "status": "ready",
        }],
    }

    await configure_gateway_for_active_route(runtime)
    assert calls == []


def test_runtime_config_keeps_manual_routes_when_serverless_row_is_present():
    manual = "http://user:pass@manual.example:8080"
    runtime = {
        "serverless_proxy": config(enabled=True, endpoint="https://example.test")["serverless_proxy"],
        "manual_proxies": [{
            "scheme": "http",
            "host": "manual.example",
            "port": 8080,
            "username": "user",
            "password": "pass",
            "enabled": True,
            "status": "ready",
        }],
    }
    assert active_proxy_url(runtime) == manual
    assert miit_proxy_url(runtime) == manual

def test_enabled_config_requires_endpoint():
    with pytest.raises(ServerlessProxyError, match="函数地址"):
        validate_saved_config(config(enabled=True))


@pytest.mark.parametrize("provider,region", [
    ("aliyun", "cn-hangzhou"),
    ("tencent", "ap-guangzhou"),
])
def test_managed_deploy_does_not_require_image(provider, region):
    validate_deploy_config(config(
        provider=provider,
        access_key_id="ak",
        access_key_secret="secret",
        region=region,
    ))


def test_custom_provider_cannot_be_auto_deployed():
    with pytest.raises(ServerlessProxyError, match="其他云"):
        validate_deploy_config(config(provider="custom"))


def test_desired_icp_node_count_is_bounded_and_scales_with_target(monkeypatch):
    import app.serverless_proxy as proxy

    monkeypatch.setattr(proxy.settings, "icp_target_seconds", 300)
    monkeypatch.setattr(proxy.settings, "icp_auto_scale_companies_per_node", 160)
    monkeypatch.setattr(proxy.settings, "icp_auto_scale_max_nodes", 8)
    assert proxy.desired_icp_node_count(1) == 1
    assert proxy.desired_icp_node_count(1280) == 8
    assert proxy.desired_icp_node_count(5000) == 8

    monkeypatch.setattr(proxy.settings, "icp_target_seconds", 600)
    assert proxy.desired_icp_node_count(1280) == 4


@pytest.mark.asyncio
async def test_ensure_icp_node_pool_deploys_only_missing_regions(monkeypatch):
    import app.serverless_proxy as proxy

    base = config(
        enabled=True,
        endpoint="https://hangzhou.example",
        access_key_id="ak",
        access_key_secret="secret",
        nodes=[
            {
                "id": "aliyun:cn-hangzhou:asset-workbench-seamoon",
                "enabled": True,
                "provider": "aliyun",
                "endpoint": "https://hangzhou.example",
                "region": "cn-hangzhou",
                "function_name": "asset-workbench-seamoon",
                "status": "ready",
            },
            {
                "id": "aliyun:cn-shanghai:asset-workbench-seamoon",
                "enabled": True,
                "provider": "aliyun",
                "endpoint": "https://shanghai.example",
                "region": "cn-shanghai",
                "function_name": "asset-workbench-seamoon",
                "status": "ready",
            },
        ],
    )["serverless_proxy"]

    class Repo:
        def __init__(self):
            self.row = base
            self.updated = None

        async def get_runtime_config(self):
            return {"serverless_proxy": self.row, "manual_proxies": []}

        async def update_serverless_proxy(self, payload):
            self.row = payload
            self.updated = payload
            return payload

        async def set_serverless_proxy_status(self, *_args, **_kwargs):
            return self.row

    deployed = []

    async def fake_deploy(_action, cfg):
        deployed.append(cfg["region"])
        return {"endpoint": f"https://{cfg['region']}.example", "deployment_id": cfg["region"]}

    async def fake_configure(*_args, **_kwargs):
        return None

    repo = Repo()
    monkeypatch.setattr(proxy.settings, "icp_target_seconds", 300)
    monkeypatch.setattr(proxy.settings, "icp_auto_scale_companies_per_node", 160)
    monkeypatch.setattr(proxy.settings, "icp_auto_scale_max_nodes", 8)
    monkeypatch.setattr(
        proxy.settings,
        "icp_auto_scale_regions",
        "cn-hangzhou,cn-shanghai,cn-beijing,cn-shenzhen",
    )
    async def fake_test(*_args, **_kwargs):
        return None

    monkeypatch.setattr(proxy, "run_cloud_operation", fake_deploy)
    monkeypatch.setattr(proxy, "test_serverless_proxy", fake_test)
    monkeypatch.setattr(proxy, "configure_gateway", fake_configure)

    result = await proxy.ensure_icp_node_pool(repo, 500)

    assert result["target"] == 4
    assert result["deployed"] == 2
    assert set(deployed) == {"cn-beijing", "cn-shenzhen"}
    assert len(repo.updated["nodes"]) == 4


def test_auto_scale_regions_excludes_configured_regions(monkeypatch):
    import app.serverless_proxy as proxy

    monkeypatch.setattr(
        proxy.settings,
        "icp_auto_scale_regions",
        "cn-hangzhou,cn-chengdu,cn-shanghai",
    )
    monkeypatch.setattr(proxy.settings, "icp_auto_scale_excluded_regions", "cn-chengdu")
    assert proxy._auto_scale_regions() == ["cn-hangzhou", "cn-shanghai"]
