from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.auth import TokenAuthMiddleware
from app.core.auto_migrate import auto_migrate
from app.core.config import get_settings
from app.core.crypto import init_crypto
from app.core.db import Base, engine
from app.core.errors import register_exception_handlers
from app.core.health import run_startup_self_check
from app.core.knock import KnockMiddleware, init_knock
from app.core.scheduler import shutdown as sched_shutdown
from app.core.scheduler import start as sched_start

settings = get_settings()

# 弱主密钥会让任何拿到 ./data 的人离线推导 Fernet key，启动时直接拒绝。
_PLACEHOLDER_MASTER_PASSWORDS = {"change-me-please", "change-this-to-a-long-random-string"}
_MIN_MASTER_PASSWORD_LEN = 12


def _guard_master_password(password: str) -> None:
    pw = (password or "").strip()
    if not pw or pw in _PLACEHOLDER_MASTER_PASSWORDS:
        raise RuntimeError(
            "MASTER_PASSWORD 未设置或仍为占位符。请在 .env 中设置一个强随机主密钥后再启动。"
        )
    if len(pw) < _MIN_MASTER_PASSWORD_LEN:
        raise RuntimeError(
            f"MASTER_PASSWORD 太短（至少 {_MIN_MASTER_PASSWORD_LEN} 个字符），请改用更强的主密钥。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _guard_master_password(settings.master_password)
    init_crypto(settings.master_password, settings.data_dir)
    init_knock()
    Base.metadata.create_all(bind=engine)
    auto_migrate(engine)
    sched_start()
    run_startup_self_check()
    yield
    sched_shutdown()


app = FastAPI(
    title="CloudHelper", version="0.1.0", lifespan=lifespan,
    # 隐身模式：关闭 swagger / redoc / openapi.json，扫描器无法批量发现路径
    docs_url=None, redoc_url=None, openapi_url=None,
)
register_exception_handlers(app)
# 中间件顺序：先 knock（外层）再 token auth（内层）—— knock 不过直接 404
app.add_middleware(TokenAuthMiddleware)
app.add_middleware(KnockMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
