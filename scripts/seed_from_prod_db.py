"""PostgreSQL restore primitives for ``seed_from_prod``."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.seed_dump_projection import project_copy_blocks


class SeedError(RuntimeError):
    pass


def build_copy_in_sql(table: str, columns: Sequence[str]) -> str:
    cols = ", ".join(f'"{c}"' for c in columns)
    return f"COPY public.\"{table}\" ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '\\N')"


def restore_plain_sql(
    psql: str,
    dsn: str,
    dump_path: Path,
    target_columns_by_table: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Restore one plain dump with FK triggers disabled for the load."""
    proc = subprocess.Popen(
        [psql, dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    with dump_path.open("rb") as fh:
        proc.stdin.write(b"SET session_replication_role = replica;\n")
        for line in project_copy_blocks(fh, target_columns_by_table or {}):
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


def load_csv_transaction(
    psql: str,
    dsn: str,
    csv_paths: Mapping[str, Path],
    columns_by_table: Mapping[str, Sequence[str]],
    tables: Sequence[str],
    truncate_tables: Sequence[str],
) -> None:
    """Load every Layer 2 table in one transaction after object preparation."""
    proc = subprocess.Popen(
        [psql, dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    proc.stdin.write(b"BEGIN;\n")
    truncate = ", ".join(f'public."{table}"' for table in truncate_tables)
    proc.stdin.write(f"TRUNCATE {truncate} CASCADE;\n".encode())
    for table in tables:
        proc.stdin.write(build_copy_in_sql(table, columns_by_table[table]).encode() + b"\n")
        with csv_paths[table].open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                proc.stdin.write(chunk)
        proc.stdin.write(b"\n\\.\n")
    proc.stdin.write(b"COMMIT;\n")
    proc.stdin.close()
    stderr = proc.stderr.read() if proc.stderr else b""
    if proc.wait() != 0:
        raise SeedError(
            "Layer 2 psql 事务失败，已回滚全部 Layer 2 行；"
            f"已复制对象保留供重跑:\n{stderr.decode(errors='replace')[-2000:]}"
        )
