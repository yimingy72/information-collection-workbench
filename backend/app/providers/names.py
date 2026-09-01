from __future__ import annotations

from typing import Literal

PROVIDER_LABELS: dict[str, str] = {
    "tianyancha": "天眼查",
    "tianyancha-anonymous": "天眼查",
    "aiqicha": "爱企查",
    "kuaicha": "快查",
    "riskbird": "风鸟",
}

ProviderId = Literal["tianyancha", "aiqicha", "kuaicha", "riskbird"]
ENABLED_PROVIDERS: tuple[ProviderId, ...] = ("tianyancha", "aiqicha", "kuaicha", "riskbird")


def provider_label(provider_id: str) -> str:
    return PROVIDER_LABELS.get(provider_id, provider_id)


def normalize_providers(values: list[str] | None) -> list[str]:
    ids: list[str] = []
    for value in values or ["tianyancha"]:
        key = "tianyancha" if value == "tianyancha-anonymous" else value
        if key in PROVIDER_LABELS and key not in ids:
            ids.append(key)
    return ids or ["tianyancha"]
