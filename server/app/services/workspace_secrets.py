"""Workspace-scoped secrets facade behind the secrets API (spec D13)."""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.vault import VaultError, VaultService
from server.app.settings import Settings


class WorkspaceSecretsService:
    """Workspace-scoped vault facade behind the secrets API (spec D13)."""

    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self._job_db = job_db
        self._vault = VaultService(job_db.path, settings.config)

    def _ensure_workspace(self, workspace_id: str) -> None:
        if self._job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        self._ensure_workspace(workspace_id)
        return self._vault.list(workspace_id)

    def set(self, workspace_id: str, name: str, value: str) -> dict[str, Any]:
        self._ensure_workspace(workspace_id)
        try:
            return self._vault.set(workspace_id, name, value)
        except VaultError as exc:
            raise InvalidOperationError(str(exc)) from exc

    def delete(self, workspace_id: str, name: str) -> None:
        self._ensure_workspace(workspace_id)
        self._vault.delete(workspace_id, name)
