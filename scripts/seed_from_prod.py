#!/usr/bin/env python3
"""Seed the develop database from the local prod Docker stack.

Source (READ-ONLY): the Postgres container of the prod compose project
(``../prod/deploy/compose.host.yaml`` + ``compose.local.yaml``), reached only
via ``docker compose exec -T postgres ...``. No write SQL is ever sent there.

Target: ``AGENT_LEGION_DATABASE_URL`` from the environment, falling back to
the same key in the repo-root ``.env``. Guarded: both database and bucket must
be the exact derived resources of this worktree (tower children use their
parent), on loopback endpoints; shared/prod and other worktrees fail closed.

Layers:
  1. Definition data: full-table copy (pg_dump --data-only | pg_restore).
  2. Sample jobs: newest N jobs per workspace, copied with their child
     rows (COPY CSV), plus their on-disk job dirs and legacy CAS blobs.
     All materials are selected; job_artifacts follows the sampled jobs.
     Verified S3 objects are copied from the fixed prod bucket to this
     worktree's derived bucket before one Layer 2 transaction loads rows.
  3. Secrets are NOT copied; a reminder is printed at the end (develop uses
     its own vault_master_key, so prod ciphertext would be undecryptable).

Object rows with missing/mismatched sources, destination conflicts, or copy
failures are omitted and reported; successful rows commit and reruns resume
idempotently. --skip-objects explicitly restores the old no-S3 behavior.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import seed_from_prod_db as seed_db  # noqa: E402
from scripts import seed_from_prod_env as seed_env  # noqa: E402
from scripts import seed_from_prod_guard as guard  # noqa: E402
from scripts.seed_from_prod_files import copy_artifact_blobs, copy_job_files  # noqa: E402
from scripts.seed_from_prod_objects import (  # noqa: E402
    ObjectCopyReport,
    ObjectSeedError,
    S3CrossBucketStorage,
    copy_rows_with_objects,
    guard_object_copy,
    source_s3_settings,
    target_s3_settings,
)

SeedError = seed_db.SeedError
DEFAULT_COMPOSE_DIR = "../prod/deploy"
COMPOSE_FILES = ("compose.host.yaml", "compose.local.yaml")
# 目标开发库统一为 PostgreSQL 17（与 Docker stack 的 postgres:17.5 对齐），
# 客户端工具优先用 Homebrew postgresql@17（Apple Silicon / Intel 两个前缀）。
PG17_BIN_CANDIDATES: tuple[Path, ...] = (
    Path("/opt/homebrew/opt/postgresql@17/bin"),
    Path("/usr/local/opt/postgresql@17/bin"),
)

SOURCE_DB_USER = "agent_legion"
SOURCE_DB_NAME = "agent_legion"

# Layer 1: definition data, copied in full.
LAYER1_TABLES: tuple[str, ...] = (
    "users",
    "workspaces",
    "workspace_members",
    "workspace_agent_capacities",
    "workspace_node_capacities",
    "workspace_node_limits",
    "workspace_node_routes",
    "workspace_packages",
    "versioned_entities",
    "workflow_node_codes",
    "workflow_revisions",
    "global_settings",
)

# Layer 2: materials covers every copied workspace; the other rows follow the
# sampled jobs. Load order honors FK parents (artifact_refs and job_artifacts
# after jobs; node_run_token_usage after node_runs).
SAMPLE_SUBQUERY = (
    "SELECT id FROM ("
    "SELECT id, row_number() OVER (PARTITION BY workspace_id "
    "ORDER BY created_at DESC, id DESC) AS rn FROM public.jobs"
    ") t WHERE rn <= {limit}"
)

# (table, WHERE clause over the sample set). ``{sample}`` is replaced with
# the SAMPLE_SUBQUERY text.
LAYER2_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "runs",
        "id IN (SELECT DISTINCT run_id FROM public.jobs WHERE id IN ({sample}) AND run_id <> '')",
    ),
    ("artifacts", "hash IN (SELECT hash FROM public.artifact_refs WHERE job_id IN ({sample}))"),
    ("materials", "TRUE"),
    ("jobs", "id IN ({sample})"),
    ("job_nodes", "job_id IN ({sample})"),
    ("node_runs", "job_id IN ({sample})"),
    ("node_run_token_usage", "job_id IN ({sample})"),
    ("node_shards", "job_id IN ({sample})"),
    ("artifact_refs", "job_id IN ({sample})"),
    ("job_artifacts", "job_id IN ({sample})"),
)
OBJECT_TABLES = ("materials", "job_artifacts")

# Target-side tables wiped before layer 2 loads. CASCADE also clears runtime
# tables referencing jobs and object manifests; all selected rows are loaded
# together only after their destination objects have been verified.
LAYER2_TRUNCATE = ("jobs", "runs", "artifacts", "materials")


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------


def build_sample_sql(limit: int) -> str:
    if limit < 1:
        raise SeedError("--jobs-per-workflow 必须 >= 1")
    return SAMPLE_SUBQUERY.format(limit=int(limit))


def build_copy_out_sql(table: str, columns: Sequence[str], where: str) -> str:
    cols = ", ".join(f'"{c}"' for c in columns)
    return (
        f'COPY (SELECT {cols} FROM public."{table}" WHERE {where}) '
        "TO STDOUT WITH (FORMAT csv, HEADER true, NULL '\\N')"
    )


def compose_base_cmd(compose_dir: Path) -> list[str]:
    if not compose_dir.is_dir():
        raise SeedError(f"源 compose 目录不存在: {compose_dir}")
    cmd = ["docker", "compose"]
    missing = []
    for name in COMPOSE_FILES:
        path = compose_dir / name
        if path.is_file():
            cmd += ["-f", str(path)]
        else:
            missing.append(name)
    if len(missing) == len(COMPOSE_FILES):
        raise SeedError(f"{compose_dir} 下没有任何 compose 文件 {COMPOSE_FILES}")
    return cmd


def pg_client_bin(explicit: str | None = None) -> Path:
    """Prefer the PostgreSQL 17 client tools (target server is 17)."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env_dir = os.environ.get("SEED_PG_CLIENT_BIN")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(PG17_BIN_CANDIDATES)
    candidates.append(Path(""))  # PATH lookup
    for cand in candidates:
        if str(cand) == "" or (cand / "psql").is_file():
            return cand
    raise SeedError("找不到 psql 客户端（尝试过 postgres@17 与 PATH）")


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def run(cmd: Sequence[str], *, stdin=None, stdout=None) -> subprocess.CompletedProcess:
    proc = subprocess.run(list(cmd), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=False)
    if proc.returncode != 0:
        display = [
            "<redacted-database-url>" if arg.startswith(("postgresql://", "postgres://")) else arg
            for arg in cmd[:6]
        ]
        raise SeedError(
            f"命令失败 ({proc.returncode}): {' '.join(display)}...\n<redacted-psql-stderr>"
        )
    return proc


