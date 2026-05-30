import threading


class WorkerControl:
    def __init__(self) -> None:
        self._paused = True
        self._tick_requested = False
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

    def request_tick(self) -> None:
        with self._lock:
            self._tick_requested = True

    def consume_tick(self) -> bool:
        with self._lock:
            tick = self._tick_requested
            self._tick_requested = False
            return tick
