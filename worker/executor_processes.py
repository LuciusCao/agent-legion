"""Executor Popen bookkeeping for the supervisor (#682).

Keeps the supervisor's executor children registered with ``ManagedChildren`` so
the PID 1 ``ZombieReaper`` never steals their exit status, owns the reaper's
start/stop (it only exists when this process is PID 1 with ``/proc``), and
unregisters an executor once its owner has wait()ed it so neither the table
nor the stdout pipe accumulates across restarts in non-PID-1 deployments.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence

from worker.zombie_reaper import ManagedChildren, ZombieReaper, reaping_enabled


class ExecutorProcesses:
    def __init__(self, log: Callable[[str], None]) -> None:
        self.managed = ManagedChildren()
        self.reaper = ZombieReaper(self.managed, log) if reaping_enabled() else None

    def start(self) -> None:
        if self.reaper is not None:
            self.reaper.start()

    def stop(self) -> None:
        if self.reaper is not None:
            self.reaper.stop()

    def spawn(self, argv: Sequence[str], env: Mapping[str, str]) -> subprocess.Popen[str]:
        """Spawn + register under the registry lock: no scan sees it unregistered."""
        return self.managed.spawn(
            lambda: subprocess.Popen(
                list(argv),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=dict(env),
                text=True,
                bufsize=1,
            )
        )

    def finished(self, process: subprocess.Popen[str]) -> None:
        """The owner has wait()ed ``process``: unregister it and close its pipe."""
        self.managed.forget(process)
        if process.stdout is not None:
            process.stdout.close()
