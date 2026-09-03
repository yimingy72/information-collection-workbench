from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://workbench:workbench@localhost:5432/workbench"
    tianyancha_base_url: str = "https://capi.tianyancha.com"
    miit_api_url: str = "http://127.0.0.1:16181"
    icp_proxy_control_token: str = "asset-workbench-local"
    icp_company_max_attempts: int = 3
    icp_company_retry_backoff_seconds: float = 2.0
    icp_target_seconds: int = 300
    icp_auto_scale_max_nodes: int = 8
    icp_auto_scale_companies_per_node: int = 160
    icp_auto_scale_regions: str = (
        "cn-hangzhou,cn-shanghai,cn-beijing,cn-shenzhen,"
        "cn-qingdao,cn-zhangjiakou,cn-huhehaote"
    )
    # Chengdu has repeatedly failed WebSocket warm-up; keep it excluded even
    # if an old deployment injects it into the candidate-region list.
    icp_auto_scale_excluded_regions: str = "cn-chengdu"
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 120
    worker_concurrency: int = 2
    serverless_proxy_url: str = "http://127.0.0.1:19080"
    serverless_proxy_admin_url: str = "http://127.0.0.1:19081"
    serverless_proxy_miit_url: str = "http://seamoon-gateway:19080"
    seamoon_core_binary: str = "/usr/local/bin/seamoon-core"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
