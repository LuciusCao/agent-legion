"""COPY-block column projection for scripts/seed_from_prod.py layer 1.

Split out of seed_from_prod.py for the file budget; pure (no DB, no Docker).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence

_COPY_HEADER = re.compile(rb'^COPY public\."?([A-Za-z0-9_]+)"? \(([^)]*)\) FROM stdin;\n?$')


def project_copy_blocks(
    lines: Iterable[bytes], target_columns_by_table: Mapping[str, Sequence[str]]
) -> Iterator[bytes]:
    """Drop source-only columns from a plain pg_dump's COPY blocks.

    Layer 1 copies from a source instance that may run one schema version
    behind the target (prod pulls only after merge). A column the target has
    retired — e.g. ``workspaces.default_workflow_key`` dropped at v91 (#211
    M3) — would make the whole COPY fail. Each block's header is rewritten to
    the columns the target still has and every data row loses the matching
    fields (COPY text rows are tab-separated; a literal tab inside a value
    is escaped as ``\\t``, so splitting on tabs is exact). Columns the target
    added since keep their defaults, as with any explicit column list.
    """
    keep: list[int] | None = None
    for line in lines:
        if keep is not None:
            if line.rstrip(b"\n") == b"\\.":
                keep = None
                yield line
                continue
            fields = line.rstrip(b"\n").split(b"\t")
            yield b"\t".join(fields[i] for i in keep) + b"\n"
            continue
        match = _COPY_HEADER.match(line)
        if match is None:
            yield line
            continue
        table = match.group(1).decode()
        columns = [c.strip().strip('"') for c in match.group(2).decode().split(",")]
        allowed = set(target_columns_by_table.get(table, columns))
        indexes = [i for i, column in enumerate(columns) if column in allowed]
        if len(indexes) == len(columns):
            yield line
            continue
        dropped = [c for c in columns if c not in allowed]
        print(
            f"[seed_from_prod] 第 1 层 {table}: 目标库已无列 {', '.join(dropped)}，导入时剥离",
            file=sys.stderr,
        )
        kept = ", ".join(columns[i] for i in indexes)
        yield f"COPY public.{table} ({kept}) FROM stdin;\n".encode()
        keep = indexes
