from __future__ import annotations

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
        "workflows": {"enabled": True},
        "agent_workers": {"max_archive_bytes": 64 * 1024 * 1024, "min_protocol_version": 1},
        "openclaw": {
            "cwd": ".",
            "timeout_seconds": 600,
            "isolated_workspace_root": "",
            "command_template": [
                "openclaw",
                "agent",
                "--local",
                "--agent",
                "main",
                "--session-id",
                "{video_id}-{timestamp}",
                "--thinking",
                "on",
                "--message",
                "{prompt_text}",
                "--json",
            ],
            "skill_safety": {
                "enabled": True,
                "repos": [
                    {"path": "~/.agents/skills/agent-legion/video_knowledge/generate_interactions"},
                    {"path": "~/.agents/skills/agent-legion/video_knowledge/review_video_content"},
                    {"path": "~/.agents/skills/agent-legion/video_knowledge/review_subtitles"},
                    {"path": "~/.agents/skills/agent-legion/video_knowledge/generate_chapters"},
                ],
            },
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
    assert response.json() == _payload()


def test_put_roundtrip(client) -> None:
    payload = _payload()
    payload["lease_ttl_seconds"] = 120
    payload["cleanup"]["log_retention_days"] = 14
    response = client.put(INSTANCE_SETTINGS_URL, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == payload

    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.json() == payload


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
    payload["openclaw"]["timeout_seconds"] = 0
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422
    payload = _payload()
    payload["openclaw"]["command_template"] = []
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422


def test_put_rejects_skill_safety_ref(client) -> None:
    """skill_safety repos are a path-only allowlist (G3): ref keys 422."""
    payload = _payload()
    payload["openclaw"]["skill_safety"]["repos"][0]["ref"] = "v1.0.0"
    assert client.put(INSTANCE_SETTINGS_URL, json=payload).status_code == 422


def test_put_openclaw_roundtrip(client) -> None:
    payload = _payload()
    payload["openclaw"]["cwd"] = "/tmp/openclaw"
    payload["openclaw"]["command_template"] = ["openclaw", "agent", "--json"]
    payload["openclaw"]["skill_safety"]["enabled"] = False
    response = client.put(INSTANCE_SETTINGS_URL, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == payload

    response = client.get(INSTANCE_SETTINGS_URL)
    assert response.json() == payload
