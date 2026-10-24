"""Contract tests for scripts/gc-s3-jobs.py.

CLI 编排层的契约：dotenv 加载、未配置 S3 的干净跳过、dry-run/--apply 两档
输出、参数透传、材料删除批（#1314）的端到端编排。判定/删除逻辑是**真实**
的 s3_jobs_gc 与 s3_material_gc（源码拷进探针树）；单元级判定另见
tests/services/test_s3_jobs_gc.py 与 test_s3_material_gc.py。这里用
PYTHONPATH 探针桩替掉 boto3、storage settings、server.app.settings 与
JobQueries（不触真 Postgres）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gc-s3-jobs.py"

_DOTENV_PROBE = """
import os


def load_dotenv(dotenv_path=None, override=False):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(f"load_dotenv {dotenv_path}\\n")
    return True
"""

# boto3 桩：ListObjectsV2 分页器按 Prefix 过滤 STUB_OBJECTS_JSON（[[key, epoch秒,
# size], ...]）产出；delete_objects 记入 STUB_LOG（每个 key 一行 deleted_key）
# 并返回全成功。
_BOTO3_PROBE = """
import json
import os
from datetime import UTC, datetime


def _stub_objects():
    raw = os.environ.get("STUB_OBJECTS_JSON")
    if not raw:
        return []
    return [
        {"Key": key, "LastModified": datetime.fromtimestamp(epoch, UTC), "Size": size}
        for key, epoch, size in json.loads(raw)
    ]


class _Paginator:
    def paginate(self, Bucket, Prefix=None):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"list_objects {Bucket} prefix={Prefix}\\n")
        yield {"Contents": [o for o in _stub_objects() if o["Key"].startswith(Prefix or "")]}


class _Client:
    def get_paginator(self, op):
        return _Paginator()

    def delete_objects(self, Bucket, Delete):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"delete_objects {Bucket} n={len(Delete['Objects'])}\\n")
            for obj in Delete["Objects"]:
                fh.write(f"deleted_key {obj['Key']}\\n")
        return {"Errors": []}


def client(*args, **kwargs):
    return _Client()
"""

_STORAGE_PROBE_CONFIGURED = """
from types import SimpleNamespace


def load_s3_settings():
    return SimpleNamespace(
        bucket="agent-legion-dev",
        endpoint_url="http://127.0.0.1:9000",
        region="us-east-1",
        access_key="",
        secret_key="",
        public_endpoint_url="",
    )
"""

_STORAGE_PROBE_UNCONFIGURED = """
def load_s3_settings():
    return None
"""

# settings 桩：load_settings 返回带 database_url/jobs_dir 的 SimpleNamespace；
# 真实 DB 从不被触碰。
_SETTINGS_PROBE = """
from types import SimpleNamespace


def load_settings(*args, **kwargs):
    return SimpleNamespace(
        database_url="postgresql://stub/stub",
        data_dir=__import__("pathlib").Path("."),
        jobs_dir=__import__("pathlib").Path("jobs"),
    )
"""

# JobQueries 桩（构造函数不得触碰 DB）：扫描期全量 key 集合取
# STUB_KNOWN_KEYS_JSON；删除前新鲜反查取 STUB_REVALIDATE_KNOWN_JSON（缺省 =
# 全部「已有行」，使反查后无删除）。
_JOBS_QUERIES_PROBE = """
import json
import os


class JobQueries:
    def __init__(self, path, jobs_dir=None):
        self.path = path
        self.jobs_dir = jobs_dir

    def all_object_storage_keys(self):
        return set(json.loads(os.environ.get("STUB_KNOWN_KEYS_JSON", "[]")))

    def existing_object_storage_keys(self, storage_keys):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"revalidate n={len(storage_keys)}\\n")
        raw = os.environ.get("STUB_REVALIDATE_KNOWN_JSON")
        if raw is None:
            return set(storage_keys)
        known = set(json.loads(raw))
        return {key for key in storage_keys if key in known}
