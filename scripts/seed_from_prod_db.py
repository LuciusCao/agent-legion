"""PostgreSQL restore primitives for ``seed_from_prod``."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.seed_dump_projection import project_copy_blocks


class SeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Layer1Dump:
    path: Path
    tables: Sequence[str]
    columns_by_table: Mapping[str, Sequence[str]]


def build_copy_in_sql(table: str, columns: Sequence[str]) -> str:
    cols = ", ".join(f'"{c}"' for c in columns)
    return (
        f"COPY public.\"{table}\" ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '\\N');"
    )


def load_csv_transaction(
    psql: str,
    dsn: str,
    csv_paths: Mapping[str, Path],
    columns_by_table: Mapping[str, Sequence[str]],
    tables: Sequence[str],
    truncate_tables: Sequence[str],
    *,
    layer1: Layer1Dump | None = None,
) -> None:
    """Load prepared layers atomically; copied objects survive a rollback."""
    proc = subprocess.Popen(
        [psql, dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    if layer1 is not None:
        proc.stdin.write(b"SET session_replication_role = replica;\n")
    proc.stdin.write(b"BEGIN;\n")
    if layer1 is not None:
        truncate = ", ".join(f'public."{table}"' for table in layer1.tables)
        proc.stdin.write(f"TRUNCATE {truncate} CASCADE;\n".encode())
        with layer1.path.open("rb") as fh:
            for line in project_copy_blocks(fh, layer1.columns_by_table):
                if not line.startswith(b"SET transaction_timeout"):
                    proc.stdin.write(line)
    truncate = ", ".join(f'public."{table}"' for table in truncate_tables)
    proc.stdin.write(f"TRUNCATE {truncate} CASCADE;\n".encode())
    for table in tables:
        proc.stdin.write(build_copy_in_sql(table, columns_by_table[table]).encode() + b"\n")
        last_byte = b"\n"
        with csv_paths[table].open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                proc.stdin.write(chunk)
                last_byte = chunk[-1:]
        proc.stdin.write(b"\\.\n" if last_byte == b"\n" else b"\n\\.\n")
    proc.stdin.write(b"COMMIT;\n")
    if layer1 is not None:
        proc.stdin.write(b"SET session_replication_role = DEFAULT;\n")
    proc.stdin.close()
    stderr = proc.stderr.read() if proc.stderr else b""
    if proc.wait() != 0:
        scope = "Layer 1/Layer 2" if layer1 is not None else "Layer 2"
        raise SeedError(
            f"{scope} psql 事务失败，已回滚全部 {scope} 行；"
            f"已复制对象保留供重跑；stderr {len(stderr)} bytes 已脱敏"
        )
