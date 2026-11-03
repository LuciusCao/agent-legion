"""Parent-network-namespace listener health and token controls for E6."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from typing import Any

from scripts.e2e.native_sandbox_probe_payload import LISTENER_TOKEN

Attempt = Callable[[], dict[str, Any]]
TcpAttempt = Callable[[str, int], dict[str, Any]]


class AbstractUnixListener:
    def __init__(self, name: str) -> None:
        self.name = name
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind("\0" + name)
        self._sock.listen()
        self._sock.settimeout(0.2)
        self._count = 0
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                connection, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                connection.sendall(LISTENER_TOKEN)
                with self._lock:
                    self._count += 1

    def count(self) -> int:
        with self._lock:
            return self._count

    def close(self) -> None:
        self._stopping.set()
        self._sock.close()
        self._thread.join(timeout=2)


def control_connection(host: str, port: int) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=2) as connection:
            received = connection.recv(len(LISTENER_TOKEN))
    except OSError as exc:
        return {"connected": False, "token_valid": False, "detail": str(exc)}
    return {"connected": True, "token_valid": received == LISTENER_TOKEN, "detail": "connected"}


def abstract_control(name: str) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect("\0" + name)
            received = connection.recv(len(LISTENER_TOKEN))
    except OSError as exc:
        return {"connected": False, "token_valid": False, "detail": str(exc)}
    return {"connected": True, "token_valid": received == LISTENER_TOKEN, "detail": "connected"}


def positive_controls(
    listeners: dict[str, Any], *, tcp_attempt: TcpAttempt, abstract_attempt: Attempt
) -> dict[str, dict[str, Any]]:
    controls = {}
    for name, listener in listeners.items():
        before = listener.count()
        attempt = (
            abstract_attempt()
            if name == "abstract_unix"
            else tcp_attempt(listener.host, listener.port)
        )
        after = listener.count()
        row = {**attempt, "before": before, "after": after}
        if (
            attempt["connected"] is not True
            or attempt["token_valid"] is not True
            or after - before != 1
        ):
            raise RuntimeError(f"listener positive control failed: {name}: {row}")
        controls[name] = row
    return controls