def source_psql_cmd(compose_dir: Path) -> list[str]:
    return compose_base_cmd(compose_dir) + [
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        SOURCE_DB_USER,
        "-d",
        SOURCE_DB_NAME,
        "-v",
        "ON_ERROR_STOP=1",
        "-X",
    ]


def source_query(compose_dir: Path, sql: str) -> str:
    proc = run(source_psql_cmd(compose_dir) + ["-At", "-c", sql], stdout=subprocess.PIPE)
    return str(proc.stdout.decode())


def target_psql(psql: str, dsn: str, sql: str, *, stdin=None) -> str:
    proc = run(
        [psql, dsn, "-v", "ON_ERROR_STOP=1", "-X", "-At", "-c", sql],
        stdin=stdin,
        stdout=subprocess.PIPE,
    )
    return str(proc.stdout.decode())


def ensure_target_schema(psql: str, dsn: str, dry_run: bool = False) -> None:
    exists = target_psql(psql, dsn, "SELECT to_regclass('public.workspaces')").strip()
    if exists and exists != "-":
        return
    if dry_run:
        raise SeedError("dry-run 不会初始化空目标库；先启动一次 dev backend 建 schema。")
    log("目标库尚无 schema，调用 server.app.db.schema.init_db 初始化（与 backend 启动同路径）")
    sys.path.insert(0, str(REPO_ROOT))
    from server.app.db.schema import init_db  # noqa: PLC0415

    init_db(dsn)


