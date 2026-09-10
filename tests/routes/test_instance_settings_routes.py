from __future__ import annotations

from server.app.skills.skill_roots import SKILLS_ROOT_DISPLAY

CSRF = {"x-agent-legion-request": "1"}
INSTANCE_SETTINGS_URL = "/api/admin/instance-settings"


def _member_client(client, username="instance_member", password="pw1"):
    response = client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _payload() -> dict:
    return {
        "cleanup": {
            "log_retention_days": 7,
            "run_dir_retention_days": 3,
            "interval_seconds": 3600,
        },
        "monitoring": {"sample_interval_seconds": 60, "retention_days": 30},
        "heartbeat_interval_seconds": 10,
        "lease_ttl_seconds": 90,
        "heartbeat_failure_threshold": 3,
        "sweeper_enabled": True,
        "sweeper_interval_seconds": 5.0,
        "code_capacity": 16,
        "materials_ttl_days": 0,
        "execution_retention_days": 0,
        "workflows": {"max_items_per_run": 20_000},
        "agent_workers": {
            "max_archive_bytes": 64 * 1024 * 1024,
            "min_protocol_version": 1,
            "max_concurrent_result_commits": 16,
            "result_commit_batching": True,
        },
        "agent_enqueue": {"workers": 48, "max_pending": 1024},
        "result_unpack": {"workers": 0},
        "result_validate": {"workers": 0},
        "agent_claim": {"worker_touch_interval_seconds": 30},
    }


def test_get_requires_auth(anon_client) -> None:
    assert anon_client.get(INSTANCE_SETTINGS_URL).status_code == 401


def test_put_requires_auth(anon_client) -> None:
    assert anon_client.put(INSTANCE_SETTINGS_URL, json=_payload()).status_code == 401


def test_member_forbidden(client) -> None:
    member = _member_client(client)
    assert member.get(INSTANCE_SETTINGS_URL).status_code == 403
    assert member.put(INSTANCE_SETTINGS_URL, json=_payload()).status_code == 403


def test_get_returns_code_defaults_when_unset(client) -> None:
    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.status_code == 200
    assert response.json() == {**_payload(), "skills_root": SKILLS_ROOT_DISPLAY}


def test_get_includes_readonly_skills_root(client) -> None:
    """The response carries the on-disk skills root as a read-only field."""
    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.status_code == 200
    assert response.json()["skills_root"] == SKILLS_ROOT_DISPLAY


def test_get_strips_legacy_stored_openclaw_block(client) -> None:
    """A stored document saved before the openclaw retirement (#75) still
    carries an openclaw block; GET must drop it wholesale instead of failing
    response validation with a 500."""
    from server.app.services.instance_settings_store import InstanceSettingsStore

    store = InstanceSettingsStore(client.app.state.job_db.dsn_identity)
    store.put(
        {
            "openclaw": {
                "cwd": "/tmp/openclaw-legacy",
                "command_template": ["openclaw", "agent"],
                "skill_safety": {"repos": [{"path": "~/.skills/s1"}]},
            }
        }
    )

    response = client.get(INSTANCE_SETTINGS_URL)

    assert response.status_code == 200, response.text
    assert "openclaw" not in response.json()


def test_get_strips_retention_cursor_block(client) -> None:
    """The retention sweep's persisted keyset cursors (#354) ride the stored
    instance document; GET must drop them instead of failing the
    extra=forbid response validation with a 500."""
    from server.app.services.instance_settings_store import InstanceSettingsStore

    store = InstanceSettingsStore(client.app.state.job_db.dsn_identity)
    document = _payload()
    document["execution_retention_cursor"] = {
        "requests:done": {"at": "2026-01-01T00:00:00+00:00", "id": "exec-1"}
    }
    store.put(document)

    response = client.get(INSTANCE_SETTINGS_URL)

    assert response.status_code == 200, response.text
    assert "execution_retention_cursor" not in response.json()
    assert response.json()["execution_retention_days"] == 0


