import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _env():
    """孤立 DB / data 目录，避免污染开发数据。"""
    tmp = tempfile.mkdtemp(prefix="cloudhelper-test-")
    os.environ["MASTER_PASSWORD"] = "test-password"
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"
    os.environ["CORS_ORIGINS"] = "http://localhost"
    os.environ["TZ"] = "UTC"
    os.environ["NOTIFY_WEBHOOK_URL"] = ""
    # data_dir 通过 config.py 的默认值或环境变量；这里没专门挂
    yield tmp


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
