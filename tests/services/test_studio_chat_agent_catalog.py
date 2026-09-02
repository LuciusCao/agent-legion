"""Built-in ACP agent catalog + detector behavior (issue #332).

All probes are faked (which/runner/clock injection): these tests never touch
the host PATH or spawn real processes.
"""

from __future__ import annotations

import logging
import re
import subprocess

import pytest

from server.app.routes.studio_agents_admin_contracts import StudioAgentRegistryEntry
from server.app.studio_chat.agent_catalog import (
    AGENT_CATALOG,
    AgentCatalogDetector,
    CatalogDetection,
    detected_ids,
    merge_detected_into_document,
    merge_manual_edit,
    redetect_and_merge,
    spawn_startup_detection,
)

pytestmark = pytest.mark.no_db


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout)


def _detector(
    binaries: dict[str, str],
    *,
    runner=None,
    clock: _FakeClock | None = None,
) -> AgentCatalogDetector:
    return AgentCatalogDetector(
        60.0,
        which=lambda command: binaries.get(command),
        runner=runner or (lambda *a, **k: _completed("agent 1.2.3\n")),
        clock=(clock or _FakeClock()),
    )


def test_catalog_entries_are_valid_registry_templates() -> None:
    ids = [entry.id for entry in AGENT_CATALOG]
    assert len(ids) == len(set(ids)), "catalog ids must be unique"
    for entry in AGENT_CATALOG:
        # The id pattern mirrors the registry contract; the template must
        # validate as a registry entry as-is.
        assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*", entry.id)
        StudioAgentRegistryEntry(
            id=entry.id, label=entry.label, command=entry.command, args=list(entry.args)
        )
        assert entry.executables, "every catalog entry needs a detection executable"


def test_detect_reports_missing_binary_without_version_probe() -> None:
    calls: list = []
    detector = _detector({}, runner=lambda *a, **k: calls.append(a))
    statuses = detector.detect()
    assert set(statuses) == {entry.id for entry in AGENT_CATALOG}
    assert all(not status.detected and status.path is None for status in statuses.values())
    assert calls == [], "version probe must not run when the binary is missing"


def test_detect_resolves_path_and_parses_first_version_line() -> None:
    detector = _detector(
        {"kimi": "/usr/local/bin/kimi"},
        runner=lambda *a, **k: _completed("\n  \nkimi, version 0.55.0\nmore\n"),
    )
    status = detector.detect()["kimi"]
    assert status == CatalogDetection(
        detected=True, path="/usr/local/bin/kimi", version="kimi, version 0.55.0"
    )


def test_version_probe_failures_degrade_silently() -> None:
    binaries = {"kimi": "/usr/local/bin/kimi"}
    # Timeout: detection still succeeds (the binary exists), version unknown.
    detector = _detector(
        binaries,
        runner=lambda *a, **k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd=["kimi"], timeout=5)
        ),
    )
    assert detector.detect()["kimi"] == CatalogDetection(True, "/usr/local/bin/kimi", None)
    # Non-zero exit.
    detector = _detector(binaries, runner=lambda *a, **k: _completed("boom", returncode=1))
    assert detector.detect()["kimi"].version is None
    # Spawn failure (binary raced away after which).
    detector = _detector(
        binaries, runner=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("gone"))
    )
    assert detector.detect()["kimi"].version is None
    # Empty output.
    detector = _detector(binaries, runner=lambda *a, **k: _completed("  \n\n"))
    assert detector.detect()["kimi"].version is None


def test_detect_caches_within_ttl_and_force_bypasses() -> None:
    clock = _FakeClock()
    which_calls: list[str] = []
    detector = AgentCatalogDetector(
        60.0,
        which=lambda command: which_calls.append(command) or None,
        clock=clock,
    )
    detector.detect()
    detector.detect()
    first_pass = len(which_calls)
    assert first_pass == sum(len(entry.executables) for entry in AGENT_CATALOG)
    clock.now = 61.0
    detector.detect()
    assert len(which_calls) == first_pass * 2
    detector.detect(force=True)
    assert len(which_calls) == first_pass * 3


def test_detected_ids_filters_to_present_agents() -> None:
    statuses = {
        "kimi": CatalogDetection(True, "/usr/bin/kimi", None),
        "codex": CatalogDetection(False),
    }
    assert detected_ids(statuses) == {"kimi"}


def _statuses(*ids: str) -> dict[str, CatalogDetection]:
    return {agent_id: CatalogDetection(True, f"/usr/bin/{agent_id}", None) for agent_id in ids}


def test_merge_detected_appends_missing_and_refreshes_stale() -> None:
    document = {
        "api_base": "http://127.0.0.1:8000",
        "agents": [
            {"id": "mine", "label": "Mine", "command": "mine", "args": [], "source": "manual"},
            {
                "id": "kimi",
                "label": "stale label",
                "command": "kimi",
                "args": ["acp"],
                "source": "detected",
            },
            {"id": "codex", "label": "Gone", "command": "codex", "args": [], "source": "detected"},
        ],
    }
    merged = merge_detected_into_document(document, _statuses("kimi"))
    # api_base preserved; manual entry untouched; codex dropped (vanished);
    # kimi re-added from the fresh template (stale label gone).
    assert merged["api_base"] == "http://127.0.0.1:8000"
    assert [agent["id"] for agent in merged["agents"]] == ["mine", "kimi"]
    kimi = merged["agents"][1]
    assert kimi["source"] == "detected"
    template = next(entry for entry in AGENT_CATALOG if entry.id == "kimi")
    assert kimi["label"] == template.label
    assert kimi["command"] == template.command
    assert kimi["args"] == list(template.args)


