from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_data_path

TAIL_READ_LIMIT = 12 * 1024
RETURN_LIMIT = 8 * 1024


class JobLogService:
    def __init__(self, settings: Settings, job_db: JobQueries) -> None:
        self.settings = settings
        self.job_db = job_db
        self.logs_root = (settings.logs_dir / "jobs").resolve()

    def read(self, job_id: str, run_id: int) -> dict[str, Any]:
        run = self.job_db.get_node_run(job_id, run_id)
        if run is None:
            raise NotFoundError("Run not found")

        log_path = run.get("log_path") or ""
        if not log_path:
            return {"run_id": run_id, "log": "", "truncated": False}

        try:
            path = resolve_data_path(log_path, self.settings.data_dir, allow_missing=True)
            path.relative_to(self.logs_root)
        except (ValueError, ManagedPathError) as exc:
            raise InvalidOperationError("Invalid log path") from exc

        if not path.exists() or not path.is_file():
            return {"run_id": run_id, "log": "", "truncated": False}

        size = path.stat().st_size
        if size == 0:
            return {"run_id": run_id, "log": "", "truncated": False}

        truncated = False
        with open(path, "rb") as f:
            if size > TAIL_READ_LIMIT:
                f.seek(-TAIL_READ_LIMIT, os.SEEK_END)
                raw = f.read(TAIL_READ_LIMIT)
                truncated = True
            else:
                raw = f.read()

        text = raw.decode("utf-8", errors="replace")
        sanitized = self._sanitize(text)
        encoded = sanitized.encode("utf-8")
        if len(encoded) > RETURN_LIMIT:
            sanitized = encoded[:RETURN_LIMIT].decode("utf-8", errors="ignore")
            truncated = True

        return {"run_id": run_id, "log": sanitized, "truncated": truncated}

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
