import pytest

from app.serverless_proxy import (
    ServerlessProxyError,
    active_proxy_url,
    miit_proxy_url,
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
