from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from server.app.db.migrations.errors import MigrationError


@dataclass(frozen=True)
class MigrationIssue:
    table: str
    row_key: str
    constraint: str
    message: str

    def __str__(self) -> str:
        return f"table={self.table} key={self.row_key} constraint={self.constraint}: {self.message}"


@dataclass(frozen=True)
class MigrationReport:
    migration_version: int
    migration_name: str
    issues: tuple[MigrationIssue, ...]

    def __str__(self) -> str:
        header = f"Migration {self.migration_version} ({self.migration_name}) blocked"
        if not self.issues:
            return header
        sorted_issues = sorted(
            self.issues,
            key=lambda issue: (issue.table, issue.row_key, issue.constraint),
        )
        details = "; ".join(str(issue) for issue in sorted_issues)
        return f"{header}: {details}"

    def to_dict(self) -> dict[str, object]:
        sorted_issues = sorted(
            self.issues,
            key=lambda issue: (issue.table, issue.row_key, issue.constraint),
        )
        return {
            "migration_version": self.migration_version,
            "migration_name": self.migration_name,
            "issues": [asdict(issue) for issue in sorted_issues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


class MigrationBlockedError(MigrationError):
    """A migration cannot proceed because it would violate a constraint.

    Carries a structured :class:`MigrationReport` describing every blocking
    issue so callers can inspect, log, or serialize it deterministically.
    """

    def __init__(self, report: MigrationReport) -> None:
        super().__init__(str(report))
        self.report = report


def raise_blocked(report: MigrationReport) -> None:
    raise MigrationBlockedError(report)
