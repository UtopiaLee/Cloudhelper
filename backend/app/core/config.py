from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=[".env", "../.env"], env_file_encoding="utf-8", extra="ignore")

    master_password: str = Field(default="change-me-please", alias="MASTER_PASSWORD")
    auth_username: str = Field(default="", alias="AUTH_USERNAME")  # 空 = 不开鉴权
    auth_password: str = Field(default="", alias="AUTH_PASSWORD")  # 明文，启动时哈希
    access_token: str = Field(default="", alias="ACCESS_TOKEN")    # 静态 token（API 客户端用）
    knock_secret: str = Field(default="", alias="KNOCK_SECRET")    # 空 = 启动时随机生成
    database_url: str = Field(default="sqlite:///./data/cloudhelper.db", alias="DATABASE_URL")
    cors_origins: str = Field(default="http://localhost:8080,http://localhost:5173", alias="CORS_ORIGINS")
    tz: str = Field(default="Asia/Shanghai", alias="TZ")
    notify_webhook_url: str = Field(default="", alias="NOTIFY_WEBHOOK_URL")
    data_dir: Path = Field(default=Path("./data"))

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