def target_columns(psql: str, dsn: str, table: str) -> list[str]:
    out = target_psql(
        psql,
        dsn,
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_schema='public' AND table_name='{table}' "
        "ORDER BY ordinal_position",
    )
    cols = [line for line in out.splitlines() if line.strip()]
    if not cols:
        raise SeedError(f"目标库缺少表 public.{table}（schema 未初始化？）")
    return cols


# ---------------------------------------------------------------------------
# Layer implementations
# ---------------------------------------------------------------------------


def seed_layer1(compose_dir: Path, dsn: str, pg_bin: Path, tmpdir: Path, dry_run: bool) -> None:
    psql = str(pg_bin / "psql") if str(pg_bin) else "psql"

    if dry_run:
        for table in LAYER1_TABLES:
            count = source_query(compose_dir, f'SELECT count(*) FROM public."{table}"').strip()
            log(f"[dry-run] 第 1 层 {table}: 源 {count} 行")
        return

    dump_path = tmpdir / "layer1.sql"
    dump_cmd = (
        compose_base_cmd(compose_dir)
        + [
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "-U",
            SOURCE_DB_USER,
            "-d",
            SOURCE_DB_NAME,
            "--data-only",
            "--format=plain",
            "--no-owner",
            "--no-privileges",
        ]
        + [f"--table=public.{t}" for t in LAYER1_TABLES]
    )
    log(f"第 1 层：pg_dump {len(LAYER1_TABLES)} 张定义表（plain 格式，源只读）")
    with dump_path.open("wb") as fh:
        run(dump_cmd, stdout=fh)

    tables = ", ".join(f'public."{t}"' for t in LAYER1_TABLES)
    log("第 1 层：目标侧 TRUNCATE 定义表（CASCADE）")
    target_psql(psql, dsn, f"TRUNCATE {tables} CASCADE")
    log("第 1 层：psql 灌入目标库（session_replication_role=replica 绕过 FK 顺序）")
    # Source may lag the target by a schema version: project each COPY block
    # onto the columns the target still has (#211 M3 dropped a workspaces column).
    columns_by_table = {table: target_columns(psql, dsn, table) for table in LAYER1_TABLES}
    seed_db.restore_plain_sql(psql, dsn, dump_path, columns_by_table)


def _csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as fh:
        return sum(1 for _row in csv.DictReader(fh))


def _empty_csv(path: Path, columns: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=list(columns)).writeheader()


def seed_layer2_db(
    compose_dir: Path,
    dsn: str,
    pg_bin: Path,
    tmpdir: Path,
    limit: int,
    dry_run: bool,
    *,
    sample_jobs: list[dict[str, str]],
    object_storage: S3CrossBucketStorage | None,
    overwrite_objects: bool,
    object_batch_size: int,
) -> tuple[dict[str, int], list[ObjectCopyReport]]:
    """Export, prepare objects, then atomically load all selected Layer 2 rows."""
    psql = str(pg_bin / "psql") if str(pg_bin) else "psql"
    sample = build_sample_sql(limit)
    loaded: dict[str, int] = {}
    reports: list[ObjectCopyReport] = []
    csv_paths: dict[str, Path] = {}
    columns_by_table: dict[str, list[str]] = {}

    for table, where_tpl in LAYER2_QUERIES:
        columns = target_columns(psql, dsn, table)
        columns_by_table[table] = columns
        csv_path = tmpdir / f"{table}.csv"
        with csv_path.open("wb") as fh:
            run(
                source_psql_cmd(compose_dir)
                + ["-c", build_copy_out_sql(table, columns, where_tpl.format(sample=sample))],
                stdout=fh,
            )
        csv_paths[table] = csv_path
        loaded[table] = _csv_row_count(csv_path)
        log(f"第 2 层 {table}: 源选择 {loaded[table]} 行")

    job_workspaces = {row["id"]: row["workspace_id"] for row in sample_jobs}
    for table in OBJECT_TABLES:
        if object_storage is None:
            report = ObjectCopyReport(table=table, selected=loaded[table])
            empty_path = tmpdir / f"{table}.empty.csv"
            _empty_csv(empty_path, columns_by_table[table])
            csv_paths[table] = empty_path
            loaded[table] = 0
        else:
            ready_path = tmpdir / f"{table}.ready.csv"
            report = copy_rows_with_objects(
                table,
                csv_paths[table],
                ready_path,
                columns_by_table[table],
                object_storage,
                job_workspaces=job_workspaces,
                dry_run=dry_run,
                overwrite=overwrite_objects,
                batch_size=object_batch_size,
            )
            csv_paths[table] = ready_path
            loaded[table] = report.rows_ready
        reports.append(report)
        log_object_report(report, dry_run)

    if dry_run:
        return loaded, reports

    log("第 2 层：对象已备妥，在单事务内 TRUNCATE 并灌入全部所选行")
    seed_db.load_csv_transaction(
        psql,
        dsn,
        csv_paths,
        columns_by_table,
        [table for table, _where in LAYER2_QUERIES],
        LAYER2_TRUNCATE,
    )
    for table, _where in LAYER2_QUERIES:
        count = int(target_psql(psql, dsn, f'SELECT count(*) FROM public."{table}"').strip())
        loaded[table] = count
        log(f"第 2 层 {table}: 灌入 {count} 行")
    return loaded, reports


