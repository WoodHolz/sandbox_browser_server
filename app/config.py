from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sandbox_image: str = "woodholz/infomator-sandbox:latest"
    sandbox_name_prefix: str = "yuan-sandbox"
    sandbox_api_port: int = 8081
    sandbox_ttl_minutes: int = 30
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
    # Each heartbeat pushes destroy_after to now + this many seconds.
    heartbeat_extend_seconds: int = 300
    # Hard ceiling from created_at; heartbeat cannot go past this.
    absolute_max_lifetime_seconds: int = 10800
    cdp_ready_timeout_seconds: int = 60

    host: str = "0.0.0.0"
    port: int = 8090


settings = Settings()

