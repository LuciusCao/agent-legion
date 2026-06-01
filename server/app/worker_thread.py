import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from server.app.db import Database
from server.app.pipeline.runners import RunnerPool
from server.app.settings import Settings
from server.app.worker_control import WorkerControl


class WorkerThread:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        runner_pool: RunnerPool,
        agent_manager: Any,
        worker_control: WorkerControl | None = None,
        max_workers: int | None = None,
    ):
        self.db = db
        self.settings = settings
        self.runner_pool = runner_pool
        self.agent_manager = agent_manager
        self.worker_control = worker_control or WorkerControl()
        self.max_workers = max_workers
        self.stop_event = threading.Event()
        self.executor: ThreadPoolExecutor | None = None
        self.running_futures: dict[str, Future[bool]] = {}
        self.running_local_counts: dict[str, int] = {}
        self.running_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        from server.app.worker import process_video_once
        from server.app.worker_scheduler import (
            DEFAULT_PHASE_CONCURRENCY,
            WorkerCapacity,
            get_phase_concurrency_limit,
            pick_next_work,
        )

        def _configured_worker_count(runner_count: int) -> int:
            if self.max_workers is not None:
                return self.max_workers
            local_slots = sum(
                get_phase_concurrency_limit(self.settings, phase)
                for phase in DEFAULT_PHASE_CONCURRENCY
            )
            return max(1, runner_count + local_slots)

        def _finish_agent_work(video_id: str, runner_index: int, agent_id: str) -> None:
            with self.running_lock:
                self.running_futures.pop(video_id, None)
            self.runner_pool.release(runner_index)
            self.agent_manager.set_idle(agent_id)

        def _finish_local_work(video_id: str, phase: str) -> None:
            with self.running_lock:
                self.running_futures.pop(video_id, None)
                next_count = self.running_local_counts.get(phase, 0) - 1
                if next_count > 0:
                    self.running_local_counts[phase] = next_count
                else:
                    self.running_local_counts.pop(phase, None)

        def _worker_loop() -> None:
            assert self.executor is not None
            while not self.stop_event.is_set():
                submitted = False
                if self.worker_control.is_paused():
                    self.stop_event.wait(1)
                    continue
                runner_slot = None
                try:
                    runner_slot = self.runner_pool.acquire()
                except RuntimeError:
                    runner_slot = None
                with self.running_lock:
                    running_video_ids = set(self.running_futures)
                    local_counts = dict(self.running_local_counts)
                work = pick_next_work(
                    self.db.list_videos(status_filter=["queued", "missing_url", "running"]),
                    running_video_ids=running_video_ids,
                    capacity=WorkerCapacity(
                        free_runner=runner_slot,
                        running_local_counts=local_counts,
                    ),
                    settings=self.settings,
                )
                if work is None:
                    if runner_slot is not None:
                        self.runner_pool.release(runner_slot[0])
                    wait_seconds = 0.2 if self.worker_control.consume_tick() else 3
                    self.stop_event.wait(wait_seconds)
                    continue

                video = work.video
                if work.kind == "agent":
                    if runner_slot is None:
                        self.stop_event.wait(1)
                        continue
                    runner_index, runner = runner_slot
                    agent_id = getattr(runner, "agent_id", f"runner-{runner_index}")
                    self.agent_manager.set_busy(agent_id, video)
                    future = self.executor.submit(
                        process_video_once,
                        self.db,
                        self.settings,
                        video["id"],
                        None,
                        runner,
                    )
                    with self.running_lock:
                        self.running_futures[video["id"]] = future

                    def _on_agent_done(
                        _f: Any,
                        vid: str = video["id"],
                        idx: int = runner_index,
                        aid: str = agent_id,
                    ) -> None:
                        _finish_agent_work(vid, idx, aid)

                    future.add_done_callback(_on_agent_done)
                    submitted = True
                else:
                    if runner_slot is not None:
                        self.runner_pool.release(runner_slot[0])
                    future = self.executor.submit(
                        process_video_once, self.db, self.settings, video["id"]
                    )
                    with self.running_lock:
                        self.running_futures[video["id"]] = future
                        self.running_local_counts[work.phase] = (
                            self.running_local_counts.get(work.phase, 0) + 1
                        )

                    def _on_local_done(
                        _f: Any, vid: str = video["id"], phase: str = work.phase
                    ) -> None:
                        _finish_local_work(vid, phase)

                    future.add_done_callback(_on_local_done)
                    submitted = True
                wait_seconds = (
                    0.2 if self.worker_control.consume_tick() else (1 if submitted else 3)
                )
                self.stop_event.wait(wait_seconds)

        runner_count = self.runner_pool.size()
        workers = _configured_worker_count(runner_count)
        self.executor = ThreadPoolExecutor(max_workers=workers)
        self._thread = threading.Thread(target=_worker_loop, name="video-hive-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3) -> None:
        self.stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        if self.executor:
            self.executor.shutdown(wait=False)
