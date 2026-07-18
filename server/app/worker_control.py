import threading


class WorkspaceWorkerControl:
    """Per-workspace worker pause/resume control (in-memory, defaults to paused)."""

    def __init__(self) -> None:
        self._paused: dict[str, bool] = {}
        self._lock = threading.Lock()

    def pause(self, workspace_id: str) -> None:
        with self._lock:
            self._paused[workspace_id] = True

    def resume(self, workspace_id: str) -> None:
        with self._lock:
            self._paused[workspace_id] = False

    def is_paused(self, workspace_id: str) -> bool:
        with self._lock:
            return self._paused.get(workspace_id, True)
