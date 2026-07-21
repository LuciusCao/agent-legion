from __future__ import annotations

import re

DatabaseDsn = str
_QMARK = re.compile(r"\?")


def postgres_sql(sql: str) -> str:
    """Translate the repository's parameter marker to psycopg's marker."""
    return _QMARK.sub("%s", sql)
