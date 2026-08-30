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
        "workflows": {"enabled": True},
        "agent_workers": {"max_archive_bytes": 64 * 1024 * 1024, "min_protocol_version": 1},
        "openclaw": {
            "cwd": ".",
        },
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


def test_get_normalizes_legacy_stored_openclaw_keys(client) -> None:
    """A stored document saved before the openclaw-knob retirement still
    carries command_template/timeout_seconds/isolated_workspace_root/
    skill_safety; GET must normalize to the cwd-only shape instead of
    failing response validation with a 500 (Codex review, PR #183)."""
    from server.app.services.instance_settings_store import InstanceSettingsStore

    store = InstanceSettingsStore(client.app.state.job_db.dsn_identity)
    store.put(
        {
            "openclaw": {
                "cwd": "/tmp/openclaw-legacy",
                "command_template": ["openclaw", "agent"],
                "timeout_seconds": 600,
                "isolated_workspace_root": "",
                "skill_safety": {"enabled": True, "repos": [{"path": "~/.skills/s1"}]},
            }
        }
    )

    response = client.get(INSTANCE_SETTINGS_URL)

    assert response.status_code == 200, response.text
    assert response.json()["openclaw"] == {"cwd": "/tmp/openclaw-legacy"}


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


def test_put_rejects_retired_openclaw_keys(client) -> None:
    """Retired openclaw knobs are unknown fields now: they 422 (extra=forbid)."""
    payload = _payload()
    payload["openclaw"]["skill_safety"] = {"enabled": True, "repos": []}
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["openclaw"]["command_template"] = ["openclaw"]
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422


def test_put_openclaw_roundtrip(client) -> None:
    payload = _payload()
    payload["openclaw"]["cwd"] = "/tmp/openclaw"
    response = client.put(INSTANCE_SETTINGS_URL, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == {**payload, "skills_root": SKILLS_ROOT_DISPLAY}

    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.json() == {**payload, "skills_root": SKILLS_ROOT_DISPLAY}
