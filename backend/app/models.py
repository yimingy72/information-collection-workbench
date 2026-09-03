from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.providers.names import ProviderId, normalize_providers


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["enterprise"] = "enterprise"
    keyword: str = Field(min_length=2, max_length=200)
    providers: list[ProviderId] = Field(default_factory=lambda: ["tianyancha"], min_length=1)
    provider: str | None = None
    depth: int = Field(default=1, ge=1, le=5)
    holding_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100, max_digits=5, decimal_places=2)
    include_branches: bool = False
    fields: list[Literal["invest", "partner"]] = Field(default_factory=lambda: ["invest"], min_length=1)

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("keyword must not be blank")
        return value

    @field_validator("providers")
    @classmethod
    def unique_providers(cls, value: list[str]) -> list[str]:
        ids = normalize_providers(list(value))
        if not ids:
            raise ValueError("请至少选择一个数据源")
        return ids  # type: ignore[return-value]

    @field_validator("provider")
    @classmethod
    def legacy_provider(cls, value: str | None) -> str | None:
        if value in {None, "tianyancha-anonymous"}:
            return "tianyancha"
        return value

    @field_validator("fields")
    @classmethod
    def unique_fields(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value)) or ["invest"]

    @field_validator("include_branches")
    @classmethod
    def branches_unsupported(cls, value: bool) -> bool:
        if value:
            raise ValueError("分支机构暂不支持")
        return value


class RunSummary(BaseModel):
    id: UUID
    keyword: str
    provider: str
    providers: list[str]
    depth: int
    holding_percent: Decimal
    include_branches: bool
    fields: list[str]
    status: str
    attempts: int
    progress: int
    total: int | None
    icp_cache_hits: int = 0
    icp_live_queries: int = 0
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunListResponse(BaseModel):
    items: list[RunSummary]
    total: int


class RunDetail(RunSummary):
    request: CollectionRequest


class ResultItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: str
    entity_id: UUID
    entity_name: str
    payload: dict
    source_url: str
    captured_at: datetime


class RelationshipItem(BaseModel):
    id: UUID
    parent_entity_id: UUID
    parent_name: str
    child_entity_id: UUID
    child_name: str
    relation_type: str
    holding_percent: Decimal | None
    depth: int
    reference: str
    source_url: str
    source: str
    captured_at: datetime


class ShareholderRow(BaseModel):
    name: str
    company: str
    holding_percent: float | None
    source: str


class InvestmentRow(BaseModel):
    parent_name: str
    child_name: str
    holding_percent: Decimal | None
    depth: int
    source: str


class IcpRow(BaseModel):
    unit_name: str
    main_licence: str
    service_licence: str
    domain: str
    nature_name: str
    update_time: str
    source: str = "ICP备案"


class QueryResponse(BaseModel):
    run: RunSummary
    investments: list[InvestmentRow]
    shareholders: list[ShareholderRow]
    icp_records: list[IcpRow]
    source_errors: list[str]


class ResultsResponse(BaseModel):
    run_id: UUID
    results: list[ResultItem]
    relationships: list[RelationshipItem]
    total_results: int
    total_relationships: int


class SubdomainOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passive: bool = True
    brute_force: bool = True
    deep_scan: bool = True
    http_probe: bool = True

    @model_validator(mode="after")
    def require_discovery_method(self):
        if not self.passive and not self.brute_force:
            raise ValueError("被动数据源和 DNS 字典至少启用一项")
        return self


class SubdomainRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: list[str] = Field(default_factory=list, max_length=200)
    source_run_ids: list[UUID] = Field(default_factory=list, max_length=50)
    options: SubdomainOptions = Field(default_factory=SubdomainOptions)

    @field_validator("domains")
    @classmethod
    def strip_domains(cls, values: list[str]) -> list[str]:
        return [str(value or "").strip() for value in values if str(value or "").strip()]

    @model_validator(mode="after")
    def require_source(self):
        if not self.domains and not self.source_run_ids:
            raise ValueError("请至少输入域名或选择一条 ICP 查询记录")
        return self


class SubdomainRunSummary(BaseModel):
    id: UUID
    domains: list[str]
    source_run_ids: list[UUID]
    options: SubdomainOptions
    status: str
    phase: str
    attempts: int
    progress: int
    total: int | None
    discovered: int
    warnings: list[str]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SubdomainRunListResponse(BaseModel):
    items: list[SubdomainRunSummary]
    total: int