def test_merge_detected_never_overrides_manual_same_id() -> None:
    document = {
        "agents": [
            {
                "id": "kimi",
                "label": "My custom kimi",
                "command": "/opt/kimi-custom",
                "args": ["acp", "--x"],
                "source": "manual",
            }
        ]
    }
    merged = merge_detected_into_document(document, _statuses("kimi", "codex"))
    assert [agent["id"] for agent in merged["agents"]] == ["kimi", "codex"]
    assert merged["agents"][0]["command"] == "/opt/kimi-custom"
    assert merged["agents"][0]["source"] == "manual"
    assert merged["agents"][1]["source"] == "detected"


def test_merge_detected_treats_missing_source_as_manual() -> None:
    """Legacy registry rows carry no source marker: they are admin-owned and
    must survive detection passes untouched."""
    document = {"agents": [{"id": "kimi", "label": "Legacy", "command": "kimi", "args": ["acp"]}]}
    merged = merge_detected_into_document(document, _statuses("kimi"))
    assert merged["agents"] == [
        {"id": "kimi", "label": "Legacy", "command": "kimi", "args": ["acp"]}
    ]


def test_merge_detected_on_empty_document() -> None:
    merged = merge_detected_into_document({}, _statuses("kimi"))
    assert [agent["id"] for agent in merged["agents"]] == ["kimi"]
    assert merged["agents"][0]["source"] == "detected"


def test_merge_manual_edit_keeps_source_only_for_unchanged_rows() -> None:
    stored = {
        "agents": [
            {
                "id": "kimi",
                "label": "Kimi Code",
                "command": "kimi",
                "args": ["acp"],
                "source": "detected",
            }
        ]
    }
    # An identical re-save (client did not round-trip source) keeps detected.
    incoming = {
        "agents": [{"id": "kimi", "label": "Kimi Code", "command": "kimi", "args": ["acp"]}]
    }
    assert merge_manual_edit(incoming, stored)["agents"][0]["source"] == "detected"
    # Any edit flips the row to manual (admin took ownership).
    edited = {"agents": [{"id": "kimi", "label": "Kimi Code", "command": "kimi", "args": []}]}
    assert merge_manual_edit(edited, stored)["agents"][0]["source"] == "manual"


def test_merge_manual_edit_forces_new_or_forged_rows_manual() -> None:
    incoming = {
        "agents": [
            {"id": "brand-new", "label": "X", "command": "x", "args": [], "source": "detected"}
        ]
    }
    assert merge_manual_edit(incoming, {})["agents"][0]["source"] == "manual"


class _FakeStore:
    def __init__(self, document: dict) -> None:
        self._document = document

    def get(self) -> dict:
        return self._document

    def update(self, updater) -> None:
        self._document = updater(self._document)


def test_redetect_and_merge_forces_fresh_detection_and_returns_document() -> None:
    clock = _FakeClock()
    detector = _detector({"kimi": "/usr/bin/kimi"}, clock=clock)
    detector.detect()  # prime the TTL cache
    store = _FakeStore({"api_base": "http://127.0.0.1:8000", "agents": []})
    document = redetect_and_merge(store, detector)
    assert [agent["id"] for agent in document["agents"]] == ["kimi"]
    assert document["agents"][0]["source"] == "detected"


class _CapturedThread:
    """Stand-in for threading.Thread: captures kwargs, runs target on demand."""

    def __init__(self) -> None:
        self.kwargs: dict = {}
        self.started = False


def _patch_thread(monkeypatch) -> _CapturedThread:
    captured = _CapturedThread()

    class _FakeThread:
        def __init__(self, **kwargs) -> None:
            captured.kwargs = kwargs

        def start(self) -> None:
            captured.started = True

    monkeypatch.setattr("server.app.studio_chat.agent_catalog.threading.Thread", _FakeThread)
    return captured


def test_spawn_startup_detection_runs_merge_on_daemon_thread(monkeypatch) -> None:
    captured = _patch_thread(monkeypatch)
    store = _FakeStore({"api_base": "http://127.0.0.1:8000", "agents": []})
    detector = _detector({"kimi": "/usr/bin/kimi"})
    monkeypatch.setattr(
        "server.app.studio_chat.agent_catalog.AgentCatalogDetector", lambda: detector
    )
    spawn_startup_detection(store)
    assert captured.started and captured.kwargs["daemon"] is True
    captured.kwargs["target"]()  # run the thread body synchronously
    agents = store.get()["agents"]
    assert [(agent["id"], agent["source"]) for agent in agents] == [("kimi", "detected")]


def test_startup_detection_failure_is_logged_and_swallowed(monkeypatch, caplog) -> None:
    captured = _patch_thread(monkeypatch)

    class _BoomDetector:
        def detect(self, *, force: bool = False):
            raise RuntimeError("probe blew up")

    monkeypatch.setattr(
        "server.app.studio_chat.agent_catalog.AgentCatalogDetector", lambda: _BoomDetector()
    )
    store = _FakeStore({"api_base": "http://127.0.0.1:8000", "agents": []})
    spawn_startup_detection(store)
    with caplog.at_level(logging.ERROR):
        captured.kwargs["target"]()  # must not raise
    assert any("startup detection failed" in record.message for record in caplog.records)
    assert store.get()["agents"] == []