def test_put_roundtrip(client) -> None:
    payload = _payload()
    payload["lease_ttl_seconds"] = 120
    payload["cleanup"]["log_retention_days"] = 14
    response = client.put(INSTANCE_SETTINGS_URL, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == {**payload, "skills_root": SKILLS_ROOT_DISPLAY}

    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.json() == {**payload, "skills_root": SKILLS_ROOT_DISPLAY}


def test_put_rejects_skills_root(client) -> None:
    """skills_root is read-only (server-injected); writing it 422s like any
    other unknown key (InstanceSettingsUpdate is extra="forbid")."""
    payload = _payload()
    payload["skills_root"] = "/somewhere/else"
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422


def test_put_rejects_unknown_keys(client) -> None:
    payload = _payload()
    payload["unknown_key"] = 1
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    nested = _payload()
    nested["cleanup"]["bogus"] = 1
    assert client.put(INSTANCE_SETTINGS_URL, json=nested).status_code == 422


def test_put_rejects_out_of_range_values(client) -> None:
    payload = _payload()
    payload["lease_ttl_seconds"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["heartbeat_interval_seconds"] = -1
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["agent_workers"]["min_protocol_version"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["monitoring"]["sample_interval_seconds"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422


def test_put_rejects_invalid_materials_ttl(client) -> None:
    payload = _payload()
    payload["materials_ttl_days"] = -1
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["materials_ttl_days"] = "thirty"
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    # 上界 36500（约 100 年）：超过会让 complete 的 now() + make_interval 溢出。
    payload = _payload()
    payload["materials_ttl_days"] = 36501
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["materials_ttl_days"] = 36500
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 200


def test_put_materials_ttl_roundtrip(client) -> None:
    payload = _payload()
    payload["materials_ttl_days"] = 30
    response = client.put(INSTANCE_SETTINGS_URL, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["materials_ttl_days"] == 30

    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.json()["materials_ttl_days"] == 30


def test_put_capacity_knobs_roundtrip(client) -> None:
    """#509/#554/#569/#561: agent_enqueue / result_unpack / result_validate /
    agent_claim blocks ride the full-document PUT and come back on GET."""
    payload = _payload()
    payload["agent_enqueue"] = {"workers": 64, "max_pending": 2048}
    payload["result_unpack"] = {"workers": 8}
    payload["result_validate"] = {"workers": 6}
    payload["agent_claim"] = {"worker_touch_interval_seconds": 7.5}
    response = client.put(INSTANCE_SETTINGS_URL, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["agent_enqueue"] == {"workers": 64, "max_pending": 2048}
    assert response.json()["result_unpack"] == {"workers": 8}
    assert response.json()["result_validate"] == {"workers": 6}
    assert response.json()["agent_claim"] == {"worker_touch_interval_seconds": 7.5}

    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.json()["agent_enqueue"] == {"workers": 64, "max_pending": 2048}
    assert response.json()["result_unpack"] == {"workers": 8}
    assert response.json()["result_validate"] == {"workers": 6}
    assert response.json()["agent_claim"] == {"worker_touch_interval_seconds": 7.5}


def test_put_rejects_out_of_range_capacity_knobs(client) -> None:
    payload = _payload()
    payload["agent_enqueue"]["workers"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["agent_enqueue"]["workers"] = 257  # #509 misconfiguration guard
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["agent_enqueue"]["max_pending"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["result_unpack"]["workers"] = 65
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    # #569：validate 池同上限。
    payload = _payload()
    payload["result_validate"]["workers"] = 65
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    # #561：负的写入间隔不合法；0 = 每次都写（恢复 0.7.5 行为）合法。
    payload = _payload()
    payload["agent_claim"]["worker_touch_interval_seconds"] = -1
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["agent_claim"]["worker_touch_interval_seconds"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 200
    # #565 codex：超上限会被 PostgreSQL make_interval 拒绝，契约层拦住。
    payload = _payload()
    payload["agent_claim"]["worker_touch_interval_seconds"] = 86401
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    # 0 = 自动（min(4, 核数)）是合法值。
    payload = _payload()
    payload["result_unpack"]["workers"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 200
    payload = _payload()
    payload["result_validate"]["workers"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 200


def test_put_accepts_capacity_knob_upper_bounds(client) -> None:
    """边界接受侧：workers=256 / 两个进程池 workers=64 / 写入间隔 86400 均为合法上限。"""
    payload = _payload()
    payload["agent_enqueue"]["workers"] = 256
    payload["result_unpack"]["workers"] = 64
    payload["result_validate"]["workers"] = 64
    payload["agent_claim"]["worker_touch_interval_seconds"] = 86400
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 200


def test_put_rejects_retired_openclaw_block(client) -> None:
    """The openclaw block retired with the openclaw runtime (#75): writing it
    422s like any other unknown key (InstanceSettingsUpdate is extra=forbid)."""
    payload = _payload()
    payload["openclaw"] = {"cwd": "/tmp/openclaw"}
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422


def test_get_legacy_document_missing_capacity_blocks_falls_back(client) -> None:
    """#509/#554/#569/#561: a stored document written before the capacity knobs
    existed carries no agent_enqueue / result_unpack / result_validate /
    agent_claim blocks;
    GET must merge the code defaults (no migration) instead of failing
    response validation."""
    from server.app.services.instance_settings_store import InstanceSettingsStore

    store = InstanceSettingsStore(client.app.state.job_db.dsn_identity)
    document = _payload()
    del document["agent_enqueue"]
    del document["result_unpack"]
    del document["result_validate"]
    del document["agent_claim"]
    store.put(document)

    response = client.get(INSTANCE_SETTINGS_URL)

    assert response.status_code == 200, response.text
    assert response.json()["agent_enqueue"] == {"workers": 48, "max_pending": 1024}
    assert response.json()["result_unpack"] == {"workers": 0}
    assert response.json()["result_validate"] == {"workers": 0}
    assert response.json()["agent_claim"] == {"worker_touch_interval_seconds": 30}