class SubdomainResultItem(BaseModel):
    id: int
    run_id: UUID
    root_domain: str
    hostname: str
    ips: list[str]
    canonical_name: str
    dns_status: str
    wildcard: bool
    http_url: str
    http_status: int | None
    title: str
    sources: list[str]
    discovered_at: datetime


class SubdomainResultsResponse(BaseModel):
    run_id: UUID
    items: list[SubdomainResultItem]
    total: int


class IcpDomainRun(BaseModel):
    id: UUID
    keyword: str
    created_at: datetime
    domains: list[str]


class IcpDomainRunListResponse(BaseModel):
    items: list[IcpDomainRun]


class ProviderSessionView(BaseModel):
    provider: str
    label: str
    status: Literal["logged_out", "logged_in", "expired"]
    expires_at: datetime | None = None
    updated_at: datetime | None = None


CloudProvider = Literal["aliyun", "tencent", "custom"]


class ServerlessProxyNodeView(BaseModel):
    id: str
    enabled: bool
    provider: CloudProvider
    endpoint: str
    region: str
    function_name: str
    image_uri: str
    access_key_id: str
    has_access_key_secret: bool
    insecure_skip_verify: bool
    deployment_id: str
    status: str
    last_error: str
    latency_ms: int | None = None
    failure_count: int = 0
    updated_at: datetime | None = None


class ServerlessProxyView(BaseModel):
    enabled: bool
    provider: CloudProvider
    endpoint: str
    region: str
    function_name: str
    image_uri: str
    access_key_id: str
    has_access_key_secret: bool
    insecure_skip_verify: bool
    deployment_id: str
    status: str
    last_error: str
    local_proxy_url: str
    updated_at: datetime | None = None
    nodes: list[ServerlessProxyNodeView] = Field(default_factory=list)


class ManualProxyView(BaseModel):
    id: UUID
    scheme: Literal["http", "https"]
    host: str
    port: int
    username: str
    has_password: bool
    enabled: bool
    status: str
    latency_ms: int | None = None
    failure_count: int = 0
    last_error: str
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SettingsResponse(BaseModel):
    sessions: list[ProviderSessionView]
    serverless_proxy: ServerlessProxyView
    manual_proxies: list[ManualProxyView] = Field(default_factory=list)


class ManualProxyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxy_url: str = Field(min_length=1, max_length=2000)
    enabled: bool = True

    @field_validator("proxy_url")
    @classmethod
    def strip_proxy_url(cls, value: str) -> str:
        return str(value or "").strip()


class ManualProxyTestResponse(BaseModel):
    status: Literal["ok"] = "ok"
    proxy_id: UUID
    latency_ms: int
    target: str


class ServerlessProxyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: CloudProvider = "aliyun"
    endpoint: str = Field(default="", max_length=2000)
    region: str = Field(default="cn-hangzhou", max_length=100)
    function_name: str = Field(default="asset-workbench-seamoon", min_length=1, max_length=128)
    image_uri: str = Field(default="", max_length=2000)
    access_key_id: str = Field(default="", max_length=512)
    access_key_secret: str | None = Field(default=None, max_length=2000)
    insecure_skip_verify: bool = False
    node_id: str | None = Field(default=None, max_length=300)

    @field_validator("endpoint", "region", "function_name", "image_uri", "access_key_id")
    @classmethod
    def strip_cloud_value(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("access_key_secret")
    @classmethod
    def strip_cloud_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None


class ServerlessProxyTestResponse(BaseModel):
    status: Literal["ok"] = "ok"
    latency_ms: int
    endpoint: str
    target: str
    tested_nodes: int = 1
    successful_nodes: int = 1


class ServerlessProxyEnableResponse(BaseModel):
    settings: SettingsResponse
    test: ServerlessProxyTestResponse


class ServerlessProxyDeployResponse(BaseModel):
    settings: SettingsResponse
    test: ServerlessProxyTestResponse


class ProviderSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cookie: str = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("cookie")
    @classmethod
    def normalize_cookie(cls, value: str) -> str:
        value = " ".join(str(value or "").replace("\n", " ").split()).strip().strip(";")
        if not value:
            raise ValueError("请粘贴 Cookie")
        return value
