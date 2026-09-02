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

from server.app.studio_chat.agent_catalog import (
    CatalogDetection,
    merge_detected_into_document,
    merge_manual_edit,
)
from server.app.studio_chat.registry import (
    GLOBAL_SETTINGS_KEY,
    StudioAgentRegistryStore,
    default_registry_document,
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
