from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "NetOps AI V1"
    environment: str = "dev"
    postgres_dsn: str = "postgresql+asyncpg://netops:netops@postgres:5432/netops"
    redis_url: str = "redis://redis:6379/0"
    model_provider: str = "cloud_api"

    class Config:
        env_prefix = "NETOPS_"
        case_sensitive = False


settings = Settings()
