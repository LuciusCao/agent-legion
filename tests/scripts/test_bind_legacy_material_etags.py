"""scripts/bind_legacy_material_etags.py（#1316）CLI 薄壳：不触发迁移、
库未到当前 schema 版本时拒绝运行；dry-run 不写、--apply 绑定。
绑定判定本身见 tests/services/test_material_legacy_etag_binding.py。"""

from __future__ import annotations

import pytest

from scripts import bind_legacy_material_etags as cli
from tests.postgres_support import TEST_DATABASE_URL
from tests.services import materials_testlib

storage = materials_testlib.storage
service = materials_testlib.service


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """CLI 会 load_dotenv：测试进程不得被 worktree 的 .env 污染环境变量。"""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


def test_schema_at_head_on_a_migrated_database() -> None:
    assert cli.schema_at_head(TEST_DATABASE_URL)


@pytest.mark.fresh_schema
def test_refuses_when_the_database_is_not_migrated(monkeypatch, storage, capsys) -> None:
    from server.app.db.schema import SCHEMA_VERSION
    from server.app.db.transaction import write_transaction

    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= %s", (SCHEMA_VERSION,))
    monkeypatch.setattr("server.app.storage.build_s3_storage", lambda: storage)

    assert cli.main(["--apply", "--database-url", TEST_DATABASE_URL]) == 1
    assert "拒绝运行" in capsys.readouterr().out


def test_dry_run_then_apply(job_db, service, storage, monkeypatch, capsys) -> None:
    material_id = materials_testlib.legacy_hashless(job_db, service, storage, b"cli-bytes")
    monkeypatch.setattr("server.app.storage.build_s3_storage", lambda: storage)

    assert cli.main(["--database-url", TEST_DATABASE_URL]) == 0
    assert materials_testlib.object_etag_of(job_db, material_id) == ""
    assert cli.main(["--apply", "--database-url", TEST_DATABASE_URL]) == 0
    assert materials_testlib.object_etag_of(job_db, material_id) != ""
    assert "绑定 1 行" in capsys.readouterr().out
