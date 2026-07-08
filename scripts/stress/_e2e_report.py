"""Report generation helpers for the end-to-end stress runner."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class E2EStressReport:
    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    backend_command: str = ""
    frontend_command: str = ""
    backend_metrics_path: str | None = None
    frontend_metrics_path: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_report(report: E2EStressReport, report_path: Path, project_root: Path) -> None:
    backend_metrics: dict[str, Any] = {}
    if report.backend_metrics_path:
        backend_file = project_root / report.backend_metrics_path
        if backend_file.exists():
            backend_metrics = json.loads(backend_file.read_text(encoding="utf-8"))

    frontend_metrics: dict[str, Any] = {}
    if report.frontend_metrics_path:
        frontend_file = project_root / report.frontend_metrics_path
        if frontend_file.exists():
            frontend_metrics = json.loads(frontend_file.read_text(encoding="utf-8"))

    lines = [
        "# Large Scale Agent Concurrency Stress Report\n",
        f"**Run ID:** {report.run_id}\n",
        f"**Started:** {report.started_at}\n",
        f"**Finished:** {report.finished_at}\n",
    ]
    if report.backend_command:
        lines.append(f"\n## Backend Command\n\n```\n{report.backend_command}\n```\n")
    if report.frontend_command:
        lines.append(f"\n## Frontend Command\n\n```\n{report.frontend_command}\n```\n")
    lines += [
        "\n## Backend Metrics\n",
        "```json\n",
        json.dumps(backend_metrics, indent=2, default=str),
        "\n```\n",
        "\n## Frontend Metrics\n",
        "```json\n",
        json.dumps(frontend_metrics, indent=2, default=str),
        "\n```\n",
    ]
    if report.errors:
        lines.append("\n## Errors\n")
        for error in report.errors:
            lines.append(f"- {error}\n")
    report_path.write_text("".join(lines), encoding="utf-8")
