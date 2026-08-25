import json
from pathlib import Path

import pytest

from scripts.architecture.service_data_boundary import (
    check_service_data_boundary,
    collect_service_data_bypasses,
    count_service_data_bypasses,
)
from scripts.check_architecture import check_repository
from tests.architecture_budget_helpers import write_neutral_budget_governance

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_boundary_baseline(root: Path, files: dict[str, list[int]]) -> None:
    baseline_path = root / "config/architecture/service-data-boundary-baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps({"version": 1, "files": files}, indent=2) + "\n", encoding="utf-8"
    )


def test_counts_sql_literals_by_keyword_family():
    source = (
        'A = "select * from jobs"\n'
        'B = "INSERT INTO t VALUES (1)"\n'
        'C = "alter table t add column x text"\n'
        'D = "hello world"\n'
    )

    sql_literals, db_primitive_refs = count_service_data_bypasses(source)

    assert sql_literals == 3
    assert db_primitive_refs == 0


def test_counts_db_primitive_imports_and_calls():
    source = (
        "from server.app.db.transaction import read_connection\n"
        "from server.app.db.transaction import write_transaction\n"
        "import server.app.db.connection\n"
    )

    sql_literals, db_primitive_refs = count_service_data_bypasses(source)

    assert sql_literals == 0
    assert db_primitive_refs == 3


def test_counts_db_primitives_from_other_import_paths():
    # from server.app.db import transaction-style module access also counts.
    source = "from server.app.db.transaction import read_connection, write_transaction\n"

    _, db_primitive_refs = count_service_data_bypasses(source)

    assert db_primitive_refs == 2


def test_collect_only_scans_services_root(tmp_path):
    write(tmp_path / "server/app/services/bypass.py", 'SQL = "SELECT 1 FROM t"\n')
    write(tmp_path / "server/app/routes/allowed.py", 'SQL = "SELECT 1 FROM t"\n')
    write(tmp_path / "server/app/jobs/queries/allowed.py", 'SQL = "SELECT 1 FROM t"\n')

    counts = collect_service_data_bypasses(tmp_path)

    assert list(counts) == ["server/app/services/bypass.py"]


def test_rejects_bypass_in_service_file_without_baseline_entry(tmp_path):
    write(tmp_path / "server/app/services/legacy.py", 'SQL = "SELECT * FROM t"\n')
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert any("no baseline entry" in error and "legacy.py" in error for error in errors)


def test_rejects_count_above_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        'A = "SELECT 1"\nB = "INSERT INTO t VALUES (1)"\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [1, 0]})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline" in error and "legacy.py" in error for error in errors)


def test_rejects_db_primitive_growth_above_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        "from server.app.db.transaction import read_connection\n"
        "from server.app.db.transaction import write_transaction\n",
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [0, 1]})

    errors = check_repository(tmp_path)

    assert any("exceeds baseline" in error for error in errors)


def test_accepts_counts_within_baseline(tmp_path):
    write(
        tmp_path / "server/app/services/legacy.py",
        'A = "SELECT 1"\nB = "INSERT INTO t VALUES (1)"\n',
    )
    write_neutral_budget_governance(tmp_path)
    write_boundary_baseline(tmp_path, {"server/app/services/legacy.py": [2, 0]})

    errors = check_repository(tmp_path)

    assert not any("baseline entry" in error or "exceeds baseline" in error for error in errors)


def test_accepts_facade_only_service_file(tmp_path):
    write(tmp_path / "server/app/services/clean.py", "X = job_db.get_job(1)\n")
    write_neutral_budget_governance(tmp_path)

    errors = check_repository(tmp_path)

    assert not any("baseline" in error and "clean.py" in error for error in errors)


def test_missing_baseline_is_configuration_error(tmp_path):
    (tmp_path / "server/app/services").mkdir(parents=True)

    errors = check_service_data_boundary(tmp_path)

    assert any("service data boundary configuration" in error for error in errors)


def test_malformed_baseline_is_configuration_error(tmp_path):
    write(
        tmp_path / "config/architecture/service-data-boundary-baseline.json",
        "{not json",
    )

    errors = check_service_data_boundary(tmp_path)

    assert any("service data boundary configuration" in error for error in errors)


def test_baseline_rejects_zero_zero_entries(tmp_path):
    write(tmp_path / "server/app/services/legacy.py", 'A = "SELECT 1"\n')
    baseline = tmp_path / "config/architecture/service-data-boundary-baseline.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_text(
        json.dumps({"version": 1, "files": {"server/app/services/legacy.py": [0, 0]}}),
        encoding="utf-8",
    )

    errors = check_service_data_boundary(tmp_path)

    assert any("must record at least one bypass" in error for error in errors)


def test_current_repo_passes_its_own_baseline():
    # The committed baseline must satisfy the real tree (the ratchet starts
    # frozen at the current counts, so this only fails if the checker's
    # semantics drift from what generated it).
    root = Path(__file__).resolve().parents[1]

    errors = check_service_data_boundary(root)

    assert errors == []
