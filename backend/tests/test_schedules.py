"""定时任务接口测试。"""


def _make_account(client):
    r = client.post("/api/accounts", json={
        "name": "test-acc-sched",
        "provider": "aws",
        "default_region": "us-east-1",
        "credentials": {"access_key_id": "X", "secret_access_key": "Y"},
    })
    assert r.status_code == 200
    return r.json()["id"]


def test_create_cron_schedule(client):
    aid = _make_account(client)
    r = client.post(f"/api/accounts/{aid}/schedules", json={
        "instance_id": "i-test",
        "action": "stop",
        "trigger_type": "cron",
        "cron": "0 23 * * *",
        "enabled": True,
        "note": "夜间停机",
    })
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["cron"] == "0 23 * * *"
    assert s["action"] == "stop"

    # 删除
    client.delete(f"/api/accounts/{aid}/schedules/{s['id']}")
    client.delete(f"/api/accounts/{aid}")


def test_cron_validation_bad_format(client):
    aid = _make_account(client)
    r = client.post(f"/api/accounts/{aid}/schedules", json={
        "instance_id": "i-test",
        "action": "stop",
        "trigger_type": "cron",
        "cron": "bad",  # 不是 5 段
        "enabled": True,
    })
    assert r.status_code == 400
    assert "cron" in r.json()["detail"]
    client.delete(f"/api/accounts/{aid}")


def test_date_schedule_past_rejected(client):
    aid = _make_account(client)
    r = client.post(f"/api/accounts/{aid}/schedules", json={
        "instance_id": "i-test",
        "action": "destroy",
        "trigger_type": "date",
        "run_at": "2000-01-01T00:00:00Z",
        "enabled": True,
    })
    assert r.status_code == 400
    assert "未来" in r.json()["detail"]
    client.delete(f"/api/accounts/{aid}")
