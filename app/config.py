from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sandbox_image: str = "woodholz/infomator-sandbox:latest"
    sandbox_name_prefix: str = "yuan-sandbox"
    sandbox_api_port: int = 8081
    # Container SERVICE_TIMEOUT_MINUTES; keep >= absolute_max_lifetime_seconds / 60.
    sandbox_ttl_minutes: int = 1440
    sandbox_mem_limit: str = "512m"
    sandbox_cpu_quota: int = 50000
    sandbox_network: str | None = None
    sandbox_chrome_args: str = ""
    sandbox_https_proxy: str | None = None
    sandbox_http_proxy: str | None = None
    sandbox_no_proxy: str | None = None
    docker_host: str | None = None

    idle_grace_seconds: int = 30
    max_lifetime_seconds: int = 1800
    # Each heartbeat resets the recycle countdown to this many seconds (30 min).
    heartbeat_extend_seconds: int = 1800
    # Hard ceiling from created_at; heartbeat cannot go past this (24 h).
    absolute_max_lifetime_seconds: int = 86400
    cdp_ready_timeout_seconds: int = 60

    host: str = "0.0.0.0"
    port: int = 8090


settings = Settings()

