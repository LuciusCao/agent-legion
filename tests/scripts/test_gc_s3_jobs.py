"""Contract tests for scripts/gc-s3-jobs.py.

CLI 编排层的契约：dotenv 加载、未配置 S3 的干净跳过、dry-run/--apply 两档
输出。扫描/删除判定逻辑已在 tests/services/test_s3_jobs_gc.py 直测，这里用
PYTHONPATH 探针桩替掉 boto3 与 storage settings，DB 侧桩掉
server.app.settings.load_settings（不触真 Postgres）。
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

# boto3 桩：ListObjectsV2 分页器默认对任何 Prefix 都返回空集（对象列举交给
# s3_jobs_gc 桩的固定返回）；STUB_OBJECTS_JSON（[[key, epoch秒, size], ...]）
# 提供时按该清单产出（供材料根前缀扫描断言），大清单走 STUB_OBJECTS_JSON_PATH
# 指向的临时文件（同 JSON 内容）——env/argv 合计限额在 Linux CI 上会被多页
# 用例打爆（#1298 CI 红，macOS 限额宽本地绿），delete_objects 记入 STUB_LOG
# 并返回全成功。
_BOTO3_PROBE = """
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def _stub_objects():
    raw = os.environ.get("STUB_OBJECTS_JSON")
    path = os.environ.get("STUB_OBJECTS_JSON_PATH")
    if path:
        raw = Path(path).read_text()
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
        yield {"Contents": _stub_objects()}


class _Client:
    def get_paginator(self, op):
        return _Paginator()

    def delete_objects(self, Bucket, Delete):
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"delete_objects {Bucket} n={len(Delete['Objects'])}\\n")
        return {"Errors": []}


def client(*args, **kwargs):
    return _Client()
"""


# s3_jobs_gc 桩：scan_orphans/apply_gc 的行为由 env 驱动并记入 STUB_LOG，
# CLI 编排契约（参数、输出、dry-run/--apply 分档）不依赖真判定逻辑——后者
# 已在 tests/services/test_s3_jobs_gc.py 直测。桩经 STUB_*_MODULE 机制注入。
# 材料根前缀扫描（脚本内 scan_material_root_orphans）消费本桩的常量、
# ObjectEntry 与 orphans_in_page（#1287 起页内判定下沉为 s3_jobs_gc 的共享
# 实现，桩按同语义镜像：DB 无行且超宽限窗；分页/前缀排除/孤儿归并仍是脚本
# 真实代码）；make_db_key_existence 默认「全部 key 已有行」，env
# STUB_KNOWN_KEYS_JSON 提供时只认清单内的 key（供孤儿判定断言）。
_S3_JOBS_GC_PROBE = """
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

DEFAULT_GRACE_HOURS = 24.0
DEFAULT_STAGING_GRACE_HOURS = 24.0
JOBS_PREFIX = "jobs/"
STAGING_PREFIX = "jobs-staging/"
KEY_PAGE_SIZE = 1000


@dataclass(frozen=True)
class ObjectEntry:
    key: str
    last_modified: datetime
    size_bytes: int


@dataclass
class OrphanReport:
    authority_orphans: list
    staging_orphans: list

    @property
    def count(self):
        return len(self.authority_orphans) + len(self.staging_orphans)


def scan_orphans(lister, key_exists, *, grace_hours=24.0, staging_grace_hours=24.0, now=None):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(
            f"scan_orphans grace={grace_hours} staging_grace={staging_grace_hours}\\n"
        )
    # 真判定逻辑不在此重放：固定返回 1+1，供 CLI 输出断言；
    # STUB_SCAN_EXTRA_JSON（key 列表）追加进 authority_orphans，供碰撞
    # 命名空间材料形态分流断言。
    t = datetime.now(UTC)
    extra = [
        ObjectEntry(key, t, 10)
        for key in json.loads(os.environ.get("STUB_SCAN_EXTRA_JSON", "[]"))
    ]
    return OrphanReport(
        authority_orphans=[ObjectEntry("jobs/x/a.json", t, 50), *extra],
        staging_orphans=[ObjectEntry("jobs-staging/x/b.json", t, 30)],
    )


def orphans_in_page(entries, key_exists, cutoff):
    # 镜像 s3_jobs_gc.orphans_in_page 的判定语义（DB 无行且超宽限窗）；
    # 真实实现的判定正确性由 tests/services/test_s3_jobs_gc.py 钉住。
    known = key_exists([e.key for e in entries])
    return [e for e in entries if e.key not in known and e.last_modified < cutoff]


def make_db_key_existence(database_dsn):
    raw = os.environ.get("STUB_KNOWN_KEYS_JSON")
    known_keys = set(json.loads(raw)) if raw else None

    def exists(keys):
        # 记录每批大小：材料根前缀扫描的 KEY_PAGE_SIZE 分页（#1287）据此断言。
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"key_exists n={len(keys)}\\n")
        if known_keys is not None:
            return {key for key in keys if key in known_keys}
        return set(keys)

    return exists


