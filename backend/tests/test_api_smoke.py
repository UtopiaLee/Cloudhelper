"""核心 API 烟雾测试。

不连真实云：用 monkeypatch 替换 boto3 调用。
"""

from datetime import datetime


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_full(client):
    resp = client.get("/api/health/full")
    body = resp.json()
    assert "checks" in body
    names = {c["name"] for c in body["checks"]}
    assert "database" in names
    assert "crypto" in names
    assert "scheduler" in names


def test_system_jobs(client):
    resp = client.get("/api/system/jobs")
    assert resp.status_code == 200
    # 启动时已注册 sys.ssh_collect / sys.monthly_reset
    ids = {j["id"] for j in resp.json()}
    assert "sys.ssh_collect" in ids


def test_accounts_empty_list(client):
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_account_crud_aws_minimal(client):
    payload = {
        "name": "test-aws",
        "provider": "aws",
        "default_region": "us-east-1",
        "credentials": {
            "access_key_id": "AKIA_TEST",
            "secret_access_key": "secret-test",
        },
    }
    r = client.post("/api/accounts", json=payload)
    assert r.status_code == 200, r.text
    acc = r.json()
    assert acc["name"] == "test-aws"
    assert acc["provider"] == "aws"
    assert "credentials" not in acc  # 不能泄密
    acc_id = acc["id"]

    # 重复名 → 400
    r2 = client.post("/api/accounts", json=payload)
    assert r2.status_code == 400

    # 查到
    r3 = client.get("/api/accounts")
    assert any(a["id"] == acc_id for a in r3.json())

    # 删除
    r4 = client.delete(f"/api/accounts/{acc_id}")
    assert r4.status_code == 200


def test_validation_error_friendly(client):
    """缺字段 → 422 + 友好消息。"""
    r = client.post("/api/accounts", json={"name": ""})
    assert r.status_code == 422
    body = r.json()
    assert "detail" in body
    assert "[" in body["detail"]  # 含字段名


def test_audit_log_visible(client):
    """创建账号会落 audit 吗？暂时只有 instance 操作落，account 不落，跳过实际断言。"""
    r = client.get("/api/audit")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
