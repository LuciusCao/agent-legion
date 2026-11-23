"""Transaction ordering and legacy filesystem coverage for seed_from_prod."""

from __future__ import annotations

import csv
import io
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from scripts.seed_from_prod import _csv_row_count, seed_layer2_db
from scripts.seed_from_prod_db import (
    Layer1Dump,
    SeedError,
    build_copy_in_sql,
    load_csv_transaction,
)
from scripts.seed_from_prod_files import copy_artifact_blobs, copy_job_files
from scripts.seed_from_prod_objects import ObjectCopyReport
from server.app.db.connection import connect_database


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes):
        self.data.extend(data)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, returncode: int = 0, *, stderr: io.BytesIO | None = None, validator=None):
        self.stdin = FakeStdin()
        self.stderr = stderr
        self.returncode = returncode
        self.validator = validator

    def wait(self):
        if self.validator is not None:
            try:
                self.validator(bytes(self.stdin.data))
            except (AssertionError, ValueError):
                return 1
        return self.returncode


def copy_frame_rows(payload: bytes, table: str, columns: list[str]) -> list[list[str]]:
    text = payload.decode()
    marker = build_copy_in_sql(table, columns) + "\n"
    start = text.index(marker) + len(marker)
    frame = text[start : text.index("\\.\n", start)]
    rows = list(csv.reader(io.StringIO(frame)))
    if not rows or rows[0] != columns or any(len(row) != len(columns) for row in rows):
        raise ValueError(f"invalid COPY frame for {table}: {rows!r}")
    return rows


@pytest.mark.no_db
def test_layer2_load_uses_one_transaction_for_all_csvs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    processes: list[FakeProcess] = []
    parsed: dict[str, list[list[str]]] = {}
    columns = {
        "jobs": ["id", "workspace_id"],
        "job_artifacts": ["job_id", "node_key", "name", "storage_key", "size_bytes"],
    }
    expected = {
        "jobs": ["j1", "ws"],
        "job_artifacts": ["j1", "node", "out.json", "jobs/ws/j1/out.json", "\\N"],
    }

    def validate(payload: bytes):
        for table, table_columns in columns.items():
            rows = copy_frame_rows(payload, table, table_columns)
            if len(rows) != 2:
                raise ValueError(f"unexpected record count for {table}: {rows!r}")
            parsed[table] = rows

    def fake_popen(*_args, **_kwargs):
        process = FakeProcess(validator=validate)
        processes.append(process)
        return process

    monkeypatch.setattr("scripts.seed_from_prod_db.subprocess.Popen", fake_popen)
    paths = {}
    for table, table_columns in columns.items():
        paths[table] = tmp_path / f"{table}.csv"
        with paths[table].open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(table_columns)
            writer.writerow(expected[table])

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
    assert parsed == {
        table: [table_columns, expected[table]] for table, table_columns in columns.items()
    }
    assert processes[0].stdin.closed


@pytest.mark.no_db
def test_layer2_copy_frame_handles_missing_final_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    columns = {"jobs": ["id", "workspace_id"]}
    path = tmp_path / "jobs.csv"
    path.write_bytes(b"id,workspace_id\nj1,ws")
    monkeypatch.setattr(
        "scripts.seed_from_prod_db.subprocess.Popen",
        lambda *_a, **_k: FakeProcess(
            validator=lambda payload: copy_frame_rows(payload, "jobs", columns["jobs"])
        ),
    )
    load_csv_transaction(
        "psql", "postgresql://127.0.0.1/dev", {"jobs": path}, columns, ["jobs"], ["jobs"]
    )


@pytest.mark.no_db
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


@pytest.mark.no_db
def test_layer2_psql_stderr_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    process = FakeProcess(
        1,
        stderr=io.BytesIO(b"connection failed: postgresql://user:do-not-print@remote.invalid/dev"),
    )
    monkeypatch.setattr("scripts.seed_from_prod_db.subprocess.Popen", lambda *_a, **_k: process)
    path = tmp_path / "jobs.csv"
    path.write_text("id\n")
    with pytest.raises(SeedError) as caught:
        load_csv_transaction(
            "psql",
            "postgresql://127.0.0.1/dev",
            {"jobs": path},
            {"jobs": ["id"]},
            ["jobs"],
            ["jobs"],
        )
    assert "do-not-print" not in str(caught.value)
    assert "remote.invalid" not in str(caught.value)
    assert "已脱敏" in str(caught.value)


