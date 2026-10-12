"""Studio agent registry store: defaults + detected/manual source merges (#332).

The KV layer is faked in memory; the fake mirrors the real mixin's JSON
round-trip so non-serializable documents fail here too. Transactional RMW
itself is covered by the queries-layer tests; these tests pin the merge
semantics that ride on it (manual entries never overridden, same-id manual
wins, stale detected rows refreshed).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from server.app.studio_chat import registry as registry_module
from server.app.studio_chat.agent_catalog import (
    CatalogDetection,
    merge_detected_into_document,
    merge_manual_edit,
)
from server.app.studio_chat.registry import (
    GLOBAL_SETTINGS_KEY,
    RegistryVersionMismatch,
    StudioAgentRegistryStore,
    default_registry_document,
    registry_revision,
)

pytestmark = pytest.mark.no_db


class FakeGlobalSettingsKV:
    """In-memory stand-in for the global_settings KV mixin (#281)."""

    def __init__(self, document: dict[str, Any] | None = None) -> None:
        self._document = document

    def get_global_settings_document(self, key: str) -> dict[str, Any] | None:
        assert key == GLOBAL_SETTINGS_KEY
        return self._document

    def put_global_settings_document(self, key: str, document: dict[str, Any]) -> None:
        assert key == GLOBAL_SETTINGS_KEY
        self._document = json.loads(json.dumps(document))

    def update_global_settings_document(self, key: str, updater) -> None:
        assert key == GLOBAL_SETTINGS_KEY
        self._document = json.loads(json.dumps(updater(self._document or {})))


def _store(document: dict[str, Any] | None = None) -> StudioAgentRegistryStore:
    return StudioAgentRegistryStore(FakeGlobalSettingsKV(document))


def _statuses(*ids: str) -> dict[str, CatalogDetection]:
    return {agent_id: CatalogDetection(True, f"/usr/bin/{agent_id}", "1.0") for agent_id in ids}


def test_get_returns_defaults_when_nothing_stored() -> None:
    assert _store().get() == default_registry_document()


def test_get_normalizes_legacy_document_without_source() -> None:
    stored = {"api_base": "http://10.0.0.2:8000", "agents": [{"id": "x", "command": "x"}]}
    document = _store(stored).get()
    assert document["api_base"] == "http://10.0.0.2:8000"
    assert document["agents"] == [{"id": "x", "command": "x"}]


def test_update_merges_detected_entries_transactionally() -> None:
    store = _store({"api_base": "http://127.0.0.1:8000", "agents": []})
    store.update(lambda stored: merge_detected_into_document(stored, _statuses("kimi")))
    document = store.get()
    assert [agent["id"] for agent in document["agents"]] == ["kimi"]
    assert document["agents"][0]["source"] == "detected"
    assert document["api_base"] == "http://127.0.0.1:8000"
    # A second pass with the same detection is idempotent.
    store.update(lambda stored: merge_detected_into_document(stored, _statuses("kimi")))
    assert [agent["id"] for agent in store.get()["agents"]] == ["kimi"]


def test_manual_entry_wins_over_detection_and_survives_redetection() -> None:
    store = _store(None)
    store.update(
        lambda stored: merge_manual_edit(
            {
                "api_base": "http://127.0.0.1:8000",
                "agents": [{"id": "kimi", "label": "Mine", "command": "/opt/kimi", "args": []}],
            },
            stored,
        )
    )
    store.update(lambda stored: merge_detected_into_document(stored, _statuses("kimi", "codex")))
    agents = store.get()["agents"]
    assert [agent["id"] for agent in agents] == ["kimi", "codex"]
    assert agents[0] == {
        "id": "kimi",
        "label": "Mine",
        "command": "/opt/kimi",
        "args": [],
        "source": "manual",
    }
    assert agents[1]["source"] == "detected"


def test_admin_edit_of_detected_entry_flips_it_to_manual() -> None:
    store = _store(None)
    store.update(lambda stored: merge_detected_into_document(stored, _statuses("kimi")))
    detected = store.get()["agents"][0]
    assert detected["source"] == "detected"
    # Whole-document PUT whose only change is the detected row's args.
    edited = dict(detected, args=["acp", "--verbose"])
    del edited["source"]  # old clients do not round-trip source
    store.update(
        lambda stored: merge_manual_edit(
            {"api_base": "http://127.0.0.1:8000", "agents": [edited]}, stored
        )
    )
    assert store.get()["agents"][0]["source"] == "manual"
    # Detection now leaves the admin-owned row alone even though the id is in
    # the catalog and still detected.
    store.update(lambda stored: merge_detected_into_document(stored, _statuses("kimi")))
    assert store.get()["agents"][0]["args"] == ["acp", "--verbose"]


def test_find_agent_covers_detected_entries() -> None:
    store = _store(None)
    store.update(lambda stored: merge_detected_into_document(stored, _statuses("kimi")))
    found = store.find_agent("kimi")
    assert found is not None and found["command"] == "kimi"
    assert store.find_agent("nope") is None


def test_registry_revision_is_content_stable_and_probe_independent() -> None:
    """#355：revision 仅由存储文档的 agents 与显式 api_base 决定——同一文档
    稳定，内容变化才变化，键序与响应端派生字段不参与。"""
    document = {"api_base": "http://127.0.0.1:8000", "agents": []}
    assert registry_revision(document) == registry_revision(dict(document))
    assert registry_revision(document) != registry_revision(
        dict(document, agents=[{"id": "kimi", "label": "K", "command": "kimi", "args": []}])
    )
    assert registry_revision(document) != registry_revision(
        dict(document, api_base="http://127.0.0.1:9000")
    )
    # 键序不影响版本（canonical 序列化）；无行与空文档同版本（RMW 的
    # insert-if-absent 读到的就是 {}）。
    agent = {"id": "kimi", "label": "K", "command": "kimi", "args": []}
    assert registry_revision({"agents": [agent], "api_base": "b"}) == registry_revision(
        {"api_base": "b", "agents": [dict(agent)]}
    )
    assert registry_revision(None) == registry_revision({})
    # 响应端派生字段（探测/可用性/携带的 revision 本身）不改变版本。
    assert registry_revision(document) == registry_revision(
        dict(document, detection={"kimi": {"detected": True}}, revision="x")
    )


def test_revision_ignores_derived_api_base_when_unconfigured(monkeypatch) -> None:
    """#355 + #915：api_base 未显式存储时 GET 按进程实际地址推导默认值，推导
    值随重启/端口变化——revision 输入只含显式存储的 api_base，推导地址变化
    不得让旧快照被误判为陈旧（假 409）。"""
    store = _store({"agents": [{"id": "kimi", "label": "K", "command": "kimi", "args": []}]})
    monkeypatch.setattr(registry_module, "derived_api_base", lambda: "http://127.0.0.1:8000")
    document, revision = store.get_with_revision()
    assert document["api_base"] == "http://127.0.0.1:8000"

    # 进程重启/换端口后推导地址变了：GET 文档反映新地址，版本不变。
    monkeypatch.setattr(registry_module, "derived_api_base", lambda: "http://10.0.0.2:5555")
    derived, revision_after = store.get_with_revision()
    assert derived["api_base"] == "http://10.0.0.2:5555"
    assert revision_after == revision

    # 携带旧版本（推导地址变化前签发）的 PUT 仍被接受——不假陈旧。
    payload = {"api_base": "http://10.0.0.2:5555", "agents": document["agents"]}
    merged = store.conditional_put(revision, merge_manual_edit, payload)
    assert merged["api_base"] == "http://10.0.0.2:5555"


def test_conditional_put_rejects_stale_revision_without_touching_storage() -> None:
    """#355：行锁内版本不匹配 → 抛 RegistryVersionMismatch，存储文档（含新
    detected 行）原样保留。"""
    store = _store({"api_base": "http://127.0.0.1:8000", "agents": []})
    _, stale_revision = store.get_with_revision()
    # 快照后探测合并进新 detected 行：存储版本已前进。
    store.update(lambda stored: merge_detected_into_document(stored, _statuses("kimi")))
    stale_payload = {"api_base": "http://127.0.0.1:8000", "agents": []}
    with pytest.raises(RegistryVersionMismatch):
        store.conditional_put(stale_revision, merge_manual_edit, stale_payload)
    # 陈旧快照没有覆盖掉 detected 行。
    agents = store.get()["agents"]
    assert [agent["id"] for agent in agents] == ["kimi"]
    assert agents[0]["source"] == "detected"


def test_conditional_put_accepts_current_revision_and_empty_revision() -> None:
    """#355：当前版本匹配正常写入并返回合并后的最终文档（调用方据此构建
    响应，revision 即刚落库的这次写入）；空版本跳过检查（一次性客户端的
    整份替换语义不变）。"""
    store = _store({"api_base": "http://127.0.0.1:8000", "agents": []})
    _, current_revision = store.get_with_revision()
    payload = {"api_base": "http://127.0.0.1:9000", "agents": []}
    merged = store.conditional_put(current_revision, merge_manual_edit, payload)
    assert merged == {"api_base": "http://127.0.0.1:9000", "agents": []}
    assert store.get()["api_base"] == "http://127.0.0.1:9000"
    # 返回的合并文档就是存储文档：二者的 revision 一致。
    _, stored_revision = store.get_with_revision()
    assert registry_revision(merged) == stored_revision
    # 空 revision：不比对，直接写入。
    store.conditional_put(
        "", merge_manual_edit, {"api_base": "http://127.0.0.1:8000", "agents": []}
    )
    assert store.get()["api_base"] == "http://127.0.0.1:8000"
