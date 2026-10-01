"""Generation handoff for upload tasks sharing one execution directory."""

from __future__ import annotations

import threading

from worker.upload.task import UploadTask


class UploadHandoff:
    """Track active uploads and fence execution-dir reuse across leases."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._depth = 0
        self._active: dict[str, UploadTask] = {}

    @property
    def depth(self) -> int:
        with self._lock:
            return self._depth

    def begin(self, task: UploadTask) -> None:
        with self._lock:
            current = self._active.get(task.execution_id)
            if current is not None and not current.delivery_done.is_set():
                raise RuntimeError(
                    f"upload task already active for {task.execution_id}: {current.lease_id}"
                )
            self._active[task.execution_id] = task
            self._depth += 1

    def cancel_begin(self, task: UploadTask) -> None:
        """Undo a task that failed before entering a scheduler lane."""
        with self._lock:
            if self._active.get(task.execution_id) is task:
                self._active.pop(task.execution_id, None)
                self._depth -= 1
            task.finalize_started = True
            task.delivery_done.set()

    def wait_for_prior(
        self,
        execution_id: str,
        lease_id: str,
        stop: threading.Event,
        ownership_lost: threading.Event | None = None,
    ) -> bool:
        """Condemn a prior lease and wait through its complete filesystem teardown.

        The incoming lease may itself expire while the old uploader drains;
        that verdict interrupts the wait just like Worker shutdown.
        """

        def interrupted() -> bool:
            return stop.is_set() or (ownership_lost is not None and ownership_lost.is_set())

        with self._lock:
            prior = self._active.get(execution_id)
            if prior is None:
                return not interrupted()
            done = prior.delivery_done
            if prior.lease_id != lease_id:
                prior.ownership_lost.set()
        while True:
            if interrupted():
                return False
            if done.wait(0.1):
                return not interrupted()

    def start_finalize(self, task: UploadTask) -> bool:
        with self._lock:
            if task.finalize_started:
                return False
            task.finalize_started = True
            return True

    def complete_finalize(self, task: UploadTask) -> None:
        """Release the next attempt only after all upload cleanup and accounting."""
        with self._lock:
            if self._active.get(task.execution_id) is task:
                self._active.pop(task.execution_id, None)
            self._depth -= 1
            negative_depth = self._depth < 0
            task.delivery_done.set()
        if negative_depth:
            raise AssertionError("upload queue depth became negative")