@pytest.mark.no_db
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
        layer1=None,
        sample_jobs=[{"id": "j1", "workspace_id": "ws", "storage_dir": ""}],
        object_storage=object(),
        overwrite_objects=False,
        object_batch_size=2,
    )
    assert events == ["copy:materials", "copy:job_artifacts", "load"]


@pytest.mark.no_db
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
        layer1=None,
        sample_jobs=[],
        object_storage=None,
        overwrite_objects=False,
        object_batch_size=2,
    )
    assert set(counts) >= {"materials", "job_artifacts", "jobs"}
    assert counts["materials"] == counts["job_artifacts"] == 0
    assert [report.selected for report in reports] == [0, 0]


@pytest.mark.no_db
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


@pytest.mark.no_db
def test_csv_row_count_accepts_large_database_text(tmp_path: Path):
    path = tmp_path / "jobs.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "input_json"])
        writer.writerow(["j1", "x" * (256 * 1024)])
    assert _csv_row_count(path) == 1


@pytest.fixture
def real_seed_database(job_db):
    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("real psql is required")
    parsed = urlsplit(job_db.dsn_identity)
    dsn = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    _initialize_seed_tables(dsn)
    try:
        yield psql, dsn
    finally:
        with _seed_connection(dsn) as conn:
            conn.execute("DROP TABLE public.jobs, public.workspaces CASCADE")


@contextmanager
def _seed_connection(dsn: str):
    conn = connect_database(dsn)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _initialize_seed_tables(dsn: str) -> None:
    with _seed_connection(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS public.jobs, public.workspaces CASCADE")
        conn.execute("CREATE TABLE public.workspaces (id text PRIMARY KEY, label text NOT NULL)")
        conn.execute(
            "CREATE TABLE public.jobs ("
            "id text PRIMARY KEY, workspace_id text NOT NULL REFERENCES public.workspaces, "
            "payload text NOT NULL)"
        )
        conn.execute("INSERT INTO public.workspaces VALUES ('old-ws', 'old label')")
        conn.execute("INSERT INTO public.jobs VALUES ('old-job', 'old-ws', 'old payload')")


def _layer1_dump(tmp_path: Path) -> Layer1Dump:
    path = tmp_path / "layer1.sql"
    path.write_bytes(b"COPY public.workspaces (id, label) FROM stdin;\nnew-ws\tnew label\n\\.\n")
    return Layer1Dump(path, ["workspaces"], {"workspaces": ["id", "label"]})


def _layer1_and_jobs(dsn: str) -> tuple[list[tuple], list[tuple]]:
    with _seed_connection(dsn) as conn:
        workspaces = [
            (row["id"], row["label"])
            for row in conn.execute("SELECT id, label FROM public.workspaces ORDER BY id")
        ]
        jobs = [
            (row["id"], row["workspace_id"], row["payload"])
            for row in conn.execute("SELECT id, workspace_id, payload FROM public.jobs ORDER BY id")
        ]
    return workspaces, jobs


def test_real_psql_commit_and_cross_layer_rollback(real_seed_database, tmp_path: Path):
    psql, dsn = real_seed_database
    large_payload = "x" * (256 * 1024)
    jobs_csv = tmp_path / "jobs.csv"
    with jobs_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["id", "workspace_id", "payload"])
        writer.writerow(["new-job", "new-ws", large_payload])

    load_csv_transaction(
        psql,
        dsn,
        {"jobs": jobs_csv},
        {"jobs": ["id", "workspace_id", "payload"]},
        ["jobs"],
        ["jobs"],
        layer1=_layer1_dump(tmp_path),
    )

    workspaces, jobs = _layer1_and_jobs(dsn)
    assert workspaces == [("new-ws", "new label")]
    assert jobs == [("new-job", "new-ws", large_payload)]

    _initialize_seed_tables(dsn)
    jobs_csv.write_text("id,workspace_id,payload\nnew-job,new-ws\n", encoding="utf-8")
    with pytest.raises(SeedError, match="Layer 1/Layer 2.*已回滚"):
        load_csv_transaction(
            psql,
            dsn,
            {"jobs": jobs_csv},
            {"jobs": ["id", "workspace_id", "payload"]},
            ["jobs"],
            ["jobs"],
            layer1=_layer1_dump(tmp_path),
        )

    assert _layer1_and_jobs(dsn) == (
        [("old-ws", "old label")],
        [("old-job", "old-ws", "old payload")],
    )
