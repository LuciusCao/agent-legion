"""Contract tests for the EXEC-GENERATION-002 execution write-surface guard."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.architecture.execution_write_surfaces import (
    REGISTRY_RELATIVE_PATH,
    check_execution_write_surfaces,
    find_artifact_byte_writes,
    find_manifest_row_writes,
    find_state_writes,
    load_write_surface_registry,
)

pytestmark = pytest.mark.no_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_registry(root: Path, sections: dict) -> None:
    write(root / REGISTRY_RELATIVE_PATH, json.dumps({"version": 1, **sections}))


# --- 扫描原语 -------------------------------------------------------------


def test_state_write_detects_multiline_and_mixed_case_sql() -> None:
    source = (
        'SQL = """\n'
        "update job_nodes\n"
        "set status='running'\n"
        "where job_id=%s\n"
        '"""\n'
        'OTHER = "INSERT INTO  executor_leases (id) VALUES (%s)"\n'
    )
    hits = find_state_writes(source)
    assert [label for _, label in hits] == [
        "update job_nodes",
        "insert into executor_leases",
    ]


def test_state_write_ignores_reads_and_other_tables() -> None:
    source = (
        'A = "select * from jobs"\n'
        'B = "update node_shards set status=%s"\n'
        'C = "insert into job_artifacts (id) values (%s)"\n'
        'D = "delete from jobs where id=%s"\n'
    )
    assert find_state_writes(source) == []


def test_state_write_matches_jobs_but_not_job_nodes_prefix() -> None:
    # `jobs` 与 `job_nodes` 是同前缀的不同表，词边界必须分清。
    source = 'A = "update jobs set status=%s"\nB = "update job_nodes set status=%s"\n'
    labels = [label for _, label in find_state_writes(source)]
    assert labels == ["update jobs", "update job_nodes"]


def test_byte_write_detects_attribute_and_name_calls() -> None:
    source = (
        "storage.put_stream(key, stream, size)\n"
        "self.storage.copy_object(src, dst)\n"
        "copy_object(src, dst)\n"
    )
    assert find_artifact_byte_writes(source) == [1, 2, 3]


def test_byte_write_ignores_definitions_and_prefixed_helpers() -> None:
    source = (
        "def put_stream(self, key, stream, size):\n"
        "    ...\n"
        "_put_stream(url, stream, size)\n"
        "storage.head_object(key)\n"
    )
    assert find_artifact_byte_writes(source) == []


def test_manifest_write_detects_imports_and_references() -> None:
    source = (
        "from server.app.services.job_artifact_rows import upsert_artifact_row_tx\n"
        "from server.app.executors._artifact_promotion import ARTIFACT_ROW_UPSERT_SQL\n"
        "upsert_artifact_row_tx(conn, ARTIFACT_ROW_UPSERT_SQL, job_id=j)\n"
    )
    assert find_manifest_row_writes(source) == [1, 2, 3]


# --- 注册表裁决 ------------------------------------------------------------


def test_unregistered_state_write_is_rejected(tmp_path: Path) -> None:
    write(
        tmp_path / "server/app/services/sneaky.py",
        'SQL = "update job_nodes set status=%s"\n',
    )
    write_registry(tmp_path, {"state_write_modules": {}})

    errors = check_execution_write_surfaces(tmp_path)

    assert any("sneaky.py" in error and "update job_nodes" in error for error in errors)
    assert any("execution-generation.md" in error for error in errors)


def test_registered_state_write_passes(tmp_path: Path) -> None:
    write(
        tmp_path / "server/app/jobs/atomic_mutations.py",
        'SQL = "update jobs set status=%s"\n',
    )
    write_registry(
        tmp_path,
        {"state_write_modules": {"server/app/jobs/atomic_mutations.py": {"via": []}}},
    )

    assert check_execution_write_surfaces(tmp_path) == []


