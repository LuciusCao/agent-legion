"""Unit tests for scripts/seed_from_prod.py pure helpers (no DB, no Docker)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed_dump_projection import project_copy_blocks  # noqa: E402
from scripts.seed_from_prod import (  # noqa: E402
    SeedError,
    build_copy_out_sql,
    build_sample_sql,
    compose_base_cmd,
    ensure_target_schema,
    run,
)
from scripts.seed_from_prod_db import build_copy_in_sql  # noqa: E402
from scripts.seed_from_prod_env import (  # noqa: E402
    read_env_file_database_url,
    resolve_target_dsn,
)
from scripts.seed_from_prod_files import (  # noqa: E402
    artifact_blob_relpath,
    job_storage_relpath,
)
from scripts.seed_from_prod_guard import guard_target_dsn, guard_target_repo  # noqa: E402

pytestmark = pytest.mark.no_db


class TestGuardTargetDsn:
    def test_accepts_local_dev_database(self):
        guard_target_dsn("postgresql://127.0.0.1:5432/agent_legion_develop")
        guard_target_dsn("postgresql://localhost/agent_legion_test_x")
        guard_target_dsn("postgresql://dev@127.0.0.1:5432/agent_legion_worktree")

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

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://127.0.0.1/agent_legion_develop?host=remote.invalid",
            "postgresql://127.0.0.1/agent_legion_develop?hostaddr=192.0.2.1",
            "postgresql://127.0.0.1/agent_legion_develop?host=localhost&hostaddr=192.0.2.1",
            "postgresql://127.0.0.1/agent_legion_develop?host=remote.invalid&hostaddr=127.0.0.1",
            "postgresql://127.0.0.1/agent_legion_develop?host=localhost,remote.invalid",
            "postgresql://127.0.0.1/agent_legion_develop?hostaddr=127.0.0.1,192.0.2.1",
        ],
    )
    def test_rejects_remote_conninfo_candidates(self, dsn: str):
        with pytest.raises(SeedError, match="不是本机回环地址"):
            guard_target_dsn(dsn)

    def test_conninfo_query_host_overrides_url_authority(self):
        guard_target_dsn("postgresql://remote.invalid/agent_legion_develop?host=localhost")
        guard_target_dsn(
            "postgresql://remote.invalid/agent_legion_develop"
            "?host=localhost,127.0.0.1&hostaddr=127.0.0.1,::1"
        )

    def test_hostaddr_environment_cannot_bypass_guard(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
        with pytest.raises(SeedError, match="不是本机回环地址"):
            guard_target_dsn("postgresql://127.0.0.1/agent_legion_develop")


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
        assert "row_number() OVER (PARTITION BY workspace_id" in sql
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
        sql = build_copy_out_sql("jobs", ["id", "workspace_id"], "id IN (SELECT 1)")
        assert sql.startswith('COPY (SELECT "id", "workspace_id" FROM public."jobs"')
        assert "WHERE id IN (SELECT 1)" in sql
        assert "TO STDOUT WITH (FORMAT csv, HEADER true, NULL '\\N')" in sql

    def test_copy_in_matches_column_list(self):
        sql = build_copy_in_sql("jobs", ["id", "workspace_id"])
        assert sql == (
            'COPY public."jobs" ("id", "workspace_id") FROM STDIN '
            "WITH (FORMAT csv, HEADER true, NULL '\\N')"
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

    def test_falls_back_to_workspace_and_id(self):
        rel = job_storage_relpath("", "demo_video_workflow", "job-9")
        assert rel == Path("jobs/demo_video_workflow/job-9")

    def test_rejects_absolute_and_traversal(self):
        with pytest.raises(SeedError):
            job_storage_relpath("/etc/passwd", "wf", "j")
        with pytest.raises(SeedError):
            job_storage_relpath("jobs/../../secrets", "wf", "j")


class TestSecretRedaction:
    def test_invalid_dsn_is_not_echoed(self):
        with pytest.raises(SeedError) as caught:
            guard_target_dsn("mysql://user:do-not-print@127.0.0.1/db")
        assert "do-not-print" not in str(caught.value)

    def test_failed_psql_command_redacts_dsn(self, monkeypatch: pytest.MonkeyPatch):
        import subprocess

        monkeypatch.setattr(
            "scripts.seed_from_prod.subprocess.run",
            lambda *_a, **_k: subprocess.CompletedProcess(
                [],
                1,
                stderr=b"psql: postgresql://user:stderr-do-not-print@remote.invalid/dev",
            ),
        )
        with pytest.raises(SeedError) as caught:
            run(["psql", "postgresql://user:do-not-print@127.0.0.1/dev"])
        assert "do-not-print" not in str(caught.value)
        assert "stderr-do-not-print" not in str(caught.value)
        assert "<redacted-database-url>" in str(caught.value)
        assert "<redacted-psql-stderr>" in str(caught.value)


class TestTargetRepoGuard:
    def test_tower_child_uses_parent_derived_resources(self, tmp_path: Path):
        root = tmp_path / "develop" / ".tower" / "worktrees" / "wt-207"
        assert guard_target_repo("postgresql://127.0.0.1/agent_legion_develop", root) == (
            "agent_legion_develop",
            "agent-legion-develop",
        )

    def test_rejects_other_derived_database(self, tmp_path: Path):
        root = tmp_path / ".worktrees" / "feature-a"
        with pytest.raises(SeedError, match="agent_legion_feature_a"):
            guard_target_repo("postgresql://127.0.0.1/agent_legion_other", root)


class TestDryRunSchema:
    def test_empty_target_is_not_initialized(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("scripts.seed_from_prod.target_psql", lambda *_args, **_kwargs: "\n")
        with pytest.raises(SeedError, match="dry-run 不会初始化"):
            ensure_target_schema("psql", "postgresql://127.0.0.1/dev", dry_run=True)


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


class TestProjectCopyBlocks:
    """#211 M3 (codex P2 on #1032): a pre-v91 source dump still carries
    workspaces.default_workflow_key; layer 1 must project it away instead of
    failing the whole restore."""

    _DUMP = [
        b"SET statement_timeout = 0;\n",
        b"COPY public.workspaces (id, name, default_workflow_key, description) FROM stdin;\n",
        b"ws1\tOne\tws1\tline\\twith tab\n",
        b"ws2\tTwo\tws2\t\\N\n",
        b"\\.\n",
        b"COPY public.users (id, username) FROM stdin;\n",
        b"u1\tadmin\n",
        b"\\.\n",
    ]

    def test_drops_columns_the_target_no_longer_has(self):
        out = list(
            project_copy_blocks(
                self._DUMP,
                {"workspaces": ["id", "name", "description"], "users": ["id", "username"]},
            )
        )
        assert out == [
            b"SET statement_timeout = 0;\n",
            b"COPY public.workspaces (id, name, description) FROM stdin;\n",
            b"ws1\tOne\tline\\twith tab\n",
            b"ws2\tTwo\t\\N\n",
            b"\\.\n",
            b"COPY public.users (id, username) FROM stdin;\n",
            b"u1\tadmin\n",
            b"\\.\n",
        ]

    def test_matching_schema_passes_through_unchanged(self):
        columns = {"workspaces": ["id", "name", "default_workflow_key", "description"]}
        assert list(project_copy_blocks(self._DUMP, columns)) == self._DUMP
        # Tables without a known target column list are left verbatim too.
        assert list(project_copy_blocks(self._DUMP, {})) == self._DUMP
