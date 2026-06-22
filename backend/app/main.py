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


@asynccontextmanager
async def lifespan(app: FastAPI):
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
