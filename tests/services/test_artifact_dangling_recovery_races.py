"""Deterministic PostgreSQL race matrix for constrained artifact apply (#1019)."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from server.app.db.transaction import write_transaction
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.artifact_dangling_plan import build_artifact_dangling_plan
from server.app.services.artifact_dangling_probe import s3_configuration_fingerprint
from server.app.services.artifact_dangling_recovery import execute_apply, prepare_apply
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_rerun.recovery_plan import RecoveryPlanGuard
from server.app.services.job_rerun.single import commit_rerun
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage.s3_settings import S3Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflows.revision_format import serialize_definition
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.fakes.artifact_rows import seed_artifact_row_tx
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL

S3_SETTINGS = S3Settings(bucket="race-bucket", endpoint_url="https://race.invalid")


class FakeRecoveryStorage(FakeObjectStorage):
    def head_bucket(self) -> None:
        return None

    def iter_object_pages(self, prefix: str, *, page_size: int) -> Iterator[list[str]]:
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        for start in range(0, len(keys), page_size):
            yield keys[start : start + page_size]


def definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="ws",
        label="ws",
        intake=WorkflowIntake(),
        nodes={
            "producer": WorkflowNode(
                key="producer", label="Producer", capability="produce", outputs=["result.json"]
            ),
            "consumer": WorkflowNode(
                key="consumer",
                label="Consumer",
                capability="consume",
                after=["producer"],
                inputs=["result.json"],
            ),
        },
    )


def seed_job(
    job_db: JobQueries,
    settings: Any,
    storage: FakeRecoveryStorage,
    source_id: str,
    *,
    healthy: bool = False,
    legacy_definition: bool = False,
) -> tuple[dict[str, Any], str, bytes]:
    workflow = definition()
    job = job_db.create_job(
        workflow_key="ws",
        source_type="test",
        source_id=source_id,
        run_id="",
        title=source_id,
        node_keys=["producer", "consumer"],
        workspace_id="ws",
        workflow_revision_id="rev-job",
        workflow_version=1,
        workflow_definition_hash="job-hash",
        workflow_definition_snapshot_json=""
        if legacy_definition
        else serialize_definition(workflow),
    )
    job_id = str(job["id"])
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key='producer'",
            (job_id,),
        )
        conn.execute(
            "update job_nodes set status=%s where job_id=%s and node_key='consumer'",
            ("completed" if healthy else "pending", job_id),
        )
        conn.execute("update jobs set status='completed' where id=%s", (job_id,))
    expected = f"expected:{source_id}".encode()
    key = f"jobs/ws/{job_id}/.v/attempt/result.json"
    if healthy:
        storage.objects[key] = expected
    with write_transaction(TEST_DATABASE_URL) as conn:
        seed_artifact_row_tx(
            conn,
            workspace_id="ws",
            job_id=job_id,
            node_key="producer",
            name="result.json",
            storage_key=key,
            size_bytes=len(expected),
            content_hash=hashlib.sha256(expected).hexdigest(),
        )
    job_dir = resolve_job_dir(job, settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "result.json").write_bytes(expected)
    return job_db.get_job(job_id), key, expected


def build_plan(job_db: JobQueries, storage: FakeRecoveryStorage) -> dict[str, Any]:
    return build_artifact_dangling_plan(
        job_db,
        storage,
        max_candidates=20,
        max_candidate_ratio=1.0,
        thresholds_calibrated=True,
        configuration=s3_configuration_fingerprint(S3_SETTINGS),
    )


def prepare(job_db: JobQueries, storage: FakeRecoveryStorage, plan: dict[str, Any]):
    return prepare_apply(
        job_db,
        storage,
        plan,
        S3_SETTINGS,
        expected_bucket=S3_SETTINGS.bucket,
        expected_endpoint=S3_SETTINGS.endpoint_url,
    )


def rerun_service(job_db: JobQueries, settings: Any, storage: FakeRecoveryStorage):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        object_store=JobArtifactObjectStore(job_db, storage),
    )


def db_state(job_db: JobQueries, job_id: str) -> dict[str, Any]:
    job = job_db.get_job(job_id)
    return {
        "generation": job["execution_generation"],
        "status": job["status"],
        "nodes": {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job_id)},
        "rows": JobArtifactObjectStore(job_db).rows_for_job(job_id),
    }


@pytest.fixture
def workspace(job_db: JobQueries) -> None:
    job_db.create_workspace("Recovery races", workspace_id="ws")
    WorkflowRevisionService(job_db).publish_workspace_revision("ws", definition())


def _candidate(job_db: JobQueries, settings: Any, source_id: str):
    storage = FakeRecoveryStorage()
    candidate, key, expected = seed_job(job_db, settings, storage, source_id)
    seed_job(job_db, settings, storage, f"{source_id}-control", healthy=True)
    plan = build_plan(job_db, storage)
    prepared = prepare(job_db, storage, plan)
    return storage, candidate, key, expected, prepared


def _after_revalidate(monkeypatch: pytest.MonkeyPatch, mutation: Any) -> None:
    import server.app.services.artifact_dangling_recovery as recovery

    original = recovery.revalidate_job

    def wrapped(prepared, job, queries, storage):
        current, definition, guard = original(prepared, job, queries, storage)
        mutation()
        return current, definition, guard

    monkeypatch.setattr(recovery, "revalidate_job", wrapped)


def _mutate_row(job_id: str, key: str, kind: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        if kind == "key":
            conn.execute(
                "update job_artifacts set storage_key=storage_key || '.new' where job_id=%s",
                (job_id,),
            )
        elif kind == "hash":
            conn.execute(
                "update job_artifacts set content_hash=%s where job_id=%s", ("0" * 64, job_id)
            )
        elif kind == "size":
            conn.execute(
                "update job_artifacts set size_bytes=size_bytes+1 where job_id=%s", (job_id,)
            )
        elif kind == "uploaded_at":
            conn.execute(
                "update job_artifacts set uploaded_at=uploaded_at+interval '1 second' where job_id=%s",
                (job_id,),
            )
        elif kind == "same_name":
            seed_artifact_row_tx(
                conn,
                workspace_id="ws",
                job_id=job_id,
                node_key="other_writer",
                name="result.json",
                storage_key=key.replace(".v/attempt", ".v/other"),
                size_bytes=8,
                content_hash="f" * 64,
            )
        elif kind == "generation":
            conn.execute(
                "update jobs set execution_generation=execution_generation+1 where id=%s", (job_id,)
            )
        elif kind == "definition":
            conn.execute(
                "update jobs set workflow_definition_hash='changed' where id=%s", (job_id,)
            )
        elif kind == "job_status":
            conn.execute("update jobs set status='failed' where id=%s", (job_id,))
        elif kind == "node_status":
            conn.execute(
                "update job_nodes set status='completed' where job_id=%s and node_key='consumer'",
                (job_id,),
            )
        elif kind == "execution_control":
            conn.execute("update jobs set execution_mode='until_node' where id=%s", (job_id,))
        elif kind == "storage_dir":
            conn.execute("update jobs set storage_dir='jobs/ws/moved' where id=%s", (job_id,))
        else:
            raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    [
        "generation",
        "key",
        "hash",
        "size",
        "uploaded_at",
        "same_name",
        "definition",
        "job_status",
        "node_status",
        "execution_control",
        "storage_dir",
    ],
)
def test_every_db_pin_is_rechecked_after_lock_free_s3_io(
    job_db: JobQueries,
    settings: Any,
    workspace: None,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    storage, candidate, key, expected, prepared = _candidate(job_db, settings, f"pin-{kind}")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    _after_revalidate(monkeypatch, lambda: _mutate_row(job_id, key, kind))

    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["summary"]["applied_count"] == 0
    assert report["summary"]["not_applied_count"] == 1
    assert report["jobs"][0]["reason_code"] == "stale_plan"
    after = db_state(job_db, job_id)
    assert after["generation"] == before["generation"] + (kind == "generation")
    assert len(after["rows"]) == 1 + (kind == "same_name")
    assert (resolve_job_dir(candidate, settings.jobs_dir) / "result.json").read_bytes() == expected


@pytest.mark.parametrize("busy_kind", ["active_lease", "running_node"])
def test_active_lease_and_running_node_conflict_preserve_the_job(
    job_db: JobQueries,
    settings: Any,
    workspace: None,
    monkeypatch: pytest.MonkeyPatch,
    busy_kind: str,
) -> None:
    storage, candidate, _, expected, prepared = _candidate(job_db, settings, f"busy-{busy_kind}")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)

    def make_busy() -> None:
        with write_transaction(TEST_DATABASE_URL) as conn:
            if busy_kind == "running_node":
                conn.execute(
                    "update job_nodes set status='running' where job_id=%s and node_key='producer'",
                    (job_id,),
                )
                return
            run = conn.execute(
                "insert into node_runs(job_id,node_key,status,command_json,log_path,"
                "run_dir,session_dir,started_at) values (%s,'producer','running','[]','',"
                "'','',current_timestamp) returning id",
                (job_id,),
            ).fetchone()
            conn.execute(
                "insert into executor_leases(id,execution_id,executor_id,workspace_id,job_id,"
                "node_key,node_run_id,status,acquired_at,heartbeat_at,expires_at) values "
                "(%s,%s,'code','ws',%s,'producer',%s,'active',current_timestamp,"
                "current_timestamp,current_timestamp+interval '1 hour')",
                (f"lease-{busy_kind}", f"exec-{busy_kind}", job_id, run["id"]),
            )

    _after_revalidate(monkeypatch, make_busy)
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["jobs"][0]["status"] == "not_applied"
    assert report["jobs"][0]["reason_code"] == "busy"
    after = db_state(job_db, job_id)
    assert after["generation"] == before["generation"]
    assert len(after["rows"]) == 1
    assert (resolve_job_dir(candidate, settings.jobs_dir) / "result.json").read_bytes() == expected


def test_concurrent_rerun_is_stale_and_does_not_bump_twice(
    job_db: JobQueries, settings: Any, workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, candidate, _, _, prepared = _candidate(job_db, settings, "concurrent-rerun")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    service = rerun_service(job_db, settings, storage)
    import server.app.services.artifact_dangling_recovery as recovery

    original = recovery.revalidate_job

    def rerun_between(prepared_arg, job, queries, storage_arg):
        current, definition, guard = original(prepared_arg, job, queries, storage_arg)
        result = commit_rerun(service, current, job_id, "producer", definition=definition)
        assert result["status"] == "succeeded"
        return current, definition, guard

    monkeypatch.setattr(recovery, "revalidate_job", rerun_between)
    report = execute_apply(prepared, service, storage)

    assert report["jobs"][0]["reason_code"] == "stale_plan"
    after = db_state(job_db, job_id)
    assert after["generation"] == before["generation"] + 1
    assert after["rows"] == []


def test_reconciler_insert_before_retire_rolls_back_delete_reset_and_staging(
    job_db: JobQueries, settings: Any, workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, candidate, key, expected, prepared = _candidate(job_db, settings, "reconciler-race")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    store = JobArtifactObjectStore(job_db, storage)
    new_key = key.replace(".v/attempt", ".v/reconciler")
    storage.objects[new_key] = b"new-bytes"
    original = RecoveryPlanGuard.validate

    def promote_before_retire(self, conn, **kwargs):
        original(self, conn, **kwargs)
        store.record_remote_many(
            [
                {
                    "workspace_id": "ws",
                    "job_id": job_id,
                    "node_key": "reconciler",
                    "name": "result.json",
                    "storage_key": new_key,
                    "size_bytes": 9,
                    "content_hash": hashlib.sha256(b"new-bytes").hexdigest(),
                }
            ]
        )

    monkeypatch.setattr(RecoveryPlanGuard, "validate", promote_before_retire)
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["jobs"][0]["status"] == "not_applied"
    assert report["jobs"][0]["reason_code"] == "stale_plan"
    after = db_state(job_db, job_id)
    assert after["generation"] == before["generation"]
    assert after["status"] == before["status"]
    assert after["nodes"] == before["nodes"]
    assert {row["storage_key"] for row in after["rows"]} == {key, new_key}
    assert storage.objects[new_key] == b"new-bytes"
    assert (resolve_job_dir(candidate, settings.jobs_dir) / "result.json").read_bytes() == expected


def test_object_and_positive_control_changes_fail_before_mutation(
    job_db: JobQueries, settings: Any, workspace: None
) -> None:
    storage, candidate, key, _, prepared = _candidate(job_db, settings, "object-race")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    storage.objects[key] = b"expected-now-present"

    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["jobs"][0]["reason_code"] == "stale_plan"
    assert db_state(job_db, job_id) == before


def test_head_bucket_failure_at_job_recheck_is_zero_write(
    job_db: JobQueries,
    settings: Any,
    workspace: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, candidate, _, _, prepared = _candidate(job_db, settings, "health-race")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)

    def unavailable() -> None:
        raise TimeoutError("injected storage outage")

    monkeypatch.setattr(storage, "head_bucket", unavailable)
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["jobs"][0]["reason_code"] == "health_gate_failed"
    assert db_state(job_db, job_id) == before


def _register(store: JobArtifactObjectStore, job_id: str, key: str, node_key: str) -> None:
    store.record_remote_many(
        [
            {
                "workspace_id": "ws",
                "job_id": job_id,
                "node_key": node_key,
                "name": "result.json",
                "storage_key": key,
                "size_bytes": 4,
                "content_hash": hashlib.sha256(b"new!").hexdigest(),
            }
        ]
    )


def _assert_stale_rollback(
    job_db: JobQueries,
    candidate: dict[str, Any],
    settings: Any,
    before: dict[str, Any],
    expected: bytes,
    keys: set[str],
) -> None:
    after = db_state(job_db, str(candidate["id"]))
    assert after["generation"] == before["generation"]
    assert after["status"] == before["status"]
    assert after["nodes"] == before["nodes"]
    assert {row["storage_key"] for row in after["rows"]} == keys
    assert (resolve_job_dir(candidate, settings.jobs_dir) / "result.json").read_bytes() == expected


def test_after_delete_registration_is_rejected_by_final_lock_and_read(
    job_db: JobQueries, settings: Any, workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, candidate, key, expected, prepared = _candidate(job_db, settings, "after-delete")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    store = JobArtifactObjectStore(job_db, storage)
    new_key = key.replace(".v/attempt", ".v/after-delete")
    original = RecoveryPlanGuard.validate_retired_rows

    def register_after_delete(self, conn, rows):
        _register(store, job_id, new_key, "after_delete_writer")
        return original(self, conn, rows)

    monkeypatch.setattr(RecoveryPlanGuard, "validate_retired_rows", register_after_delete)
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["jobs"][0]["status"] == "not_applied"
    assert report["jobs"][0]["reason_code"] == "stale_plan"
    _assert_stale_rollback(job_db, candidate, settings, before, expected, {key, new_key})


def test_after_final_validation_registration_is_rejected(
    job_db: JobQueries, settings: Any, workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, candidate, key, expected, prepared = _candidate(job_db, settings, "after-final")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    store = JobArtifactObjectStore(job_db, storage)
    new_key = key.replace(".v/attempt", ".v/after-final")
    original = job_db.try_artifact_recovery_final_locks_in_transaction

    def register_before_final_lock(conn, locked_job_id, workspace_id):
        _register(store, job_id, new_key, "after_final_writer")
        return original(conn, locked_job_id, workspace_id)

    monkeypatch.setattr(
        job_db,
        "try_artifact_recovery_final_locks_in_transaction",
        register_before_final_lock,
    )
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["jobs"][0]["status"] == "not_applied"
    assert report["jobs"][0]["reason_code"] == "stale_plan"
    _assert_stale_rollback(job_db, candidate, settings, before, expected, {key, new_key})


def test_validate_then_publish_is_stale_and_never_drives_old_definition(
    job_db: JobQueries, settings: Any, workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = FakeRecoveryStorage()
    candidate, key, expected = seed_job(
        job_db, settings, storage, "legacy-publish", legacy_definition=True
    )
    seed_job(job_db, settings, storage, "legacy-publish-control", healthy=True)
    prepared = prepare(job_db, storage, build_plan(job_db, storage))
    assert prepared.reviewed_plan["artifacts"][0]["definition"]["source"] == "active_revision"
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    active_before = job_db.get_active_workflow_revision("ws", "ws")
    original = job_db.try_artifact_recovery_final_locks_in_transaction

    def publish_before_final_lock(conn, locked_job_id, workspace_id):
        WorkflowRevisionService(job_db).publish_workspace_revision(
            "ws", replace(definition(), label="changed-after-validate")
        )
        return original(conn, locked_job_id, workspace_id)

    monkeypatch.setattr(
        job_db,
        "try_artifact_recovery_final_locks_in_transaction",
        publish_before_final_lock,
    )
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["jobs"][0]["status"] == "not_applied"
    assert report["jobs"][0]["reason_code"] == "stale_plan"
    active_after = job_db.get_active_workflow_revision("ws", "ws")
    assert active_after["version"] == active_before["version"] + 1
    assert active_after["definition_hash"] != active_before["definition_hash"]
    _assert_stale_rollback(job_db, candidate, settings, before, expected, {key})


def test_active_manifest_writer_fails_final_try_lock(
    job_db: JobQueries, settings: Any, workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, candidate, key, expected, prepared = _candidate(job_db, settings, "active-writer")
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    store = JobArtifactObjectStore(job_db, storage)
    new_key = key.replace(".v/attempt", ".v/active-writer")
    entered = threading.Event()
    release = threading.Event()
    original_upsert = __import__(
        "server.app.services.job_artifact_objects", fromlist=["upsert_artifact_row_tx"]
    ).upsert_artifact_row_tx

    def blocking_upsert(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(
        "server.app.services.job_artifact_objects.upsert_artifact_row_tx", blocking_upsert
    )
    failures: list[BaseException] = []

    def write() -> None:
        try:
            _register(store, job_id, new_key, "active_writer")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=write)
    thread.start()
    assert entered.wait(timeout=10)
    try:
        report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)
    finally:
        release.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert failures == []

    assert report["jobs"][0]["status"] == "not_applied"
    assert report["jobs"][0]["reason_code"] == "stale_plan"
    _assert_stale_rollback(job_db, candidate, settings, before, expected, {key, new_key})


def test_active_revision_publisher_fails_final_try_lock(
    job_db: JobQueries, settings: Any, workspace: None
) -> None:
    storage = FakeRecoveryStorage()
    candidate, key, expected = seed_job(
        job_db, settings, storage, "active-publisher", legacy_definition=True
    )
    seed_job(job_db, settings, storage, "active-publisher-control", healthy=True)
    prepared = prepare(job_db, storage, build_plan(job_db, storage))
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    def hold_publish(_conn) -> None:
        entered.set()
        assert release.wait(timeout=10)

    def publish() -> None:
        try:
            WorkflowRevisionService(job_db).publish_workspace_revision(
                "ws", replace(definition(), label="active-publisher"), on_commit=hold_publish
            )
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=publish)
    thread.start()
    assert entered.wait(timeout=10)
    try:
        report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)
    finally:
        release.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert failures == []

    assert report["jobs"][0]["status"] == "not_applied"
    assert report["jobs"][0]["reason_code"] == "stale_plan"
    assert job_db.get_active_workflow_revision("ws", "ws")["version"] == 2
    _assert_stale_rollback(job_db, candidate, settings, before, expected, {key})


def test_post_commit_legacy_registration_and_upload_keep_default_semantics(
    job_db: JobQueries, settings: Any, workspace: None
) -> None:
    storage, candidate, _, expected, prepared = _candidate(job_db, settings, "post-commit")
    job_id = str(candidate["id"])
    store = JobArtifactObjectStore(job_db, storage)
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)
    assert report["jobs"][0]["status"] == "applied"
    assert db_state(job_db, job_id)["rows"] == []

    versioned_key = f"jobs/ws/{job_id}/.v/post-commit/result.json"
    _register(store, job_id, versioned_key, "post_commit_writer")
    registered = store.lookup(job_id, "result.json")
    assert registered["node_key"] == "post_commit_writer"
    assert registered["storage_key"] == versioned_key
    assert registered["content_hash"] == hashlib.sha256(b"new!").hexdigest()

    job_dir = resolve_job_dir(candidate, settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "result.json").write_bytes(expected)
    uploaded = store.upload(
        workspace_id="ws",
        job_id=job_id,
        node_key="producer",
        name="result.json",
        local_path=job_dir / "result.json",
    )
    assert uploaded["node_key"] == "producer"
    assert uploaded["content_hash"] == hashlib.sha256(expected).hexdigest()
    assert storage.objects[uploaded["storage_key"]] == expected