class ApplyResult:
    def __init__(self, deleted, skipped_revalidated=0):
        self.deleted = deleted
        self.skipped_revalidated = skipped_revalidated


def apply_gc(client, bucket, report, revalidate=None):
    with open(os.environ["STUB_LOG"], "a") as fh:
        fh.write(f"apply_gc revalidate={revalidate is not None}\\n")
    return ApplyResult(len(report.authority_orphans) + len(report.staging_orphans))
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
# 真实 DB 从不被触碰——s3_jobs_gc 桩的 make_db_key_existence 不走真实查询。
_SETTINGS_PROBE = """
from types import SimpleNamespace


def load_settings(*args, **kwargs):
    return SimpleNamespace(
        database_url="postgresql://stub/stub",
        data_dir=__import__("pathlib").Path("."),
        jobs_dir=__import__("pathlib").Path("jobs"),
    )
"""

# JobQueries 桩：CLI 只把它透传给 make_db_key_existence（同样被桩掉），
# 构造函数不得触碰 DB（真实 JobQueries.__init__ 会跑 init_db 建表）。
_JOBS_QUERIES_PROBE = """
class JobQueries:
    def __init__(self, path, jobs_dir=None):
        self.path = path
        self.jobs_dir = jobs_dir

    def existing_object_storage_keys(self, storage_keys):
        # CLI 的删除前新鲜反查（TOCTOU 防护）：桩语义 = 全部「已有行」，
        # 使 revalidate 过滤后无删除——断言只验证调用链形状。
        import os
        with open(os.environ["STUB_LOG"], "a") as fh:
            fh.write(f"revalidate n={len(storage_keys)}\\n")
        return set(storage_keys)
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
    (pystub / "server/app/services/s3_jobs_gc.py").write_text(_S3_JOBS_GC_PROBE)
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


def test_unconfigured_store_is_a_clean_skip(tmp_path: Path) -> None:
    """未配置 AGENT_LEGION_S3_BUCKET：提示并退出 0，不触碰 boto3。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_UNCONFIGURED)

    result, log = _run(tmp_path, pystub)

    assert result.returncode == 0, result.stderr
    assert "未配置" in result.stdout
    assert "list_objects" not in log


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    """dry-run：打印桩报告的孤儿计数，不调用 apply/delete_objects；dotenv
    必须显式加载 ROOT/.env（回归到裸 load_dotenv() 会在 stdin/子进程场景
    崩溃——桩记录路径供断言）。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub)

    assert result.returncode == 0, result.stderr
    assert f"load_dotenv {ROOT}/.env" in log
    assert "dry-run" in result.stdout
    assert "jobs/ 孤儿 1 个" in result.stdout
    assert "jobs-staging/ 孤儿 1 个" in result.stdout
    assert "apply_gc" not in log
    assert "delete_objects" not in log


def test_apply_deletes_reported_orphans(tmp_path: Path):
    """--apply：调用 apply_gc 并打印删除数。列举前缀的正确性由
    tests/services/test_s3_jobs_gc.py 的 exact-prefix 单测钉住（CLI 桩
    的 scan_orphans 不消费真实 lister,boto3 桩记录不到列举）。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, "--apply")

    assert result.returncode == 0, result.stderr
    assert "已删除 2 个" in result.stdout
    assert "apply_gc revalidate=True" in log


def test_grace_args_reach_scan(tmp_path: Path):
    """--grace-hours/--staging-grace-hours 原样传给 scan_orphans。"""
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(tmp_path, pystub, "--grace-hours", "48", "--staging-grace-hours", "6")

    assert result.returncode == 0, result.stderr
    assert "scan_orphans grace=48.0 staging_grace=6.0" in log


def test_material_root_prefix_orphans_reported_but_never_deleted(tmp_path: Path):
    """#1269 格子③ + #1282 三轮 P1 收口：材料根前缀纳入扫描报告——根前缀
    列举排除 jobs/ 与 jobs-staging/ 后，DB（job_artifacts ∪ materials）无行
    且超宽限窗的材料 key 判定为孤儿并打印数量与「只报告不删除」说明；
    --apply 下材料孤儿也不进删除批（key 内容寻址可复用，check-then-delete
    闭不死窗口，删除待 #1279 所有权协议）。分页/前缀排除/孤儿归并是脚本内
    scan_material_root_orphans 的真实代码；页内判定自 #1287 起复用
    s3_jobs_gc.orphans_in_page（此处为同语义桩，真实判定由
    tests/services/test_s3_jobs_gc.py 钉住），桩提供对象清单
    （STUB_OBJECTS_JSON）与已知 key 集合（STUB_KNOWN_KEYS_JSON）。"""
    now = time.time()
    objects = [
        ["ws1/hash-old/g0/superseded.txt", now - 48 * 3600, 10],  # 孤儿：无行 + 超窗
        ["ws1/hash-live/g0/live.txt", now - 48 * 3600, 10],  # 有行：保留
        ["ws1/hash-new/g0/inflight.txt", now - 3600, 10],  # 宽限窗内：保留
        ["jobs/ws1/j1/a.json", now - 48 * 3600, 10],  # jobs/ 前缀：归 scan_orphans
        ["jobs-staging/ws1/j1/e1/b.json", now - 48 * 3600, 10],  # 同上
    ]
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(
        tmp_path,
        pystub,
        "--apply",
        env_extra={
            "STUB_OBJECTS_JSON": json.dumps(objects),
            "STUB_KNOWN_KEYS_JSON": json.dumps(["ws1/hash-live/g0/live.txt"]),
        },
    )

    assert result.returncode == 0, result.stderr
    # 根前缀确实被列举（Prefix="" 即 bucket 根），且只有 1 个材料孤儿。
    assert "list_objects agent-legion-dev prefix=\n" in log
    assert "材料根前缀孤儿 1 个" in result.stdout
    assert "只报告不删除" in result.stdout
    # --apply：材料孤儿不进删除批——删除的只有 jobs 报告桩的 1+1。
    assert "已删除 2 个" in result.stdout
    # 根前缀只列举一次（扫描）；删除路径不存在后不再有删除前重列。
    assert log.count("prefix=\n") == 1


