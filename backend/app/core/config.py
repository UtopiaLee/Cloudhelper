from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=[".env", "../.env"], env_file_encoding="utf-8", extra="ignore")

    master_password: str = Field(default="change-me-please", alias="MASTER_PASSWORD")
    auth_username: str = Field(default="", alias="AUTH_USERNAME")
    auth_password: str = Field(default="", alias="AUTH_PASSWORD")
    access_token: str = Field(default="", alias="ACCESS_TOKEN")
    knock_secret: str = Field(default="", alias="KNOCK_SECRET")
    database_url: str = Field(default="sqlite:///./data/cloudhelper.db", alias="DATABASE_URL")
    cors_origins: str = Field(default="http://localhost:8080,http://localhost:5173", alias="CORS_ORIGINS")
    tz: str = Field(default="Asia/Shanghai", alias="TZ")
    notify_webhook_url: str = Field(default="", alias="NOTIFY_WEBHOOK_URL")
    cookie_secure: bool = Field(default=True, alias="COOKIE_SECURE")
    data_dir: Path = Field(default=Path("./data"))

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def _resolve_env_path() -> Path:
    candidates = [Path(".env"), Path("../.env")]
    for p in candidates:
        rp = p.resolve()
        if rp.exists():
            return rp
    return candidates[0].resolve()


def update_env_vars(updates: dict[str, str]) -> Path:
    env_path = _resolve_env_path()
    updates = {k: v for k, v in updates.items() if k and v is not None}

    # 换行符会被注入为额外的 .env 行，直接拒绝
    for k, v in updates.items():
        if "\r" in v or "\n" in v:
            raise ValueError(f"环境变量 {k} 的值不能包含换行符")

    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    pending = dict(updates)
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue

        key, _ = line.split("=", 1)
        key = key.strip()
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)

    if pending:
        if out and out[-1].strip():
            out.append("")
        for k, v in pending.items():
            out.append(f"{k}={v}")

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return env_path
