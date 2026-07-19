"""Gate: the literal 'begin immediate' may only live in db/transaction.py."""

from __future__ import annotations

import re
from pathlib import Path

SERVER_APP = Path(__file__).resolve().parents[2] / "server" / "app"
ALLOWED = {"server/app/db/transaction.py"}
_PATTERN = re.compile(r"begin\s+immediate", re.IGNORECASE)


def test_no_scattered_begin_immediate() -> None:
    offenders: list[str] = []
    for path in sorted(SERVER_APP.rglob("*.py")):
        rel = path.relative_to(SERVER_APP.parent.parent).as_posix()
        if rel in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _PATTERN.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "begin immediate outside db/transaction.py:\n" + "\n".join(offenders)
