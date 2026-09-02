from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://workbench:workbench@localhost:5432/workbench"
    tianyancha_base_url: str = "https://capi.tianyancha.com"
    miit_api_url: str = "http://127.0.0.1:16181"
    icp_proxy_control_token: str = "asset-workbench-local"
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
