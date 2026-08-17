#!/usr/bin/env python3
"""Seed the develop database from the local prod Docker stack.

Source (READ-ONLY): the Postgres container of the prod compose project
(``../prod/deploy/compose.host.yaml`` + ``compose.local.yaml``), reached only
via ``docker compose exec -T postgres ...``. No write SQL is ever sent there.

Target: ``AGENT_LEGION_DATABASE_URL`` from the environment, falling back to
the same key in the repo-root ``.env``. Guarded: the tool refuses to run when
the target database is named ``agent_legion`` (the prod name) or the target
host is not loopback.

Layers:
  1. Definition data: full-table copy (pg_dump --data-only | pg_restore).
  2. Sample jobs: newest N jobs per workflow_key, copied with their child
     rows (COPY CSV), plus their on-disk job dirs and artifact blobs.
  3. Secrets are NOT copied; a reminder is printed at the end (develop uses
     its own vault_master_key, so prod ciphertext would be undecryptable).
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROD_DB_NAME = "agent_legion"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
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
    "workspace_executor_allocations",
    "workspace_node_bindings",
    "workspace_node_capacities",
    "workspace_node_limits",
    "workspace_node_routes",
    "workspace_packages",
    "versioned_entities",
    "workflow_node_codes",
    "workflow_revisions",
    "global_settings",
)

# Layer 2: rows tied to the sampled jobs. Order matters on load (FK parents
# first): job_batches/artifacts before jobs is not required, but
# node_run_token_usage must follow node_runs and artifact_refs must follow
# both jobs and artifacts.
SAMPLE_SUBQUERY = (
    "SELECT id FROM ("
    "SELECT id, row_number() OVER (PARTITION BY workflow_key "
    "ORDER BY created_at DESC, id DESC) AS rn FROM public.jobs"
    ") t WHERE rn <= {limit}"
)

# (table, WHERE clause over the sample set). ``{sample}`` is replaced with
# the SAMPLE_SUBQUERY text.
LAYER2_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "job_batches",
        "id IN (SELECT DISTINCT batch_id FROM public.jobs "
        "WHERE id IN ({sample}) AND batch_id <> '')",
    ),
    ("artifacts", "hash IN (SELECT hash FROM public.artifact_refs WHERE job_id IN ({sample}))"),
    ("jobs", "id IN ({sample})"),
    ("job_nodes", "job_id IN ({sample})"),
    ("node_runs", "job_id IN ({sample})"),
    ("node_run_token_usage", "job_id IN ({sample})"),
    ("node_shards", "job_id IN ({sample})"),
    ("artifact_refs", "job_id IN ({sample})"),
)

# Target-side tables wiped before layer 2 loads. CASCADE also clears runtime
# tables referencing jobs (agent_execution_requests, executor_leases), which
# is intended: they would point at nonexistent jobs after a partial copy.
LAYER2_TRUNCATE = ("jobs", "job_batches", "artifacts")


class SeedError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------


def parse_dsn(dsn: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(dsn)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise SeedError(f"目标 DSN 必须是 postgresql:// URL，收到: {dsn!r}")
    return parsed


def guard_target_dsn(dsn: str) -> None:
    """Refuse to run against anything that looks like the prod database."""
    parsed = parse_dsn(dsn)
    host = parsed.hostname or ""
    dbname = parsed.path.lstrip("/")
    if dbname == PROD_DB_NAME:
        raise SeedError(f"拒绝执行：目标库名 {dbname!r} 与生产库同名，疑似指向生产。")
    if host not in LOOPBACK_HOSTS:
        raise SeedError(f"拒绝执行：目标 host {host!r} 不是本机回环地址 {sorted(LOOPBACK_HOSTS)}。")


def read_env_file_database_url(env_file: Path) -> str | None:
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "AGENT_LEGION_DATABASE_URL":
            return value.strip().strip('"').strip("'")
    return None


def resolve_target_dsn(environ: dict[str, str], env_file: Path) -> str:
    dsn = environ.get("AGENT_LEGION_DATABASE_URL") or read_env_file_database_url(env_file)
    if not dsn:
        raise SeedError("找不到目标库 DSN：请设置 AGENT_LEGION_DATABASE_URL 或写进 .env。")
    return dsn


def build_sample_sql(limit: int) -> str:
    if limit < 1:
        raise SeedError("--jobs-per-workflow 必须 >= 1")
    return SAMPLE_SUBQUERY.format(limit=int(limit))


def build_copy_out_sql(table: str, columns: Sequence[str], where: str) -> str:
    cols = ", ".join(f'"{c}"' for c in columns)
    return (
        f'COPY (SELECT {cols} FROM public."{table}" WHERE {where}) '
        "TO STDOUT WITH (FORMAT csv, HEADER true)"
    )


def build_copy_in_sql(table: str, columns: Sequence[str]) -> str:
    cols = ", ".join(f'"{c}"' for c in columns)
    return f'COPY public."{table}" ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)'


def artifact_blob_relpath(hash_value: str) -> Path:
    """Map an artifact hash to its blob path under data/ (prefix dir + hash)."""
    digest = hash_value.strip()
    if not digest or "/" in digest or ".." in digest:
        raise SeedError(f"非法 artifact hash: {hash_value!r}")
    return Path("artifacts") / digest[:2] / digest


def job_storage_relpath(storage_dir: str, workflow_key: str, job_id: str) -> Path:
    """On-disk job dir relative to data/; falls back to the conventional path."""
    rel = (storage_dir or "").strip() or f"jobs/{workflow_key}/{job_id}"
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        raise SeedError(f"非法 job storage_dir: {storage_dir!r}")
    return path


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
        raise SeedError(
            f"命令失败 ({proc.returncode}): {' '.join(cmd[:6])}...\n"
            f"{proc.stderr.decode(errors='replace')[-2000:]}"
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
    return proc.stdout.decode()


def target_psql(psql: str, dsn: str, sql: str, *, stdin=None) -> str:
    proc = run(
        [psql, dsn, "-v", "ON_ERROR_STOP=1", "-X", "-At", "-c", sql],
        stdin=stdin,
        stdout=subprocess.PIPE,
    )
    return proc.stdout.decode()


def ensure_target_schema(psql: str, dsn: str) -> None:
    exists = target_psql(psql, dsn, "SELECT to_regclass('public.workspaces')").strip()
    if exists and exists != "-":
        return
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


def restore_plain_sql(psql: str, dsn: str, dump_path: Path) -> None:
    """Restore a plain-format dump with FK triggers disabled.

    Source (prod container) and target (local dev) both run PG 17; plain SQL
    keeps layer 1 agnostic to pg_dump custom-format versions. PG 17 dumps emit
    ``SET transaction_timeout`` statements; they are header lines outside COPY
    data and are filtered out on the fly so a newer source dump still loads on
    an older target.
    ``session_replication_role = replica`` (target user is the local
    superuser) makes the COPY order FK-agnostic.
    """
    proc = subprocess.Popen(
        [psql, dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    with dump_path.open("rb") as fh:
        proc.stdin.write(b"SET session_replication_role = replica;\n")
        for line in fh:
            if line.startswith(b"SET transaction_timeout"):
                continue
            proc.stdin.write(line)
        proc.stdin.write(b"\nSET session_replication_role = DEFAULT;\n")
    proc.stdin.close()
    stderr = proc.stderr.read() if proc.stderr else b""
    if proc.wait() != 0:
        raise SeedError(
            f"psql 恢复 {dump_path.name} 失败:\n{stderr.decode(errors='replace')[-2000:]}"
        )


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
    restore_plain_sql(psql, dsn, dump_path)


def seed_layer2_db(
    compose_dir: Path,
    dsn: str,
    pg_bin: Path,
    tmpdir: Path,
    limit: int,
    dry_run: bool,
) -> dict[str, int]:
    """Copy sampled job rows. Returns {table: loaded row count}."""
    psql = str(pg_bin / "psql") if str(pg_bin) else "psql"
    sample = build_sample_sql(limit)
    loaded: dict[str, int] = {}

    csv_paths: dict[str, Path] = {}
    for table, where_tpl in LAYER2_QUERIES:
        where = where_tpl.format(sample=sample)
        columns = target_columns(psql, dsn, table)
        if dry_run:
            count = source_query(
                compose_dir, f'SELECT count(*) FROM public."{table}" WHERE {where}'
            ).strip()
            log(f"[dry-run] 第 2 层 {table}: 将拷贝 {count} 行")
            loaded[table] = int(count)
            continue
        csv_path = tmpdir / f"{table}.csv"
        with csv_path.open("wb") as fh:
            run(
                source_psql_cmd(compose_dir) + ["-c", build_copy_out_sql(table, columns, where)],
                stdout=fh,
            )
        csv_paths[table] = csv_path

    if dry_run:
        return loaded

    truncate = ", ".join(f'public."{t}"' for t in LAYER2_TRUNCATE)
    log("第 2 层：目标侧 TRUNCATE jobs/job_batches/artifacts（CASCADE，含 runtime 引用表）")
    target_psql(psql, dsn, f"TRUNCATE {truncate} CASCADE")

    for table, _where in LAYER2_QUERIES:
        columns = target_columns(psql, dsn, table)
        with csv_paths[table].open("rb") as fh:
            target_psql(psql, dsn, build_copy_in_sql(table, columns), stdin=fh)
        count = int(target_psql(psql, dsn, f'SELECT count(*) FROM public."{table}"').strip())
        loaded[table] = count
        log(f"第 2 层 {table}: 灌入 {count} 行")

    return loaded


def fetch_sample_jobs(compose_dir: Path, limit: int) -> list[dict[str, str]]:
    sql = (
        "COPY (SELECT id, workflow_key, storage_dir FROM public.jobs "
        f"WHERE id IN ({build_sample_sql(limit)}) ORDER BY workflow_key, id) "
        "TO STDOUT WITH (FORMAT csv, HEADER true)"
    )
    proc = run(source_psql_cmd(compose_dir) + ["-c", sql], stdout=subprocess.PIPE)
    rows = list(csv.DictReader(proc.stdout.decode().splitlines()))
    return rows


def fetch_artifact_hashes(csv_path: Path) -> list[str]:
    with csv_path.open(newline="", encoding="utf-8") as fh:
        return sorted({row["hash"] for row in csv.DictReader(fh) if row.get("hash")})


def copy_job_files(
    prod_data: Path, dev_data: Path, sample_jobs: list[dict[str, str]], dry_run: bool
) -> tuple[int, int]:
    """Copy per-job directories. Returns (dirs_copied, bytes_copied)."""
    dirs = 0
    total_bytes = 0
    for job in sample_jobs:
        rel = job_storage_relpath(job["storage_dir"], job["workflow_key"], job["id"])
        src = prod_data / rel
        dst = dev_data / rel
        if not src.is_dir():
            log(f"跳过缺失 job 目录: {src}")
            continue
        if dry_run:
            size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
            log(f"[dry-run] 将拷贝 {src} -> {dst} ({size / 1e6:.1f} MB)")
            dirs += 1
            total_bytes += size
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("jobs.sqlite"))
        dirs += 1
        total_bytes += sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    return dirs, total_bytes


def copy_artifact_blobs(
    prod_data: Path, dev_data: Path, hashes: Sequence[str], dry_run: bool
) -> tuple[int, int, int]:
    """Copy artifact blobs. Returns (blobs_copied, bytes_copied, missing)."""
    copied = 0
    total_bytes = 0
    missing = 0
    for digest in hashes:
        rel = artifact_blob_relpath(digest)
        src = prod_data / rel
        dst = dev_data / rel
        if not src.is_file():
            missing += 1
            continue
        if dry_run:
            copied += 1
            total_bytes += src.stat().st_size
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        total_bytes += dst.stat().st_size
    return copied, total_bytes, missing


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
        help="每个 workflow_key 抽取的最新 job 数（默认 30）",
    )
    parser.add_argument(
        "--prod-compose-dir",
        default=None,
        help="prod compose 目录（默认 env SEED_PROD_COMPOSE_DIR 或 ../prod/deploy）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计与打印，不写目标库/文件")
    parser.add_argument("--skip-files", action="store_true", help="只灌数据库，不拷 data/ 文件")
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

    dsn = resolve_target_dsn(dict(os.environ), REPO_ROOT / ".env")
    guard_target_dsn(dsn)
    log(f"目标库: {parse_dsn(dsn).path.lstrip('/')} @ {parse_dsn(dsn).hostname}（护栏通过）")
    log(f"源: {compose_dir}（docker compose exec，只读）")

    pg_bin = pg_client_bin()
    psql = str(pg_bin / "psql") if str(pg_bin) else "psql"

    if args.dry_run:
        log("dry-run：不写目标库、不拷文件")

    ensure_target_schema(psql, dsn)
    source_query(compose_dir, "SELECT 1")

    with tempfile.TemporaryDirectory(prefix="seed-from-prod-") as tmp:
        tmpdir = Path(tmp)
        seed_layer1(compose_dir, dsn, pg_bin, tmpdir, args.dry_run)
        log(f"第 2 层：每个 workflow_key 最新 {args.jobs_per_workflow} 个 job")
        layer2_counts = seed_layer2_db(
            compose_dir, dsn, pg_bin, tmpdir, args.jobs_per_workflow, args.dry_run
        )

        sample_jobs = fetch_sample_jobs(compose_dir, args.jobs_per_workflow)
        log(f"样本 job 数: {len(sample_jobs)}")

        if args.skip_files:
            log("--skip-files：跳过 data/ 文件拷贝")
            job_stat = artifact_stat = None
        else:
            if not prod_data.is_dir():
                raise SeedError(f"源 data/ 目录不存在: {prod_data}")
            artifact_csv = tmpdir / "artifact_refs.csv"
            if args.dry_run:
                # dry-run 没落 artifact_refs.csv，直接从源查 hash 列表
                sample = build_sample_sql(args.jobs_per_workflow)
                out = source_query(
                    compose_dir,
                    f"SELECT DISTINCT hash FROM public.artifact_refs WHERE job_id IN ({sample})",
                )
                hashes = sorted(h for h in out.splitlines() if h.strip())
            else:
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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        print(f"[seed] 错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
