import threading


class WorkerControl:
    def __init__(self) -> None:
        self._paused = True
        self._lock = threading.Lock()

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused
