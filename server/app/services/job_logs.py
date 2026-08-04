from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.job_log_paths import (
    resolve_job_log_path,
    resolve_run_dir,
    resolve_run_dir_fallback,
)
from server.app.services.job_log_raw import read_raw_log
from server.app.services.job_log_renderer import render_log
from server.app.services.vault import VaultService, collect_vault_plaintexts
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

        run_dir = resolve_run_dir(run.get("run_dir") or "", self.settings)
        if run_dir is None:
            run_dir = resolve_run_dir_fallback(
                path,
                run.get("node_key") or "",
                run.get("job_id") or "",
                self.settings,
            )
        if not path.is_file():
            if run_dir is None or not (run_dir / "events.jsonl").is_file():
                return empty
            path = run_dir / "events.jsonl"
        rendered = render_log(
            path,
            run_dir,
            sanitize=self._sanitizer_for(job_id),
            command_json=run.get("command_json") or "[]",
        )
        return {
            "run_id": run_id,
            "log": rendered["log"],
            "truncated": rendered["truncated"],
            "structured": rendered["structured"],
            "raw_url": f"/api/jobs/{job_id}/runs/{run_id}/log?raw=1",
        }

    def read_raw(self, job_id: str, run_id: int) -> str:
        sanitizer = self._sanitizer_for(job_id)
        return sanitizer(read_raw_log(job_id, run_id, self.job_db, self.settings))

    def _sanitizer_for(self, job_id: str) -> Callable[[str], str]:
        job = self.job_db.get_job(job_id) or {}
        workspace_id = str(job.get("workspace_id") or "")

        def sanitize(text: str) -> str:
            return self._sanitize(text, workspace_id)

        return sanitize

    def _sanitize(self, text: str, workspace_id: str = "") -> str:
        paths = [
            (str(self.settings.root_dir.resolve()), "<local-path>"),
            (str(Path.home().resolve()), "<local-path>"),
        ]
        paths.sort(key=lambda item: len(item[0]), reverse=True)
        for original, replacement in paths:
            text = text.replace(original, replacement)

        secrets = self._collect_secrets(self.settings.config)
        if workspace_id:
            # Vault-resolved plaintexts are redacted too (VAULT-SECRET-001).
            vault = VaultService(self.job_db.path, self.settings.config)
            secrets.extend(collect_vault_plaintexts(vault, workspace_id))
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
