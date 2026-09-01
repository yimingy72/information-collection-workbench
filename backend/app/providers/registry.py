from __future__ import annotations

from typing import Any

from app.providers.aiqicha import AnonymousAiqicha
from app.providers.kuaicha import AnonymousKuaicha
from app.providers.names import normalize_providers, provider_label
from app.providers.riskbird import AnonymousRiskbird
from app.providers.tianyancha import AnonymousTianyancha
from app.runtime import SESSION_PROVIDERS, session_cookie, session_login_gaps
from app.settings import settings


def login_required_errors(
    provider_ids: list[str] | None,
    config: dict[str, Any] | None = None,
) -> list[str]:
    return [
        f"请先登录{provider_label(provider_id)}"
        for provider_id in session_login_gaps(normalize_providers(provider_ids), config or {})
    ]


def build_providers(
    provider_ids: list[str] | None,
    config: dict[str, Any] | None = None,
) -> list:
    runtime = config or {}
    providers = []
    skipped = set(session_login_gaps(normalize_providers(provider_ids), runtime))
    for provider_id in normalize_providers(provider_ids):
        if provider_id in skipped:
            continue
        if provider_id == "tianyancha":
            providers.append(AnonymousTianyancha(settings.tianyancha_base_url))
            continue
        cookie = session_cookie(runtime, provider_id)
        if provider_id == "aiqicha":
            providers.append(AnonymousAiqicha(cookie=cookie))
        elif provider_id == "kuaicha":
            providers.append(AnonymousKuaicha(cookie=cookie))
        elif provider_id == "riskbird":
            providers.append(AnonymousRiskbird(cookie=cookie))
    return providers
