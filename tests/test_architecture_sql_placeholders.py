import json
from pathlib import Path

import pytest

from scripts.architecture.sql_placeholders import (
    check_sql_placeholders,
    collect_sql_placeholder_counts,
    count_sql_qmark_placeholders,
)
from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_sql_baseline(root: Path, files: dict[str, int]) -> None:
    baseline_path = root / "config/architecture/sql-placeholders-baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps({"version": 1, "files": files}, indent=2) + "\n", encoding="utf-8"
    )


def test_counts_qmarks_only_in_sql_strings():
    source = (
        'SQL = "SELECT * FROM jobs WHERE id = ? AND state = ?"\n'
        'NOT_SQL = "what is this?"\n'
        'URL = "https://example.com/?a=1"\n'
        'DDL = "CREATE TABLE t (id TEXT)"\n'
    )

    assert count_sql_qmark_placeholders(source) == 2


def test_counts_each_sql_keyword_family():
    source = (
        'A = "INSERT INTO t VALUES (?)"\n'
        'B = "UPDATE t SET a = ?"\n'
        'C = "DELETE FROM t WHERE a = ?"\n'
        'D = "select a from t where b = ?"\n'
    )

    assert count_sql_qmark_placeholders(source) == 4


def test_collect_counts_skips_files_without_sql_qmarks(tmp_path):
    write(tmp_path / "server/app/clean.py", 'X = "%s"\n')
    write(tmp_path / "server/app/dirty.py", 'SQL = "SELECT 1 WHERE a = ?"\n')

    assert collect_sql_placeholder_counts(tmp_path) == {"server/app/dirty.py": 1}


def test_rejects_qmark_in_file_without_baseline_entry(tmp_path):
    write(tmp_path / "server/app/repo.py", 'SQL = "SELECT * FROM t WHERE id = ?"\n')
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any("without baseline entry" in error and "repo.py" in error for error in errors)


def test_rejects_qmark_count_above_baseline(tmp_path):
    write(
        tmp_path / "server/app/repo.py",
        'SQL = "SELECT * FROM t WHERE a = ? AND b = ?"\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_sql_baseline(tmp_path, {"server/app/repo.py": 1})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline 1" in error and "repo.py" in error for error in errors)


def test_accepts_qmark_count_within_baseline(tmp_path):
    write(
        tmp_path / "server/app/repo.py",
        'SQL = "SELECT * FROM t WHERE a = ? AND b = ?"\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_sql_baseline(tmp_path, {"server/app/repo.py": 2})

    errors = check_repository(tmp_path)

    assert not any("placeholder" in error for error in errors)


def test_accepts_percent_s_placeholders_in_new_file(tmp_path):
    write(
        tmp_path / "server/app/repo.py",
        'SQL = "SELECT * FROM t WHERE id = %s"\n',
    )
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert not any("placeholder" in error for error in errors)


def test_missing_baseline_is_configuration_error(tmp_path):
    (tmp_path / "server/app").mkdir(parents=True)

    errors = check_sql_placeholders(tmp_path)

    assert any("sql placeholder configuration" in error for error in errors)
