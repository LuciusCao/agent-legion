"""Admin registry routes for Studio chat ACP agents (phase 3 chunk 4).

Detection (#332) is patched at the AgentCatalogDetector class level: the
shared test app's detector instance must never depend on the host PATH, so
these tests stay identical on machines with or without the catalog CLIs.
"""

from __future__ import annotations

import logging

import pytest

from server.app.studio_chat.agent_catalog import AgentCatalogDetector, CatalogDetection

CSRF = {"x-agent-legion-request": "1"}
REGISTRY_URL = "/api/admin/studio-agents"
REDETECT_URL = "/api/admin/studio-agents/redetect"


@pytest.fixture(autouse=True)
def _no_host_detection(monkeypatch):
    """Default: nothing detected, regardless of the host running the test."""
    monkeypatch.setattr(AgentCatalogDetector, "detect", lambda self, *, force=False: {})


def _stub_detection(monkeypatch, statuses: dict[str, CatalogDetection]) -> None:
    monkeypatch.setattr(AgentCatalogDetector, "detect", lambda self, *, force=False: statuses)


def _member_client(client, username="registry-member", password="pw1"):
    response = client.post("/api/users", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _document() -> dict:
    return {
        "api_base": "http://127.0.0.1:8000",
        "agents": [
            {
                "id": "kimi-acp",
                "label": "Kimi Code (ACP)",
                "command": "kimi",
                "args": ["acp"],
            }
        ],
    }


def test_anonymous_and_member_are_rejected(client, anon_client) -> None:
    assert anon_client.get(REGISTRY_URL).status_code == 401
    assert anon_client.put(REGISTRY_URL, json=_document()).status_code == 401
    assert anon_client.post(REDETECT_URL).status_code == 401
    member = _member_client(client)
    assert member.get(REGISTRY_URL).status_code == 403
    assert member.put(REGISTRY_URL, json=_document()).status_code == 403
    assert member.post(REDETECT_URL).status_code == 403


def _strip_probes(payload: dict) -> dict[str, bool]:
    availability = payload.pop("availability")
    assert all(isinstance(value, bool) for value in availability.values())
    detection = payload.pop("detection")
    assert all(set(item) == {"detected", "path", "version"} for item in detection.values())
    return availability


def test_default_document_and_roundtrip(client) -> None:
    response = client.get(REGISTRY_URL)
    assert response.status_code == 200
    payload = response.json()
    empty_revision = _strip_probes_with_revision(payload)
    assert payload == {"api_base": "http://127.0.0.1:8000", "agents": []}

    document = _document()
    response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    expected = _document()
    # source is server-managed provenance (#332): admin-written rows are manual.
    expected["agents"][0]["source"] = "manual"
    payload = response.json()
    availability = _strip_probes(payload)
    assert set(availability) == {"kimi-acp"}
    put_revision = payload.pop("revision")
    assert payload == expected
    payload = client.get(REGISTRY_URL).json()
    availability = _strip_probes(payload)
    assert set(availability) == {"kimi-acp"}
    expected_revision = payload.pop("revision")
    assert payload == expected
    # revision 是存储内容版本（#355）：空文档与写入后内容不同→版本不同；
    # 同一内容 GET/PUT 响应一致且稳定，探测结果不参与计算。
    assert expected_revision == put_revision != empty_revision
    assert client.get(REGISTRY_URL).json()["revision"] == expected_revision


def test_validation_rejects_bad_documents(client) -> None:
    document = _document()
    document["agents"].append(document["agents"][0].copy())
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["id"] = "BAD ID"
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["command"] = ""
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["unknown"] = 1
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["unknown"] = 1
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["source"] = "bogus"
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["api_base"] = ""
    assert client.put(REGISTRY_URL, json=document).status_code == 422


def _strip_probes_with_revision(payload: dict) -> str:
    """剥掉响应端派生字段，返回 revision（#355 测试助手）。"""
    _strip_probes(payload)
    revision = payload.pop("revision")
    assert isinstance(revision, str) and revision
    return revision


def test_put_with_stale_revision_conflicts_and_keeps_detected_rows(client, monkeypatch) -> None:
    """#355：快照后探测合并进新 detected 行 → 陈旧版本 PUT 得 409，
    注册表原样保留（新 detected 行存活），且响应附当前文档与版本。"""
    snapshot = client.get(REGISTRY_URL).json()
    stale_revision = _strip_probes_with_revision(snapshot)
    # 快照与 PUT 之间：探测合并进一个新 detected 行（模拟启动探测/redetect
    # 先拿到行锁提交——正是 issue 中丢失更新的窗口）。
    _stub_detection(
        monkeypatch, {"kimi": CatalogDetection(True, "/usr/local/bin/kimi", "kimi 0.55.0")}
    )
    assert client.post(REDETECT_URL).status_code == 200

    stale_payload = _document()  # 管理员仍基于旧快照编辑（不含 kimi 行）
    stale_payload["revision"] = stale_revision
    response = client.put(REGISTRY_URL, json=stale_payload)
    assert response.status_code == 409, response.text
    conflict = response.json()
    current_revision = _strip_probes_with_revision(conflict)
    assert [agent["id"] for agent in conflict["agents"]] == ["kimi"]
    assert conflict["agents"][0]["source"] == "detected"
    assert current_revision != stale_revision

    # 存储未被陈旧快照覆盖：detected 行原样存活。
    persisted = client.get(REGISTRY_URL).json()
    assert _strip_probes_with_revision(persisted) == current_revision
    assert [agent["id"] for agent in persisted["agents"]] == ["kimi"]
    assert persisted["agents"][0]["source"] == "detected"

    # 管理员刷新快照（带当前版本）重放同一编辑即可成功。
    refreshed_payload = _document()
    refreshed_payload["revision"] = current_revision
    saved = client.put(REGISTRY_URL, json=refreshed_payload).json()
    assert [agent["id"] for agent in saved["agents"]] == ["kimi-acp"]
    assert saved["agents"][0]["source"] == "manual"


def test_put_with_current_revision_merges_and_succeeds(client, monkeypatch) -> None:
    """#355：携带当前版本的 PUT 正常合并保存（流程 b）。"""
    _stub_detection(monkeypatch, {"kimi": CatalogDetection(True, "/usr/local/bin/kimi", None)})
    current_revision = _strip_probes_with_revision(client.post(REDETECT_URL).json())

    # 整份回放检测到的文档、只改 label：未改行保 source=detected（#332 合并）。
    payload = client.get(REGISTRY_URL).json()
    payload.pop("availability")
    payload.pop("detection")
    payload["agents"][0]["label"] = "Kimi Code (customized)"
    payload["revision"] = current_revision
    response = client.put(REGISTRY_URL, json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["agents"][0]["label"] == "Kimi Code (customized)"
    assert saved["agents"][0]["source"] == "manual"  # 编辑即接管（#332）
    assert _strip_probes_with_revision(saved) != current_revision  # 内容变了版本变


def test_put_without_revision_bypasses_the_conflict_check(client, monkeypatch) -> None:
    """#355：省略 revision 的 legacy 客户端保持旧的整份替换语义（不 409）。"""
    _stub_detection(monkeypatch, {"kimi": CatalogDetection(True, "/usr/local/bin/kimi", None)})
    assert client.post(REDETECT_URL).status_code == 200
    # smoke 脚本式一次性写：不带 revision 直接整份替换。
    document = _document()
    response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    assert [agent["id"] for agent in response.json()["agents"]] == ["kimi-acp"]


def test_api_base_must_be_a_plain_http_url(client) -> None:
    """#158: api_base is the scoped-token egress target — only plain absolute
    http(s) URLs (no credentials, query, or fragment) are accepted."""
    for bad in (
        "ftp://example.com",
        "not-a-url",
        "//example.com/path",
        "https://user:pw@example.com",
        "http://example.com/?x=1",
        "http://example.com/#frag",
    ):
        document = _document()
        document["api_base"] = bad
        assert client.put(REGISTRY_URL, json=document).status_code == 422, bad


def test_external_api_base_accepted_but_logged(client, caplog) -> None:
    """#158: an external api_base is allowed (remote deployments) but loud."""
    document = _document()
    document["api_base"] = "https://studio.example.com"
    with caplog.at_level(logging.WARNING):
        response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    assert any("api_base" in record.message for record in caplog.records)

    caplog.clear()
    document["api_base"] = "http://192.168.1.20:8000"
    with caplog.at_level(logging.WARNING):
        response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    assert not any("api_base" in record.message for record in caplog.records)


def test_get_reports_per_agent_detection_status(client, monkeypatch) -> None:
    _stub_detection(
        monkeypatch,
        {
            "kimi": CatalogDetection(True, "/usr/local/bin/kimi", "kimi, version 0.55.0"),
            "codex": CatalogDetection(False),
        },
    )
    payload = client.get(REGISTRY_URL).json()
    assert payload["detection"] == {
        "kimi": {
            "detected": True,
            "path": "/usr/local/bin/kimi",
            "version": "kimi, version 0.55.0",
        },
        "codex": {"detected": False, "path": None, "version": None},
    }


def test_redetect_merges_detected_catalog_entries(client, monkeypatch) -> None:
    _stub_detection(
        monkeypatch,
        {"kimi": CatalogDetection(True, "/usr/local/bin/kimi", "kimi 0.55.0")},
    )
    response = client.post(REDETECT_URL)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [agent["id"] for agent in payload["agents"]] == ["kimi"]
    kimi = payload["agents"][0]
    assert kimi["source"] == "detected"
    assert kimi["command"] == "kimi" and kimi["args"] == ["acp"]
    # The merge is persisted, not just response sugar.
    persisted = client.get(REGISTRY_URL).json()
    assert [agent["id"] for agent in persisted["agents"]] == ["kimi"]


def test_redetect_never_overrides_manual_entries(client, monkeypatch) -> None:
    document = {
        "api_base": "http://127.0.0.1:8000",
        "agents": [
            {"id": "kimi", "label": "My kimi", "command": "/opt/kimi", "args": ["acp"]},
            {"id": "mine", "label": "Mine", "command": "mine", "args": []},
        ],
    }
    assert client.put(REGISTRY_URL, json=document).status_code == 200
    _stub_detection(
        monkeypatch,
        {
            "kimi": CatalogDetection(True, "/usr/bin/kimi", None),
            "codex": CatalogDetection(True, "/usr/bin/codex-acp", "codex 1.0"),
        },
    )
    payload = client.post(REDETECT_URL).json()
    agents = {agent["id"]: agent for agent in payload["agents"]}
    # Manual kimi row untouched (same id wins over the detected template);
    # catalog codex appended as detected.
    assert agents["kimi"]["command"] == "/opt/kimi"
    assert agents["kimi"]["source"] == "manual"
    assert agents["mine"]["source"] == "manual"
    assert agents["codex"]["source"] == "detected"


def test_put_preserves_detected_source_for_unchanged_rows(client, monkeypatch) -> None:
    _stub_detection(monkeypatch, {"kimi": CatalogDetection(True, "/usr/local/bin/kimi", None)})
    detected = client.post(REDETECT_URL).json()["agents"]
    assert detected[0]["source"] == "detected"
    # An old client re-saves the document without the source field: the
    # unchanged detected row keeps its provenance instead of flipping manual.
    legacy_row = {k: v for k, v in detected[0].items() if k != "source"}
    payload = {"api_base": "http://127.0.0.1:8000", "agents": [legacy_row]}
    saved = client.put(REGISTRY_URL, json=payload).json()
    assert saved["agents"][0]["source"] == "detected"
    # Editing the detected row makes it manual — detection never reclaims it.
    payload["agents"][0]["label"] = "Customized"
    saved = client.put(REGISTRY_URL, json=payload).json()
    assert saved["agents"][0]["source"] == "manual"
    redetected = client.post(REDETECT_URL).json()
    rows = {agent["id"]: agent for agent in redetected["agents"]}
    assert rows["kimi"]["label"] == "Customized"
    assert rows["kimi"]["source"] == "manual"
