"""Transaction ordering and legacy filesystem coverage for seed_from_prod."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest

from scripts.seed_from_prod import seed_layer2_db
from scripts.seed_from_prod_db import SeedError, load_csv_transaction
from scripts.seed_from_prod_files import copy_artifact_blobs, copy_job_files
from scripts.seed_from_prod_objects import ObjectCopyReport

pytestmark = pytest.mark.no_db


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes):
        self.data.extend(data)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, returncode: int = 0):
        self.stdin = FakeStdin()
        self.stderr = None
        self.returncode = returncode

    def wait(self):
        return self.returncode


def test_layer2_load_uses_one_transaction_for_all_csvs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    processes: list[FakeProcess] = []

    def fake_popen(*_args, **_kwargs):
        process = FakeProcess()
        processes.append(process)
        return process

    monkeypatch.setattr("scripts.seed_from_prod_db.subprocess.Popen", fake_popen)
    paths = {}
    for table in ("jobs", "job_artifacts"):
        paths[table] = tmp_path / f"{table}.csv"
        paths[table].write_text("id\nrow\n" if table == "jobs" else "job_id\nrow\n")
    columns = {"jobs": ["id"], "job_artifacts": ["job_id"]}

    load_csv_transaction(
        "psql",
        "postgresql://127.0.0.1/agent_legion_develop",
        paths,
        columns,
        ["jobs", "job_artifacts"],
        ["jobs", "materials"],
    )

    assert len(processes) == 1
    payload = bytes(processes[0].stdin.data)
    assert payload.startswith(b"BEGIN;\nTRUNCATE")
    assert payload.endswith(b"COMMIT;\n")
    assert payload.count(b"BEGIN;") == payload.count(b"COMMIT;") == 1
    assert b'COPY public."jobs"' in payload
    assert b'COPY public."job_artifacts"' in payload
    assert processes[0].stdin.closed


def test_layer2_load_failure_is_reported_as_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "scripts.seed_from_prod_db.subprocess.Popen", lambda *_a, **_k: FakeProcess(1)
    )
    path = tmp_path / "jobs.csv"
    path.write_text("id\n")
    with pytest.raises(SeedError, match="已回滚全部 Layer 2 行"):
        load_csv_transaction(
            "psql",
            "postgresql://127.0.0.1/dev",
            {"jobs": path},
            {"jobs": ["id"]},
            ["jobs"],
            ["jobs"],
        )


def test_object_rows_are_prepared_before_layer2_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    events: list[str] = []
    columns = {
        "materials": ["id", "workspace_id", "size_bytes", "storage_key"],
        "job_artifacts": ["job_id", "node_key", "name", "storage_key", "size_bytes"],
        "jobs": ["id", "workspace_id"],
    }

    def fake_columns(_psql: str, _dsn: str, table: str):
        return columns.get(table, ["id"])

    def fake_run(_cmd, *, stdout=None, **_kwargs):
        sql = _cmd[-1]
        table = re.search(r'FROM public\."([^"]+)"', sql).group(1)
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=fake_columns("", "", table))
        writer.writeheader()
        if table == "materials":
            writer.writerow(
                {"id": "m1", "workspace_id": "ws", "size_bytes": "3", "storage_key": "ws/m1"}
            )
        elif table == "job_artifacts":
            writer.writerow(
                {
                    "job_id": "j1",
                    "node_key": "n",
                    "name": "out",
                    "storage_key": "jobs/ws/j1/out",
                    "size_bytes": "3",
                }
            )
        elif table == "jobs":
            writer.writerow({"id": "j1", "workspace_id": "ws"})
        stdout.write(text.getvalue().encode())
        return None

    def fake_copy(table, _source, destination, output_columns, _storage, **_kwargs):
        events.append(f"copy:{table}")
        with destination.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=output_columns).writeheader()
        return ObjectCopyReport(table=table, selected=1)

    def fake_load(*_args, **_kwargs):
        events.append("load")

    monkeypatch.setattr("scripts.seed_from_prod.target_columns", fake_columns)
    monkeypatch.setattr("scripts.seed_from_prod.source_psql_cmd", lambda _dir: ["source"])
    monkeypatch.setattr("scripts.seed_from_prod.run", fake_run)
    monkeypatch.setattr("scripts.seed_from_prod.copy_rows_with_objects", fake_copy)
    monkeypatch.setattr("scripts.seed_from_prod.seed_db.load_csv_transaction", fake_load)
    monkeypatch.setattr("scripts.seed_from_prod.target_psql", lambda *_a, **_k: "0")

    seed_layer2_db(
        tmp_path,
        "postgresql://127.0.0.1/agent_legion_develop",
        Path(""),
        tmp_path,
        1,
        False,
        sample_jobs=[{"id": "j1", "workspace_id": "ws", "storage_dir": ""}],
        object_storage=object(),
        overwrite_objects=False,
        object_batch_size=2,
    )
    assert events == ["copy:materials", "copy:job_artifacts", "load"]


def test_dry_run_never_invokes_layer2_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("scripts.seed_from_prod.target_columns", lambda *_a: ["id"])
    monkeypatch.setattr("scripts.seed_from_prod.source_psql_cmd", lambda _dir: ["source"])

    def fake_run(_cmd, *, stdout=None, **_kwargs):
        stdout.write(b"id\n")

    monkeypatch.setattr("scripts.seed_from_prod.run", fake_run)
    monkeypatch.setattr(
        "scripts.seed_from_prod.seed_db.load_csv_transaction",
        lambda *_a, **_k: pytest.fail("dry-run invoked target load"),
    )
    counts, reports = seed_layer2_db(
        tmp_path,
        "postgresql://127.0.0.1/agent_legion_develop",
        Path(""),
        tmp_path,
        1,
        True,
        sample_jobs=[],
        object_storage=None,
        overwrite_objects=False,
        object_batch_size=2,
    )
    assert set(counts) >= {"materials", "job_artifacts", "jobs"}
    assert counts["materials"] == counts["job_artifacts"] == 0
    assert [report.selected for report in reports] == [0, 0]


def test_local_job_dir_and_cas_copies_are_repeatable(tmp_path: Path):
    prod = tmp_path / "prod"
    dev = tmp_path / "dev"
    job_dir = prod / "jobs/ws/job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "out.json").write_text("one")
    (job_dir / "jobs.sqlite").write_text("ignored")
    blob = prod / "artifacts/ab/abcd"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"blob")
    jobs = [{"id": "job-1", "workspace_id": "ws", "storage_dir": "jobs/ws/job-1"}]

    assert copy_job_files(prod, dev, jobs, False) == (1, 3)
    assert copy_job_files(prod, dev, jobs, False) == (1, 3)
    assert not (dev / "jobs/ws/job-1/jobs.sqlite").exists()
    assert copy_artifact_blobs(prod, dev, ["abcd", "missing"], False) == (1, 4, 1)
    assert copy_artifact_blobs(prod, dev, ["abcd"], False) == (1, 4, 0)
    assert (dev / "artifacts/ab/abcd").read_bytes() == b"blob"
