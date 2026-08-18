"""Unit tests for scripts/seed_from_prod.py pure helpers (no DB, no Docker)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed_from_prod import (  # noqa: E402
    SeedError,
    artifact_blob_relpath,
    build_copy_in_sql,
    build_copy_out_sql,
    build_sample_sql,
    compose_base_cmd,
    guard_target_dsn,
    job_storage_relpath,
    read_env_file_database_url,
    resolve_target_dsn,
)

pytestmark = pytest.mark.no_db


class TestGuardTargetDsn:
    def test_accepts_local_dev_database(self):
        guard_target_dsn("postgresql://127.0.0.1:5432/agent_legion_develop")
        guard_target_dsn("postgresql://localhost/agent_legion_test_x")
        guard_target_dsn("postgresql://lucius@127.0.0.1:5432/agent_legion_worktree")

    def test_rejects_prod_database_name(self):
        with pytest.raises(SeedError, match="生产库同名"):
            guard_target_dsn("postgresql://127.0.0.1:5432/agent_legion")

    def test_rejects_prod_name_on_localhost(self):
        with pytest.raises(SeedError, match="生产库同名"):
            guard_target_dsn("postgresql://localhost/agent_legion")

    def test_rejects_non_loopback_host(self):
        with pytest.raises(SeedError, match="不是本机回环地址"):
            guard_target_dsn("postgresql://192.168.1.10:5432/agent_legion_develop")

    def test_rejects_remote_host_even_with_safe_db_name(self):
        with pytest.raises(SeedError, match="不是本机回环地址"):
            guard_target_dsn("postgresql://db.internal:5432/dev_seed")

    def test_rejects_non_postgres_scheme(self):
        with pytest.raises(SeedError, match="postgresql://"):
            guard_target_dsn("mysql://127.0.0.1/agent_legion_develop")


class TestResolveTargetDsn:
    def test_env_var_wins_over_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1/from_file\n")
        dsn = resolve_target_dsn(
            {"AGENT_LEGION_DATABASE_URL": "postgresql://127.0.0.1/from_env"},
            env_file,
        )
        assert dsn == "postgresql://127.0.0.1/from_env"

    def test_falls_back_to_env_file(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            '# comment\nOTHER=1\nAGENT_LEGION_DATABASE_URL="postgresql://127.0.0.1/from_file"\n'
        )
        assert resolve_target_dsn({}, env_file) == "postgresql://127.0.0.1/from_file"

    def test_missing_everywhere_raises(self, tmp_path: Path):
        with pytest.raises(SeedError, match="找不到目标库 DSN"):
            resolve_target_dsn({}, tmp_path / ".env")

    def test_read_env_file_missing_returns_none(self, tmp_path: Path):
        assert read_env_file_database_url(tmp_path / "nope") is None


class TestBuildSampleSql:
    def test_row_number_partition_and_limit(self):
        sql = build_sample_sql(30)
        assert "row_number() OVER (PARTITION BY workflow_key" in sql
        assert "ORDER BY created_at DESC" in sql
        assert "rn <= 30" in sql
        assert "FROM public.jobs" in sql

    def test_limit_is_inlined_as_int(self):
        assert "rn <= 7" in build_sample_sql(7)

    def test_rejects_non_positive_limit(self):
        with pytest.raises(SeedError):
            build_sample_sql(0)


class TestCopySql:
    def test_copy_out_quotes_columns_and_header(self):
        sql = build_copy_out_sql("jobs", ["id", "workflow_key"], "id IN (SELECT 1)")
        assert sql.startswith('COPY (SELECT "id", "workflow_key" FROM public."jobs"')
        assert "WHERE id IN (SELECT 1)" in sql
        assert "TO STDOUT WITH (FORMAT csv, HEADER true)" in sql

    def test_copy_in_matches_column_list(self):
        sql = build_copy_in_sql("jobs", ["id", "workflow_key"])
        assert sql == (
            'COPY public."jobs" ("id", "workflow_key") FROM STDIN WITH (FORMAT csv, HEADER true)'
        )


class TestArtifactBlobRelpath:
    def test_prefix_dir_and_hash_filename(self):
        digest = "ab" + "0" * 62
        assert artifact_blob_relpath(digest) == Path("artifacts") / "ab" / digest

    def test_rejects_path_traversal(self):
        with pytest.raises(SeedError):
            artifact_blob_relpath("../etc/passwd")

    def test_rejects_slash(self):
        with pytest.raises(SeedError):
            artifact_blob_relpath("aa/bb")

    def test_rejects_empty(self):
        with pytest.raises(SeedError):
            artifact_blob_relpath("  ")


class TestJobStorageRelpath:
    def test_uses_storage_dir_from_db(self):
        rel = job_storage_relpath("jobs/demo_workspace/job-1", "wf", "job-1")
        assert rel == Path("jobs/demo_workspace/job-1")

    def test_falls_back_to_workflow_key_and_id(self):
        rel = job_storage_relpath("", "demo_video_workflow", "job-9")
        assert rel == Path("jobs/demo_video_workflow/job-9")

    def test_rejects_absolute_and_traversal(self):
        with pytest.raises(SeedError):
            job_storage_relpath("/etc/passwd", "wf", "j")
        with pytest.raises(SeedError):
            job_storage_relpath("jobs/../../secrets", "wf", "j")


class TestComposeBaseCmd:
    def test_includes_existing_compose_files(self, tmp_path: Path):
        (tmp_path / "compose.host.yaml").write_text("services: {}")
        (tmp_path / "compose.local.yaml").write_text("services: {}")
        cmd = compose_base_cmd(tmp_path)
        assert cmd[:2] == ["docker", "compose"]
        assert str(tmp_path / "compose.host.yaml") in cmd
        assert str(tmp_path / "compose.local.yaml") in cmd

    def test_missing_dir_raises(self, tmp_path: Path):
        with pytest.raises(SeedError, match="不存在"):
            compose_base_cmd(tmp_path / "nope")

    def test_no_compose_files_raises(self, tmp_path: Path):
        with pytest.raises(SeedError, match="compose"):
            compose_base_cmd(tmp_path)
