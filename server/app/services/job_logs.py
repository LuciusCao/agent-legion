from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.job_log_raw import (
    read_raw_log,
    resolve_job_log_path,
    resolve_run_dir,
    resolve_run_dir_fallback,
)
from server.app.services.job_log_renderer import render_log
from server.app.settings import Settings


class JobLogService:
    def __init__(self, settings: Settings, job_db: JobQueries) -> None:
        self.settings = settings
        self.job_db = job_db

    def read(self, job_id: str, run_id: int) -> dict[str, Any]:
        run = self.job_db.get_node_run(job_id, run_id)
        if run is None:
            raise NotFoundError("Run not found")

        log_path_str = run.get("log_path") or ""
        empty = {"run_id": run_id, "log": "", "truncated": False, "structured": [], "raw_url": ""}
        if not log_path_str:
            return empty

        path = resolve_job_log_path(log_path_str, self.settings)
        if not path.exists() or not path.is_file():
            return empty

        run_dir = resolve_run_dir(run.get("run_dir") or "", self.settings)
        if run_dir is None:
            run_dir = resolve_run_dir_fallback(
                path,
                run.get("node_key") or "",
                run.get("job_id") or "",
                self.settings,
            )
        rendered = render_log(path, run_dir, sanitize=self._sanitize)
        return {
            "run_id": run_id,
            "log": rendered["log"],
            "truncated": rendered["truncated"],
            "structured": rendered["structured"],
            "raw_url": f"/api/jobs/{job_id}/runs/{run_id}/log?raw=1",
        }

    def read_raw(self, job_id: str, run_id: int) -> str:
        return self._sanitize(read_raw_log(job_id, run_id, self.job_db, self.settings))

    def _sanitize(self, text: str) -> str:
        paths = [
            (str(self.settings.root_dir.resolve()), "<local-path>"),
            (str(Path.home().resolve()), "<local-path>"),
        ]
        paths.sort(key=lambda item: len(item[0]), reverse=True)
        for original, replacement in paths:
            text = text.replace(original, replacement)

        secrets = self._collect_secrets(self.settings.config)
        secrets.sort(key=len, reverse=True)
        for secret in secrets:
            text = text.replace(secret, "<redacted>")

        return text

    def _collect_secrets(self, obj: Any) -> list[str]:
        secrets: list[str] = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_lower = str(key).lower()
                is_secret_key = any(
                    term in key_lower for term in ("token", "secret", "password", "api_key")
                )
                if is_secret_key:
                    secrets.extend(self._extract_secret_values(value))
                secrets.extend(self._collect_secrets(value))
        elif isinstance(obj, list):
            for item in obj:
                secrets.extend(self._collect_secrets(item))
        return secrets

    def _extract_secret_values(self, value: Any) -> list[str]:
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, (list, tuple)):
            return [item for item in value if isinstance(item, str) and item]
        return []
