"""Gate: application runtime must not regain a SQLite compatibility path."""

from __future__ import annotations

from pathlib import Path

SERVER_APP = Path(__file__).resolve().parents[2] / "server" / "app"
FORBIDDEN = ("import sqlite3", "from sqlite3", "connect_sqlite", "begin immediate")


def test_postgres_is_the_only_runtime_database() -> None:
    offenders: list[str] = []
    for path in sorted(SERVER_APP.rglob("*.py")):
        rel = path.relative_to(SERVER_APP.parent.parent).as_posix()
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if any(token in line.lower() for token in FORBIDDEN):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "SQLite runtime dependency detected:\n" + "\n".join(offenders)