def test_material_root_scan_pages_key_existence(tmp_path: Path):
    """#1287：材料根前缀扫描按 KEY_PAGE_SIZE（桩=1000）分页判定——多页输入
    的孤儿归并正确，且 key_exists 按页分批调用（全量对象清单不驻留内存，
    大 bucket 不比 scan_orphans 多一份完整清单）。2503 个根前缀对象 =
    1000 + 1000 + 503 三页；其中 1 个有行、1 个在宽限窗内，均非孤儿。
    大清单走临时文件（STUB_OBJECTS_JSON_PATH）注入：env+argv 合计限额在
    Linux CI 上会被内联 JSON 打爆（#1298）。"""
    now = time.time()
    stale = now - 48 * 3600
    objects = [[f"ws1/hash-old/g0/f{i:04}.txt", stale, 10] for i in range(2501)]
    objects.append(["ws1/hash-live/g0/known.txt", stale, 10])  # 有行：保留
    objects.append(["ws1/hash-new/g0/fresh.txt", now - 60, 10])  # 窗内：保留
    objects_file = tmp_path / "stub-objects.json"
    objects_file.write_text(json.dumps(objects))
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(
        tmp_path,
        pystub,
        env_extra={
            "STUB_OBJECTS_JSON_PATH": str(objects_file),
            "STUB_KNOWN_KEYS_JSON": json.dumps(["ws1/hash-live/g0/known.txt"]),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "材料根前缀孤儿 2501 个" in result.stdout
    # 分页证据：key_exists 恰被调用 3 次，批量 1000/1000/503（含每页的
    # 非孤儿——判定按页整批送检，孤儿在每页内归并后累计）。
    batches = [line for line in log.splitlines() if line.startswith("key_exists n=")]
    assert batches == ["key_exists n=1000", "key_exists n=1000", "key_exists n=503"]


def test_collision_namespace_material_keys_are_report_only(tmp_path: Path):
    """#1282 r4 P1：workspace id 字面为 jobs / jobs-staging 时材料 key
    （{ws}/{hash}/g{代次}/{filename}）落在 jobs 前缀扫描内——删除批组装前
    按材料形态文法（第 3 段 g{数字}）分流到只报告集合。真 job 产物第 3 段
    是 job id（恒含下划线、永不匹配 g{数字}），不误伤、留在删除批。"""
    extra = [
        f"jobs/{'a' * 64}/g0/doc.txt",  # ws=jobs 的材料 key → 分流
        f"jobs-staging/{'b' * 64}/g2/d.txt",  # 撞名前缀下的材料 key → 分流
        "jobs/ws-x/ws-x_q1/dead.json",  # 真 job 产物（job id 含下划线）→ 留在删除批
        f"jobs/{'c' * 64}/old-name.txt",  # legacy 无代次段材料 key：不可复用，可删
    ]
    pystub = _make_pystub(tmp_path, _STORAGE_PROBE_CONFIGURED)

    result, log = _run(
        tmp_path,
        pystub,
        "--apply",
        env_extra={"STUB_SCAN_EXTRA_JSON": json.dumps(extra)},
    )

    assert result.returncode == 0, result.stderr
    # 两个撞名材料 key 分流进材料报告行（本次根前缀扫描为 0）。
    assert "材料根前缀孤儿 2 个" in result.stdout
    assert "只报告不删除" in result.stdout
    # jobs/ 删除批：桩固定的 1 个 + 真产物 + legacy 无代次段 key = 3；
    # jobs-staging/ 1 个（撞名材料 key 已被分流）→ 共删 4。
    assert "jobs/ 孤儿 3 个" in result.stdout
    assert "jobs-staging/ 孤儿 1 个" in result.stdout
    assert "已删除 4 个" in result.stdout
