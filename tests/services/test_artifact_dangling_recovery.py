"""End-to-end constrained artifact recovery on isolated PostgreSQL and fake S3."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.clean_dangling_job_artifacts import _parser, main
from server.app.db.transaction import write_transaction
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.artifact_dangling_plan import (
    build_artifact_dangling_plan,
    render_artifact_dangling_plan,
)
from server.app.services.artifact_dangling_probe import s3_configuration_fingerprint
from server.app.services.artifact_dangling_recovery import (
    MAX_APPLY_JOBS,
    ArtifactRecoveryError,
    _actionable_jobs,
    execute_apply,
    load_reviewed_plan,
    prepare_apply,
)
from server.app.services.hydration_defer_board import (
    HYDRATION_DEFER_BOARD,
    HydrationDeferNotice,
)
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_rerun import JobRerunService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.storage.s3_settings import S3Settings
from server.app.storage_paths import resolve_job_dir
from server.app.workflow_worker.hydration_dangling import DanglingManifestStreaks
from server.app.workflows.revision_format import serialize_definition
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.fakes.artifact_rows import seed_artifact_row_tx
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL

S3_SETTINGS = S3Settings(
    bucket="isolated-recovery-bucket", endpoint_url="https://isolated-recovery.invalid"
)


class FakeRecoveryStorage(FakeObjectStorage):
    def head_bucket(self) -> None:
        return None

    def iter_object_pages(self, prefix: str, *, page_size: int) -> Iterator[list[str]]:
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        for start in range(0, len(keys), page_size):
            yield keys[start : start + page_size]


def definition(workspace_id: str = "ws") -> WorkflowDefinition:
    return WorkflowDefinition(
        key=workspace_id,
        label=workspace_id,
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
    legacy_key: bool = False,
    gzip_stored: bool = False,
    hash_mismatch: bool = False,
    legacy_definition: bool = False,
) -> tuple[dict[str, Any], str, bytes]:
    workflow = definition()
    snapshot = "" if legacy_definition else serialize_definition(workflow)
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
        workflow_definition_snapshot_json=snapshot,
    )
    job_id = str(job["id"])
    consumer_status = "completed" if healthy else "pending"
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key='producer'",
            (job_id,),
        )
        conn.execute(
            "update job_nodes set status=%s where job_id=%s and node_key='consumer'",
            (consumer_status, job_id),
        )
        conn.execute("update jobs set status='completed' where id=%s", (job_id,))
    expected = f"expected:{source_id}".encode()
    suffix = ".gz" if gzip_stored else ""
    key = (
        f"jobs/ws/{job_id}/result.json{suffix}"
        if legacy_key
        else f"jobs/ws/{job_id}/.v/attempt/result.json{suffix}"
    )
    stored_expected = gzip.compress(expected, mtime=0) if gzip_stored else expected
    if hash_mismatch:
        wrong = gzip.compress(b"wrong", mtime=0) if gzip_stored else b"wrong"
        storage.objects[key] = wrong
    elif healthy:
        storage.objects[key] = stored_expected
    with write_transaction(TEST_DATABASE_URL) as conn:
        seed_artifact_row_tx(
            conn,
            workspace_id="ws",
            job_id=job_id,
            node_key="producer",
            name="result.json",
            storage_key=key,
            size_bytes=len(stored_expected),
            content_hash=hashlib.sha256(expected).hexdigest(),
        )
    job_dir = resolve_job_dir(job, settings.jobs_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "result.json").write_bytes(expected)
    return job_db.get_job(job_id), key, expected


def build_plan(
    job_db: JobQueries, storage: FakeRecoveryStorage, *, calibrated: bool = True
) -> dict:
    return build_artifact_dangling_plan(
        job_db,
        storage,
        max_candidates=20,
        max_candidate_ratio=1.0,
        thresholds_calibrated=calibrated,
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


def rerun_service(
    job_db: JobQueries, settings: Any, storage: FakeRecoveryStorage
) -> JobRerunService:
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
    job_db.create_workspace("Recovery", workspace_id="ws")
    WorkflowRevisionService(job_db).publish_workspace_revision("ws", definition())


@pytest.mark.parametrize(
    ("legacy_key", "gzip_stored", "hash_mismatch", "legacy_definition"),
    [
        (True, False, False, False),
        (False, True, True, False),
        (True, False, False, True),
    ],
)
def test_apply_recovers_raw_gzip_legacy_and_versioned_rows(
    job_db: JobQueries,
    settings: Any,
    workspace: None,
    monkeypatch: pytest.MonkeyPatch,
    legacy_key: bool,
    gzip_stored: bool,
    hash_mismatch: bool,
    legacy_definition: bool,
) -> None:
    storage = FakeRecoveryStorage()
    candidate, key, _ = seed_job(
        job_db,
        settings,
        storage,
        f"candidate-{legacy_key}-{gzip_stored}-{legacy_definition}",
        legacy_key=legacy_key,
        gzip_stored=gzip_stored,
        hash_mismatch=hash_mismatch,
        legacy_definition=legacy_definition,
    )
    control, control_key, _ = seed_job(
        job_db, settings, storage, f"control-{legacy_key}-{gzip_stored}", healthy=True
    )
    job_id = str(candidate["id"])
    before = db_state(job_db, job_id)
    plan = build_plan(job_db, storage)
    assert plan["health"]["passed"] is True
    assert plan["summary"]["actionable_count"] == 1
    prepared = prepare(job_db, storage, plan)
    notices = (HydrationDeferNotice("result.json", "object_missing", ("producer",), ("consumer",)),)
    HYDRATION_DEFER_BOARD.publish(job_id, notices)
    wakeups: list[bool] = []
    monkeypatch.setattr(
        "server.app.services.job_rerun.commit.notify_schedulable_work", lambda: wakeups.append(True)
    )

    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert report["summary"] == {
        "applied_count": 1,
        "not_applied_count": 0,
        "failed_count": 0,
        "not_attempted_count": 0,
    }
    assert report["batch"]["atomic"] is False
    assert report["jobs"][0]["manifest_rows_retired"] == 1
    after = db_state(job_db, job_id)
    assert after["generation"] == before["generation"] + 1
    assert after["status"] == "queued"
    assert after["nodes"] == {"producer": "pending", "consumer": "stale"}
    assert after["rows"] == []
    assert key not in storage.objects
    assert control_key in storage.objects
    assert not (resolve_job_dir(candidate, settings.jobs_dir) / "result.json").exists()
    assert HYDRATION_DEFER_BOARD.for_job(job_id) == ()
    assert wakeups == [True]
    HYDRATION_DEFER_BOARD.publish(job_id, notices)
    streaks = DanglingManifestStreaks()
    streaks.observe(job_id, {}, definition(), after["nodes"])
    assert HYDRATION_DEFER_BOARD.for_job(job_id) == ()
    assert after["nodes"]["producer"] == "pending"
    assert db_state(job_db, str(control["id"]))["rows"]


def test_cli_apply_accepts_only_reviewed_identity_and_writes_honest_report(
    job_db: JobQueries,
    settings: Any,
    workspace: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeRecoveryStorage()
    candidate, _, _ = seed_job(job_db, settings, storage, "cli-candidate")
    seed_job(job_db, settings, storage, "cli-control", healthy=True)
    plan = build_plan(job_db, storage)
    plan_path = tmp_path / "reviewed.json"
    report_path = tmp_path / "apply.json"
    plan_path.write_text(render_artifact_dangling_plan(plan), encoding="utf-8")
    monkeypatch.setattr("server.app.settings.load_settings", lambda: settings)
    monkeypatch.setattr("server.app.storage.s3_settings.load_s3_settings", lambda: S3_SETTINGS)
    monkeypatch.setattr(
        "server.app.services.artifact_dangling_probe.S3ArtifactDanglingStorage",
        lambda _settings: storage,
    )

    exit_code = main(
        [
            "--apply",
            str(plan_path),
            "--plan-sha256",
            plan["plan_sha256"],
            "--expected-bucket",
            S3_SETTINGS.bucket,
            "--expected-endpoint",
            S3_SETTINGS.endpoint_url,
            "--database-url",
            TEST_DATABASE_URL,
            "--output",
            str(report_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["plan_sha256"] == plan["plan_sha256"]
    assert report["summary"]["applied_count"] == 1
    assert db_state(job_db, str(candidate["id"]))["generation"] == 1


def test_uncalibrated_hash_or_config_mismatch_never_applies(
    job_db: JobQueries, settings: Any, workspace: None, tmp_path: Path
) -> None:
    storage = FakeRecoveryStorage()
    candidate, _, _ = seed_job(job_db, settings, storage, "negative-candidate")
    seed_job(job_db, settings, storage, "negative-control", healthy=True)
    before = db_state(job_db, str(candidate["id"]))
    uncalibrated = build_plan(job_db, storage, calibrated=False)
    path = tmp_path / "plan.json"
    path.write_text(render_artifact_dangling_plan(uncalibrated), encoding="utf-8")
    with pytest.raises(ArtifactRecoveryError, match="calibrated") as error:
        load_reviewed_plan(path, uncalibrated["plan_sha256"])
    assert error.value.reason_code == "uncalibrated_plan"
    with pytest.raises(ArtifactRecoveryError) as error:
        load_reviewed_plan(path, "0" * 64)
    assert error.value.reason_code == "plan_sha256_mismatch"
    calibrated = build_plan(job_db, storage)
    with pytest.raises(ArtifactRecoveryError) as error:
        prepare_apply(
            job_db,
            storage,
            calibrated,
            S3_SETTINGS,
            expected_bucket="wrong-bucket",
            expected_endpoint=S3_SETTINGS.endpoint_url,
        )
    assert error.value.reason_code == "configuration_mismatch"
    assert db_state(job_db, str(candidate["id"])) == before
    with pytest.raises(SystemExit):
        _parser().parse_args(["--force"])


def test_fresh_plan_drift_is_zero_write(job_db: JobQueries, settings: Any, workspace: None) -> None:
    storage = FakeRecoveryStorage()
    candidate, key, _ = seed_job(job_db, settings, storage, "drift-candidate")
    seed_job(job_db, settings, storage, "drift-control", healthy=True)
    plan = build_plan(job_db, storage)
    before = db_state(job_db, str(candidate["id"]))
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update job_artifacts set size_bytes=size_bytes+1 where storage_key=%s", (key,)
        )

    with pytest.raises(ArtifactRecoveryError) as error:
        prepare(job_db, storage, plan)

    assert error.value.reason_code == "stale_plan"
    assert db_state(job_db, str(candidate["id"]))["generation"] == before["generation"]
    assert len(db_state(job_db, str(candidate["id"]))["rows"]) == 1


def test_batch_limit_is_checked_before_any_apply(
    job_db: JobQueries, settings: Any, workspace: None
) -> None:
    storage = FakeRecoveryStorage()
    seed_job(job_db, settings, storage, "limit-candidate")
    seed_job(job_db, settings, storage, "limit-control", healthy=True)
    plan = build_plan(job_db, storage)
    entry = plan["artifacts"][0]
    entries = []
    for index in range(MAX_APPLY_JOBS + 1):
        changed = copy.deepcopy(entry)
        changed["job_id"] = f"job-{index}"
        entries.append(changed)
    plan["artifacts"] = entries
    plan["summary"]["actionable_count"] = len(entries)
    with pytest.raises(ArtifactRecoveryError) as error:
        _actionable_jobs(plan)
    assert error.value.reason_code == "batch_too_large"


def test_first_failure_stops_later_jobs_and_preserves_prior_commit(
    job_db: JobQueries, settings: Any, workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = FakeRecoveryStorage()
    for index in range(3):
        seed_job(job_db, settings, storage, f"stop-{index}")
    seed_job(job_db, settings, storage, "stop-control", healthy=True)
    plan = build_plan(job_db, storage)
    prepared = prepare(job_db, storage, plan)
    before = {job.job_id: db_state(job_db, job.job_id) for job in prepared.jobs}
    import server.app.services.artifact_dangling_recovery as recovery

    original = recovery.revalidate_job

    def fail_second(prepared_arg, job, queries, storage_arg):
        if job.job_id == prepared.jobs[1].job_id:
            raise ArtifactRecoveryError("stale_plan", "injected second-job conflict")
        return original(prepared_arg, job, queries, storage_arg)

    monkeypatch.setattr(recovery, "revalidate_job", fail_second)
    report = execute_apply(prepared, rerun_service(job_db, settings, storage), storage)

    assert [item["status"] for item in report["jobs"]] == [
        "applied",
        "not_applied",
        "not_attempted",
    ]
    assert report["summary"] == {
        "applied_count": 1,
        "not_applied_count": 2,
        "failed_count": 1,
        "not_attempted_count": 1,
    }
    first, second, third = prepared.jobs
    assert db_state(job_db, first.job_id)["generation"] == before[first.job_id]["generation"] + 1
    assert db_state(job_db, second.job_id) == before[second.job_id]
    assert db_state(job_db, third.job_id) == before[third.job_id]