"""


def _make_pystub(tmp_path: Path, storage_probe: str) -> Path:
    pystub = tmp_path / "pystub"
    (pystub / "botocore").mkdir(parents=True)
    (pystub / "server/app/storage").mkdir(parents=True)
    (pystub / "server/app/services").mkdir(parents=True)
    (pystub / "server/app/jobs/queries").mkdir(parents=True)
    (pystub / "server/app/settings.py").write_text(_SETTINGS_PROBE)
    (pystub / "server/app/jobs/__init__.py").write_text("")
    (pystub / "server/app/jobs/queries/__init__.py").write_text(_JOBS_QUERIES_PROBE)
    (pystub / "dotenv.py").write_text(_DOTENV_PROBE)
    (pystub / "boto3.py").write_text(_BOTO3_PROBE)
    (pystub / "botocore/__init__.py").write_text("")
    (pystub / "botocore/exceptions.py").write_text("")
    (pystub / "server/__init__.py").write_text("")
    (pystub / "server/app/__init__.py").write_text("")
    (pystub / "server/app/storage/__init__.py").write_text(storage_probe)
    (pystub / "server/app/services/__init__.py").write_text("")
    for name in ("s3_jobs_gc.py", "s3_material_gc.py"):
        source = ROOT / "server/app/services" / name
        (pystub / "server/app/services" / name).write_text(source.read_text())
    return pystub


def _run(
    tmp_path: Path,
    pystub: Path,
    *args: str,
    env_extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    stub_log = tmp_path / "stub.log"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(pystub),
        "STUB_LOG": str(stub_log),
    }
    env.update(env_extra or {})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )
    log = stub_log.read_text() if stub_log.exists() else ""
    return result, log


def _deleted(log: str) -> list[str]:
    return sorted(
        line.split(" ", 1)[1] for line in log.splitlines() if line.startswith("deleted_key ")
    )


_HOUR = 3600
_JOBS_OBJECTS = [["jobs/x/a.json", 0, 50], ["jobs-staging/x/e/b.json", 0, 30]]


def _aged(objects: list[list], hours: float) -> str:
    stamp = time.time() - hours * _HOUR
    return json.dumps([[key, stamp, size] for key, _, size in objects])


def test_unconfigured_store_is_a_clean_skip(tmp_path: Path) -> None:
    """未配置 AGENT_LEGION_S3_BUCKET：提示并退出 0，不触碰 boto3。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_UNCONFIGURED)

    result, log = _run(tmp_path, pystub)

    assert result.returncode == 0, result.stderr
    assert "未配置" in result.stdout
    assert "list_objects" not in log


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    """dry-run：打印孤儿计数，不调用删除；dotenv 必须显式加载 ROOT/.env
    （回归到裸 load_dotenv() 会在 stdin/子进程场景崩溃——桩记录路径供断言）。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, env_extra={"STUB_OBJECTS_JSON": _aged(_JOBS_OBJECTS, 48)})

    assert result.returncode == 0, result.stderr
    assert f"load_dotenv {ROOT}/.env" in log
    assert "dry-run" in result.stdout
    assert "jobs/ 孤儿 1 个" in result.stdout
    assert "jobs-staging/ 孤儿 1 个" in result.stdout
    assert "delete_objects" not in log
    assert "revalidate" not in log


def test_apply_deletes_reported_orphans(tmp_path: Path):
    """--apply：删除前逐批新鲜反查（TOCTOU，#344），打印删除数。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(
        tmp_path,
        pystub,
        "--apply",
        env_extra={
            "STUB_OBJECTS_JSON": _aged(_JOBS_OBJECTS, 48),
            "STUB_REVALIDATE_KNOWN_JSON": "[]",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "已删除 2 个孤儿对象（其中材料 0 个）" in result.stdout
    assert "revalidate n=" in log
    assert _deleted(log) == ["jobs-staging/x/e/b.json", "jobs/x/a.json"]


def test_grace_args_reach_scan(tmp_path: Path):
    """--grace-hours/--staging-grace-hours 原样作用于各自前缀：30h 前的对象
    在 48h 宽限窗内（jobs/ 不报），超过 6h 的 staging 宽限窗（报）。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, _ = _run(
        tmp_path,
        pystub,
        "--grace-hours",
        "48",
        "--staging-grace-hours",
        "6",
        env_extra={"STUB_OBJECTS_JSON": _aged(_JOBS_OBJECTS, 30)},
    )

    assert result.returncode == 0, result.stderr
    assert "jobs/ 孤儿 0 个" in result.stdout
    assert "jobs-staging/ 孤儿 1 个" in result.stdout


_MID = "m_" + "a" * 32


def _material_gate_objects(now: float) -> list[list]:
    """材料删除批门禁（#1314）的端到端对象表：每行 [key, mtime, size]。"""
    old, young = now - 48 * _HOUR, now - 0.5 * _HOUR
    return [
        [f"ws1/hash-old/v2g0/{_MID}/superseded.txt", old, 10],  # 单次使用、无行、超龄 → 删
        [f"ws1/hash-race/v2g0/{_MID}/regained.txt", old, 10],  # 反查时行出现 → 救下
        [f"ws1/hash-new/v2g0/{_MID}/inflight.txt", young, 10],  # 未满 1h 下限 → 暂缓
        ["ws1/hash-old/g0/legacy.txt", old, 10],  # 旧文法 → 只报告
        [f"ws1/hash-old/g0/{'a' * 32}/ambiguous.txt", old, 10],  # r1 反例形态 → 只报告
        [f"ws1/hash-old/g0/{_MID}/ambiguous2.txt", old, 10],  # r2 反例形态 → 只报告
        [f"ws1/hash-live/v2g0/{_MID}/live.txt", old, 10],  # 有行 → 非孤儿
        ["jobs/ws1/j1/a.json", old, 10],  # jobs/ 孤儿 → 删
        ["jobs-staging/ws1/j1/e1/b.json", old, 10],  # staging 孤儿 → 删
        [f"jobs/{'e' * 64}/v2g0/{_MID}/new.txt", old, 10],  # 撞名、单次使用 → 材料道删
        [f"jobs-staging/{'d' * 64}/g2/dir/.rollback/file.bin", old, 10],  # 撞名旧文法 → 只报告
    ]


@pytest.mark.parametrize("apply", [False, True])
def test_material_orphans_delete_batch_gates_end_to_end(tmp_path: Path, apply: bool):
    """#1314：``--grace-hours 0`` 下，单次使用文法、无行、超材料年龄下限
    （1h）的材料孤儿（含撞名前缀里的）进删除批；未满下限的暂缓
    （--grace-hours 0 压不穿下限）；旧文法（含 r1/r2 歧义形态）只报告；
    删除前新鲜反查救下扫描后重新获得行的 key。dry-run 不删。"""
    regained = f"ws1/hash-race/v2g0/{_MID}/regained.txt"
    live = f"ws1/hash-live/v2g0/{_MID}/live.txt"
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(
        tmp_path,
        pystub,
        "--grace-hours",
        "0",
        "--staging-grace-hours",
        "0",
        *(["--apply"] if apply else []),
        env_extra={
            "STUB_OBJECTS_JSON": json.dumps(_material_gate_objects(time.time())),
            "STUB_KNOWN_KEYS_JSON": json.dumps([live]),
            "STUB_REVALIDATE_KNOWN_JSON": json.dumps([live, regained]),
        },
    )

    assert result.returncode == 0, result.stderr
    # 根前缀被列举（Prefix="" 即 bucket 根）。
    assert "list_objects agent-legion-dev prefix=\n" in log
    # 进删除批 3 个（superseded、regained、撞名 new.txt）；暂缓 1；旧文法 4。
    assert "材料孤儿 3 个（30 字节）进删除批" in result.stdout
    assert "未满材料年龄下限暂缓 1 个" in result.stdout
    assert "旧文法材料孤儿 4 个（40 字节）只报告不删除" in result.stdout
    if not apply:
        assert _deleted(log) == []
        return
    assert _deleted(log) == sorted(
        [
            f"ws1/hash-old/v2g0/{_MID}/superseded.txt",
            f"jobs/{'e' * 64}/v2g0/{_MID}/new.txt",
            "jobs/ws1/j1/a.json",
            "jobs-staging/ws1/j1/e1/b.json",
        ]
    )
    assert "已删除 4 个孤儿对象（其中材料 2 个）" in result.stdout
    assert "反查救下 1 个对象" in result.stdout


@pytest.mark.parametrize("apply", [False, True])
def test_longer_staging_window_does_not_delay_collided_material_orphans(
    tmp_path: Path, apply: bool
):
    """#1322（#1318 codex r1 P2）的 CLI 端到端钉：--staging-grace-hours 168
    大于 --grace-hours 24 时，48h 的撞名 jobs-staging/ 前缀 v2g 材料孤儿
    进材料删除批（不再被 staging 截止点吞掉延迟回收）；同龄的真 staging
    对象仍在窗内不报；未满材料窗（12h）的撞名材料 key 不进候选。"""
    now = time.time()
    old = now - 48 * _HOUR
    v2g_key = f"jobs-staging/{'b' * 64}/v2g0/{_MID}/doc.txt"
    objects = [
        [v2g_key, old, 10],  # 撞名、单次使用、48h → 材料道删
        ["jobs-staging/ws1/j1/e1/staging.bin", old, 10],  # 真 staging，窗内 → 不报
        [f"jobs-staging/{'c' * 64}/v2g1/{_MID}/new.txt", now - 12 * _HOUR, 10],  # 窗内 → 不进候选
    ]
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(
        tmp_path,
        pystub,
        "--grace-hours",
        "24",
        "--staging-grace-hours",
        "168",
        *(["--apply"] if apply else []),
        env_extra={
            "STUB_OBJECTS_JSON": json.dumps(objects),
            "STUB_REVALIDATE_KNOWN_JSON": "[]",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "jobs-staging/ 孤儿 0 个" in result.stdout
    assert "材料孤儿 1 个（10 字节）进删除批" in result.stdout
    if not apply:
        assert _deleted(log) == []
        return
    assert _deleted(log) == [v2g_key]
    assert "已删除 1 个孤儿对象（其中材料 1 个）" in result.stdout
