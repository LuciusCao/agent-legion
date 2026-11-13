"""Isolated-Postgres proof that the M4 CLI is genuinely read-only (#1019)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from scripts.clean_dangling_job_artifacts import _parser, main
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs import JobQueries
from server.app.jobs.queries.artifact_dangling import artifact_dangling_reader_from_dsn
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.revision_format import serialize_definition
from server.app.workflows.schema import WorkflowDefinition, WorkflowIntake, WorkflowNode
from tests.fakes.artifact_rows import seed_artifact_row_tx
from tests.fakes.storage import FakeObjectStorage
from tests.postgres_support import TEST_DATABASE_URL


class FakeStorage(FakeObjectStorage):
    def __init__(self, objects: dict[str, bytes]) -> None:
        super().__init__(objects=objects)
        self.head_calls: list[str] = []

    def configuration_fingerprint(self) -> dict[str, str]:
        return {
            "algorithm": "sha256",
            "bucket": "b" * 64,
            "endpoint": "e" * 64,
            "public_endpoint": "p" * 64,
            "region": "r" * 64,
            "configuration": "c" * 64,
        }

    def head_bucket(self) -> None:
        return None

    def iter_object_pages(self, prefix: str, *, page_size: int) -> Iterator[list[str]]:
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        for start in range(0, len(keys), page_size):
            yield keys[start : start + page_size]

    def head_object(self, storage_key: str):
        self.head_calls.append(storage_key)
        return super().head_object(storage_key)


def _definition(workspace_id: str) -> WorkflowDefinition:
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


def _seed_job(
    queries: JobQueries,
    source_id: str,
    *,
    with_artifact: bool = True,
) -> tuple[dict, str, bytes]:
    definition = _definition("ws")
    job = queries.create_job(
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
        workflow_definition_snapshot_json=serialize_definition(definition),
    )
    key = f"jobs/ws/{job['id']}/.v/attempt/result.json"
    content = f"bytes:{source_id}".encode()
    if with_artifact:
        with write_transaction(TEST_DATABASE_URL) as conn:
            seed_artifact_row_tx(
                conn,
                workspace_id="ws",
                job_id=str(job["id"]),
                node_key="producer",
                name="result.json",
                storage_key=key,
                size_bytes=len(content),
                content_hash=hashlib.sha256(content).hexdigest(),
            )
    return job, key, content


@pytest.fixture
def cli_db(tmp_path: Path) -> JobQueries:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    queries.create_workspace("Dry Run", workspace_id="ws")
    WorkflowRevisionService(queries).publish_workspace_revision("ws", _definition("ws"))
    return queries


def _fingerprint() -> dict[str, str]:
    with read_connection(TEST_DATABASE_URL) as conn:
        result = {}
        for table in ("jobs", "job_nodes", "job_artifacts", "workflow_revisions"):
            row = conn.execute(
                "select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) as h"
                f' from "{table}" t'
            ).fetchone()
            assert row is not None
            result[table] = str(row["h"])
        return result


def test_cli_dry_run_preserves_db_and_object_snapshots(
    cli_db: JobQueries, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from server.app.storage.s3_settings import S3Settings

    job, key, content = _seed_job(cli_db, "job-a")
    storage = FakeStorage({key: content})
    monkeypatch.setattr(
        "server.app.services.artifact_dangling_probe.S3ArtifactDanglingStorage",
        lambda _settings: storage,
    )
    monkeypatch.setattr(
        "server.app.storage.s3_settings.load_s3_settings",
        lambda: S3Settings(bucket="isolated-test-bucket"),
    )

    def _no_init_db(*_args, **_kwargs):
        raise AssertionError("dry-run must not initialize or migrate the schema")

    monkeypatch.setattr("server.app.jobs.queries.base.init_db", _no_init_db)
    before = _fingerprint()
    objects_before = dict(storage.objects)
    output = tmp_path / "plan.json"

    exit_code = main(["--database-url", TEST_DATABASE_URL, "--output", str(output)])

    assert exit_code == 0
    assert _fingerprint() == before
    assert storage.objects == objects_before
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["mode"] == "dry-run-only"
    assert plan["health"]["passed"] is True
    assert plan["scan"]["job_ids"] is None
    assert plan["artifacts"] == []
    assert plan["summary"]["outcomes"] == {"ok": 1}
    assert plan["schema_version"] == 2
    assert plan["recovery"] == {
        "apply_implemented": True,
        "rerun_executed": False,
        "manifest_rows_deleted": 0,
        "object_bytes_written": 0,
        "hydration_notices_cleared": False,
        "scheduling_resumed": False,
        "production_apply_allowed": False,
        "next_step": "explicitly calibrate thresholds before apply",
    }
    assert storage.head_calls == [key]
    assert "dry-run only" in capsys.readouterr().err


def test_reader_connections_enforce_server_read_only(cli_db: JobQueries) -> None:
    job, _, _ = _seed_job(cli_db, "job-a", with_artifact=False)
    reader = artifact_dangling_reader_from_dsn(TEST_DATABASE_URL)

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        reader.update_job_status(str(job["id"]), "failed", "must not land")

    assert cli_db.get_job(str(job["id"]))["status"] == "queued"


def test_keyset_pages_include_complete_supporting_rows(cli_db: JobQueries) -> None:
    seeded = [_seed_job(cli_db, f"job-{index}") for index in range(3)]
    reader = artifact_dangling_reader_from_dsn(TEST_DATABASE_URL)

    first = reader.artifact_dangling_job_page(limit=2)
    assert len(first) == 2
    second = reader.artifact_dangling_job_page(
        after=(first[-1]["created_at"], str(first[-1]["id"])), limit=2
    )

    assert len(second) == 1
    scanned = [*first, *second]
    assert {job["id"] for job in scanned} == {str(job["id"]) for job, _, _ in seeded}
    assert [(job["created_at"], job["id"]) for job in scanned] == sorted(
        (job["created_at"], job["id"]) for job in scanned
    )
    for page_job in scanned:
        assert len(page_job["artifacts"]) == 1
        assert {node["node_key"] for node in page_job["nodes"]} == {"producer", "consumer"}
        assert page_job["active_revision"]["workspace_id"] == "ws"
        assert page_job["pending_shard_nodes"] == []
    assert reader.artifact_dangling_job_page(limit=2, workspace_ids=[]) == []
    with pytest.raises(ValueError, match="page size"):
        reader.artifact_dangling_job_page(limit=0)


def test_cli_apply_requires_a_plan_path() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["--apply"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--page-size", "0"],
        ["--hash-max-stored-bytes", "0"],
        ["--hash-max-content-bytes", "0"],
        ["--hash-max-seconds", "-1"],
    ],
)
def test_cli_rejects_invalid_page_and_hash_budget_values(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    assert exc_info.value.code == 2