def test_unregistered_byte_write_is_rejected(tmp_path: Path) -> None:
    write(tmp_path / "server/app/services/sneaky.py", "storage.put_stream(k, s, n)\n")
    write_registry(tmp_path, {})

    errors = check_execution_write_surfaces(tmp_path)

    assert any("sneaky.py" in error and "byte write" in error for error in errors)


def test_storage_layer_implementation_is_exempt(tmp_path: Path) -> None:
    write(
        tmp_path / "server/app/storage/s3_client.py",
        "self._client.copy_object(Bucket=b, Key=k)\n",
    )
    write_registry(tmp_path, {})

    assert check_execution_write_surfaces(tmp_path) == []


def test_unregistered_manifest_write_is_rejected(tmp_path: Path) -> None:
    write(
        tmp_path / "server/app/services/sneaky.py",
        "from server.app.services.job_artifact_rows import upsert_artifact_row_tx\n",
    )
    write_registry(tmp_path, {})

    errors = check_execution_write_surfaces(tmp_path)

    assert any("sneaky.py" in error and "manifest row" in error for error in errors)


def test_manifest_helper_definition_module_is_exempt(tmp_path: Path) -> None:
    write(
        tmp_path / "server/app/services/job_artifact_rows.py",
        "def upsert_artifact_row_tx(conn, sql, **kw):\n    ...\n",
    )
    write_registry(tmp_path, {})

    assert check_execution_write_surfaces(tmp_path) == []


def test_stale_registry_entry_is_rejected(tmp_path: Path) -> None:
    # 防漂移：注册表条目对应的文件已不存在（或不再有写面）必须报错收缩。
    write_registry(
        tmp_path,
        {"state_write_modules": {"server/app/jobs/gone.py": {"via": []}}},
    )

    errors = check_execution_write_surfaces(tmp_path)

    assert any("stale" in error and "gone.py" in error for error in errors)


def test_missing_registry_treats_every_write_site_as_unregistered(tmp_path: Path) -> None:
    # 注册表缺失 = 空注册表（删掉注册表文件不可能静默放行）。
    write(
        tmp_path / "server/app/services/sneaky.py",
        'SQL = "update job_nodes set status=%s"\n',
    )

    errors = check_execution_write_surfaces(tmp_path)

    assert any("sneaky.py" in error for error in errors)


def test_missing_registry_with_no_write_surfaces_passes(tmp_path: Path) -> None:
    # check_repository 的合成 tmp 仓库不造注册表也不写执行态——必须放行。
    write(tmp_path / "server/app/routes/ok.py", "x = 1\n")

    assert check_execution_write_surfaces(tmp_path) == []


def test_registry_entry_without_via_list_is_configuration_error(tmp_path: Path) -> None:
    write(
        tmp_path / REGISTRY_RELATIVE_PATH,
        json.dumps({"version": 1, "state_write_modules": {"x.py": {}}}),
    )

    errors = check_execution_write_surfaces(tmp_path)

    assert any("configuration" in error for error in errors)


# --- 真实仓库钉住 ----------------------------------------------------------


def test_current_repo_registry_matches_scan() -> None:
    # 漂移钉：注册表写面全集 == 实际扫描结果（双向）。
    assert check_execution_write_surfaces(PROJECT_ROOT) == []


def test_registry_via_helpers_exist() -> None:
    # 注册表 via 引用的 helper 名必须在 server/app 里真实存在
    # （def / class / 模块级赋值任一形态），防止注册表指向已退役的符号。
    registry = load_write_surface_registry(PROJECT_ROOT)
    sources = [
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "server/app").rglob("*.py"))
    ]
    missing: list[str] = []
    for section, entries in registry.items():
        for entry_path, entry in entries.items():
            for helper in entry["via"]:
                pattern = re.compile(
                    rf"^\s*(def|class)\s+{re.escape(helper)}\b"
                    rf"|^\s*{re.escape(helper)}\s*[:=]",
                    re.MULTILINE,
                )
                if not any(pattern.search(source) for source in sources):
                    missing.append(f"{section}/{entry_path}: {helper}")
    assert not missing, f"registry via helpers not found in server/app: {missing}"