def log_object_report(report: ObjectCopyReport, dry_run: bool) -> None:
    for failure in report.failures:
        log(
            f"对象行跳过: {failure.table} {failure.row_id} key={failure.storage_key!r} "
            f"stage={failure.operation} reason={failure.reason}"
        )
    log(
        f"对象清单 {report.table}: 选择 {report.selected} 行，可加载 {report.rows_ready} 行；"
        f"复制 {report.copied}，HEAD 跳过 {report.existing}，"
        f"dry-run 待复制 {report.planned if dry_run else 0}，失败 {len(report.failures)}"
    )


def fetch_sample_jobs(compose_dir: Path, limit: int) -> list[dict[str, str]]:
    sql = (
        "COPY (SELECT id, workspace_id, storage_dir FROM public.jobs "
        f"WHERE id IN ({build_sample_sql(limit)}) ORDER BY workspace_id, id) "
        "TO STDOUT WITH (FORMAT csv, HEADER true)"
    )
    proc = run(source_psql_cmd(compose_dir) + ["-c", sql], stdout=subprocess.PIPE)
    rows = list(csv.DictReader(proc.stdout.decode().splitlines()))
    return rows


def fetch_artifact_hashes(csv_path: Path) -> list[str]:
    with csv_path.open(newline="", encoding="utf-8") as fh:
        return sorted({row["hash"] for row in csv.DictReader(fh) if row.get("hash")})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从本机 prod Docker 生产库（只读）抽样灌数据到 develop 开发库"
    )
    parser.add_argument(
        "--jobs-per-workflow",
        type=int,
        default=30,
        help="每个 workspace 抽取的最新 job 数（默认 30）",
    )
    parser.add_argument(
        "--prod-compose-dir",
        default=None,
        help="prod compose 目录（默认 env SEED_PROD_COMPOSE_DIR 或 ../prod/deploy）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只读源库并 HEAD 对象，不写目标库/文件/bucket"
    )
    parser.add_argument("--skip-files", action="store_true", help="不拷 legacy data/ 文件")
    parser.add_argument(
        "--skip-objects",
        action="store_true",
        help="显式跳过 materials/job_artifacts 行及 S3 对象（旧行为）",
    )
    parser.add_argument(
        "--overwrite-objects",
        action="store_true",
        help="目标同 key 异尺寸对象也覆盖；默认报告冲突并跳过对应行",
    )
    parser.add_argument(
        "--object-batch-size",
        type=int,
        default=100,
        help="对象清单分批处理大小（默认 100）",
    )
    parser.add_argument(
        "--prod-s3-endpoint",
        default=None,
        help="prod S3 的宿主机 loopback endpoint（默认 SEED_PROD_S3_ENDPOINT 或 prod deploy/.env）",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()

    compose_dir = Path(
        args.prod_compose_dir
        or os.environ.get("SEED_PROD_COMPOSE_DIR")
        or str(REPO_ROOT / DEFAULT_COMPOSE_DIR)
    ).resolve()
    prod_data = (compose_dir.parent / "data").resolve()
    dev_data = (REPO_ROOT / "data").resolve()

    dsn = seed_env.resolve_target_dsn(dict(os.environ), REPO_ROOT / ".env")
    _expected_db, expected_bucket = guard.guard_target_repo(dsn, REPO_ROOT)
    if args.object_batch_size < 1:
        raise SeedError("--object-batch-size 必须 >= 1")
    log(
        f"目标库: {guard.parse_dsn(dsn).path.lstrip('/')} "
        f"@ {guard.parse_dsn(dsn).hostname}（护栏通过）"
    )
    log(f"源: {compose_dir}（docker compose exec，只读）")

    object_storage: S3CrossBucketStorage | None = None
    if not args.skip_objects:
        target_settings = target_s3_settings(dict(os.environ), REPO_ROOT / ".env")
        if target_settings is None:
            raise SeedError(
                "未配置 dev AGENT_LEGION_S3_BUCKET；请初始化 worktree，或显式 --skip-objects。"
            )
        source_settings = source_s3_settings(
            dict(os.environ),
            compose_dir / ".env",
            target_settings,
            endpoint_override=args.prod_s3_endpoint,
        )
        guard_object_copy(source_settings, target_settings, expected_target_bucket=expected_bucket)
        object_storage = S3CrossBucketStorage(source_settings, target_settings)
        try:
            object_storage.check_buckets()
        except Exception as exc:
            raise SeedError(f"S3 bucket 预检失败: {type(exc).__name__}") from None
        log(f"S3 server-side copy: {source_settings.bucket} -> {target_settings.bucket}")
    else:
        log("--skip-objects：materials/job_artifacts 行与 S3 对象均不复制")

    pg_bin = pg_client_bin()
    psql = str(pg_bin / "psql") if str(pg_bin) else "psql"

    if args.dry_run:
        log("dry-run：不写目标库、不拷文件、不写对象")

    ensure_target_schema(psql, dsn, args.dry_run)
    source_query(compose_dir, "SELECT 1")

    with tempfile.TemporaryDirectory(prefix="seed-from-prod-") as tmp:
        tmpdir = Path(tmp)
        seed_layer1(compose_dir, dsn, pg_bin, tmpdir, args.dry_run)
        log(f"第 2 层：每个 workspace 最新 {args.jobs_per_workflow} 个 job；materials 全量")
        sample_jobs = fetch_sample_jobs(compose_dir, args.jobs_per_workflow)
        log(f"样本 job 数: {len(sample_jobs)}")
        layer2_counts, object_reports = seed_layer2_db(
            compose_dir,
            dsn,
            pg_bin,
            tmpdir,
            args.jobs_per_workflow,
            args.dry_run,
            sample_jobs=sample_jobs,
            object_storage=object_storage,
            overwrite_objects=args.overwrite_objects,
            object_batch_size=args.object_batch_size,
        )

        if args.skip_files:
            log("--skip-files：跳过 data/ 文件拷贝")
        else:
            if not prod_data.is_dir():
                raise SeedError(f"源 data/ 目录不存在: {prod_data}")
            artifact_csv = tmpdir / "artifact_refs.csv"
            hashes = fetch_artifact_hashes(artifact_csv)
            job_stat = copy_job_files(prod_data, dev_data, sample_jobs, args.dry_run)
            artifact_stat = copy_artifact_blobs(prod_data, dev_data, hashes, args.dry_run)
            log(
                f"文件拷贝：job 目录 {job_stat[0]} 个 / {job_stat[1] / 1e6:.1f} MB；"
                f"artifact blob {artifact_stat[0]} 个 / {artifact_stat[1] / 1e6:.1f} MB"
                f"（源缺失 {artifact_stat[2]} 个）"
            )

    elapsed = time.monotonic() - started
    log("=== 汇总 ===")
    for table, count in layer2_counts.items():
        log(f"  {table}: {count} 行")
    log(f"耗时 {elapsed:.1f}s")
    log(
        "提醒（第 3 层）：未拷贝 workspace_secrets —— develop 使用独立 "
        "vault_master_key，CMS 等 secrets 请在 develop UI（默认 "
        "http://127.0.0.1:8001）设置页重新录入。"
    )
    if any(not report.ok for report in object_reports):
        log("部分对象/清单行未复制；以上逐项报告可用于修复后重跑，成功子集已保留。")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SeedError, ObjectSeedError) as exc:
        print(f"[seed] 错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
