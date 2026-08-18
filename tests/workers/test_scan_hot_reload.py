"""Worker scan-list hot reload when workflow keys register at runtime."""

from __future__ import annotations

import json
import threading

import pytest

from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_catalog_store import WorkflowCatalogStore
from server.app.workflow_worker import thread as thread_module
from server.app.workflow_worker.catalog_scan import iter_scan_entries
from server.app.workflow_worker.thread import WorkflowWorkerThread
from server.app.workflows.builtin import BUILTIN_WORKFLOW_DEFINITIONS


def _make_thread(settings, job_db=None) -> WorkflowWorkerThread:
    # reload_scan_entries only reads the catalog through settings; the
    # lease/runtime machinery is not exercised by these tests.
    return WorkflowWorkerThread(
        job_db=job_db,
        leases=None,
        runtime=None,
        settings=settings,
    )


def test_reload_picks_up_newly_registered_definitionless_key(settings) -> None:
    worker = _make_thread(settings)
    worker.reload_scan_entries()
    previous_definitions, previous_keys = worker._scan_entries
    assert previous_keys == []

    WorkflowCatalogService(settings).register("acme_quiz_flow", "Acme Quiz")
    worker.reload_scan_entries()

    definitions, definitionless_keys = worker._scan_entries
    assert definitionless_keys == ["acme_quiz_flow"]
    entries = iter_scan_entries(definitions, definitionless_keys)
    assert ("acme_quiz_flow", None) in entries
    # Snapshot swap, not in-place mutation: earlier readers keep their view.
    assert previous_keys == []
    assert previous_definitions is not definitions


def test_reload_picks_up_definition_backed_entry(settings) -> None:
    worker = _make_thread(settings)
    worker.reload_scan_entries()
    assert "acme_defined_flow" not in {d.key for d in worker._scan_entries[0]}

    raw = dict(BUILTIN_WORKFLOW_DEFINITIONS["education_video_problems_generation"])
    raw["key"] = "acme_defined_flow"
    WorkflowCatalogStore(settings.database_url).upsert_builtin(
        key="acme_defined_flow",
        label="Acme Defined",
        definition_json=json.dumps(raw, ensure_ascii=False),
    )
    worker.reload_scan_entries()

    definitions, definitionless_keys = worker._scan_entries
    assert "acme_defined_flow" in {d.key for d in definitions}
    assert "acme_defined_flow" not in definitionless_keys


def test_reload_keeps_previous_snapshot_when_load_fails(settings, monkeypatch) -> None:
    worker = _make_thread(settings)
    worker.reload_scan_entries()
    snapshot = worker._scan_entries

    def _fail(_settings):
        raise RuntimeError("catalog read failed")

    monkeypatch.setattr(thread_module, "load_workflow_scan_entries", _fail)
    with pytest.raises(RuntimeError, match="catalog read failed"):
        worker.reload_scan_entries()

    assert worker._scan_entries is snapshot


def test_reload_swap_stays_consistent_under_concurrent_reads(settings, monkeypatch) -> None:
    worker = _make_thread(settings)
    counter = 0
    counter_lock = threading.Lock()

    def _next_generation(_settings):
        nonlocal counter
        with counter_lock:
            counter += 1
            generation = counter
        # The generation number ties the pair together: a torn swap would
        # let a reader observe mismatched halves.
        return ([f"definition-{generation}"], [f"key-{generation}"])

    monkeypatch.setattr(thread_module, "load_workflow_scan_entries", _next_generation)
    worker.reload_scan_entries()

    errors: list[str] = []
    stop = threading.Event()

    def _reader() -> None:
        while not stop.is_set():
            try:
                definitions, definitionless_keys = worker._scan_entries
                if not definitions or not definitionless_keys:
                    # Brief wait: a bare continue would busy-spin a core.
                    stop.wait(0.001)
                    continue
                def_generation = definitions[0].rsplit("-", 1)[1]
                key_generation = definitionless_keys[0].rsplit("-", 1)[1]
                if def_generation != key_generation:
                    errors.append("torn scan snapshot observed")
            except Exception as exc:
                # An unexpected reader failure must fail the test, not exit
                # the thread silently and shrink the reader pool (假绿窗口).
                errors.append(f"reader thread failed: {exc!r}")
                return

    def _writer() -> None:
        for _ in range(300):
            worker.reload_scan_entries()

    readers = [threading.Thread(target=_reader) for _ in range(4)]
    for reader in readers:
        reader.start()
    _writer()
    stop.set()
    for reader in readers:
        reader.join(timeout=5)
        assert not reader.is_alive(), "reader thread did not exit"

    assert errors == []
    # Converges on the last generation once the writer stops.
    definitions, definitionless_keys = worker._scan_entries
    assert definitions == [f"definition-{counter}"]
    assert definitionless_keys == [f"key-{counter}"]


def test_start_loads_scan_entries_via_reload(settings, job_db, monkeypatch) -> None:
    worker = _make_thread(settings, job_db=job_db)
    reload_calls = 0

    def _record() -> None:
        nonlocal reload_calls
        reload_calls += 1

    monkeypatch.setattr(worker, "reload_scan_entries", _record)
    worker.start()
    worker.stop()

    assert reload_calls == 1
