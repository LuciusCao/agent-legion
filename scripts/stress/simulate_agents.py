"""Backend synthetic load generator for workspace job concurrency stress tests.

This script seeds a workspace with synthetic jobs and simulates high-frequency
job/node status transitions without invoking any LLM or external executor. It can
run against the project's PostgreSQL database directly, while
an optional SSE listener measures the rate at which the running backend broadcasts
aggregated patch batches.

Example:
    uv run python scripts/stress/simulate_agents.py \
        --workspace ws-stress --agents 100 --jobs 5000 \
        --event-rate 500 --duration 600
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import queue
import random
import sys
import time
import tracemalloc
import uuid
from pathlib import Path

# Allow importing `server` when the script is executed directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests  # noqa: E402

from scripts.stress._stress_auth import ensure_admin_session  # noqa: E402
from scripts.stress._stress_http_events import StressHttpEventRecorder  # noqa: E402
from scripts.stress._stress_metrics import StressMetrics  # noqa: E402
from server.app.jobs import JobQueries  # noqa: E402
from server.app.services.workflow_revision_format import (  # noqa: E402
    definition_hash,
    serialize_definition,
)
from server.app.settings import load_settings  # noqa: E402
from server.app.workflows.definition import (  # noqa: E402
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
)

logger = logging.getLogger(__name__)

_STRESS_WORKFLOW_KEY = "stress_concurrency"
_STRESS_NODE_KEYS = ["step_1", "step_2", "step_3"]


def _stress_workflow_definition(key: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        key=key,
        label="Stress Concurrency Workflow",
        intake=WorkflowIntake(),
        nodes={
            "step_1": WorkflowNode(key="step_1", label="Step 1", capability="noop"),
            "step_2": WorkflowNode(
                key="step_2", label="Step 2", capability="noop", after=["step_1"]
            ),
            "step_3": WorkflowNode(
                key="step_3", label="Step 3", capability="noop", after=["step_2"]
            ),
        },
    )


class StressSimulator:
    def __init__(
        self,
        workspace_id: str,
        agents: int,
        jobs: int,
        event_rate: int,
        duration: int,
        base_url: str | None,
        results_dir: Path,
    ) -> None:
        self.workspace_id = workspace_id
        self.agents = agents
        self.jobs_target = jobs
        self.event_rate = event_rate
        self.duration = duration
        self.base_url = base_url
        self.results_dir = results_dir
        self.metrics = StressMetrics(
            started_at=_iso_now(),
            agents=agents,
            jobs_target=jobs,
        )
        self._start_monotonic = time.monotonic()
        self._stop_event = asyncio.Event()
        self._job_ids: list[str] = []
        self._pending_flush_times: queue.Queue[float] = queue.Queue()
        self._session: requests.Session | None = None

    def _setup_db(self) -> JobQueries:
        settings = load_settings()
        job_db = JobQueries(settings.database_url, jobs_dir=settings.jobs_dir)
        return job_db

    def _ensure_workspace_and_revision(self, job_db: JobQueries) -> None:
        workspace = job_db.get_workspace(self.workspace_id)
        if workspace is None:
            job_db.create_workspace(
                name=self.workspace_id,
                default_workflow_key=self.workspace_id,
                default_entity="question",
            )
            logger.info("Created workspace %s", self.workspace_id)

        existing = job_db.get_active_workflow_revision(self.workspace_id, self.workspace_id)
        if existing is not None:
            return

        definition = _stress_workflow_definition(self.workspace_id)
        version = job_db.next_workflow_revision_version(self.workspace_id, self.workspace_id)
        definition_json = serialize_definition(definition)
        job_db.create_workflow_revision(
            revision_id=f"{self.workspace_id}-stress-rev-1",
            workspace_id=self.workspace_id,
            workflow_key=self.workspace_id,
            version=version,
            status="active",
            definition_json=definition_json,
            definition_hash=definition_hash(definition_json),
        )
        logger.info("Created active workflow revision for %s", self.workspace_id)

    def _seed_jobs(self, job_db: JobQueries) -> None:
        existing = job_db.list_jobs(workspace_id=self.workspace_id)
        existing_ids = {str(job["id"]) for job in existing}
        needed = max(0, self.jobs_target - len(existing_ids))
        if needed == 0:
            self._job_ids = list(existing_ids)
            self.metrics.jobs_created = len(existing_ids)
            logger.info("Reusing %d existing jobs", len(existing_ids))
            return

        revision = job_db.get_active_workflow_revision(self.workspace_id, self.workspace_id)
        if revision is None:
            raise RuntimeError("No active workflow revision for stress workflow")

        definition_json = str(revision["definition_json"])
        definition_dict = json.loads(definition_json)
        node_keys = list(definition_dict.get("nodes", {}).keys()) or _STRESS_NODE_KEYS
        revision_id = str(revision["id"])
        version = int(revision["version"])
        definition_hash = str(revision["definition_hash"])

        created: list[str] = []
        for i in range(needed):
            source_id = f"stress-{uuid.uuid4().hex[:12]}"
            job = job_db.create_job(
                workflow_key=self.workspace_id,
                source_type="question",
                source_id=source_id,
                run_id=f"{self.workspace_id}-batch",
                title=f"Stress job {i + 1}",
                node_keys=node_keys,
                workspace_id=self.workspace_id,
                workflow_revision_id=revision_id,
                workflow_version=version,
                workflow_definition_hash=definition_hash,
                workflow_definition_snapshot_json=definition_json,
            )
            created.append(str(job["id"]))
            if (i + 1) % 1000 == 0:
                logger.info("Seeded %d/%d jobs", i + 1, needed)

        self._job_ids = list(existing_ids | set(created))
        self.metrics.jobs_created = len(self._job_ids)
        logger.info("Total jobs available: %d", len(self._job_ids))

    async def _listen_sse(self) -> None:
        if self.base_url is None:
            return
        url = f"{self.base_url}/api/workspaces/{self.workspace_id}/events"
        try:
            # Workspace APIs require an authenticated session (auth guard).
            session = self._session or requests.Session()
            # requests does not support async streaming; use a thread for SSE.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._consume_sse_blocking, session, url)
        except Exception as exc:  # noqa: BLE001
            self.metrics.errors.append(f"SSE listener failed: {exc}")

    def _consume_sse_blocking(self, session: requests.Session, url: str) -> None:
        try:
            response = session.get(url, stream=True, timeout=10)
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if self._stop_event.is_set():
                    break
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload in ("", ":ok", ":heartbeat"):
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type")
                if msg_type == "job_patch_batch":
                    self.metrics.sse_messages_received += 1
                    self.metrics.patch_batch_sizes.append(len(data.get("jobs", [])))
                    now = time.monotonic()
                    while not self._pending_flush_times.empty():
                        try:
                            recorded_at = self._pending_flush_times.get_nowait()
                        except queue.Empty:
                            break
                        self.metrics.flush_latencies_ms.append((now - recorded_at) * 1000)
                elif msg_type == "resync_required":
                    self.metrics.resync_count += 1
        except Exception as exc:  # noqa: BLE001
            self.metrics.errors.append(f"SSE consume error: {exc}")

    async def _generate_events(self) -> None:
        job_ids = self._job_ids.copy()
        if not job_ids or self.base_url is None:
            return

        recorder = StressHttpEventRecorder(self.base_url, self.workspace_id, self._session)
        interval = self.agents / max(1, self.event_rate)
        end_time = time.monotonic() + self.duration
        events_issued = 0
        while time.monotonic() < end_time and not self._stop_event.is_set():
            batch_start = time.monotonic()
            events: list[tuple[str, str]] = []
            for _ in range(self.agents):
                job_id = random.choice(job_ids)
                roll = random.random()
                if roll < 0.01:
                    events.append((job_id, "created"))
                elif roll < 0.02:
                    events.append((job_id, "deleted"))
                else:
                    events.append((job_id, "updated"))

            if events:
                recorded, recorded_at = recorder.record_batch(events)
                events_issued += recorded
                if recorded_at is not None:
                    self._pending_flush_times.put_nowait(recorded_at)

            elapsed = time.monotonic() - batch_start
            sleep_for = max(0.0, interval - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

        self.metrics.events_recorded = events_issued

    async def _update_job_states(self) -> None:
        """Mirror synthetic events with real DB status changes for snapshot realism."""
        job_db = self._setup_db()
        job_ids = self._job_ids.copy()
        if not job_ids:
            return

        statuses = ["pending", "running", "completed", "failed"]
        nodes = _STRESS_NODE_KEYS
        end_time = time.monotonic() + self.duration
        # Throttle DB writes so state churn does not dominate the simulation.
        interval = max(0.05, 1.0 / (self.event_rate / 10))

        while time.monotonic() < end_time and not self._stop_event.is_set():
            start = time.monotonic()
            for _ in range(min(50, self.agents)):
                job_id = random.choice(job_ids)
                status = random.choice(statuses)
                job_db.update_job_status(job_id, status)
                node_key = random.choice(nodes)
                job_db.update_job_node(
                    job_id,
                    node_key,
                    status=random.choice(["pending", "running", "completed", "failed"]),
                )
            elapsed = time.monotonic() - start
            sleep_for = max(0.0, interval - elapsed)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def _sample_memory(self) -> None:
        end_time = time.monotonic() + self.duration
        while time.monotonic() < end_time and not self._stop_event.is_set():
            _, peak = tracemalloc.get_traced_memory()
            self.metrics.memory_high_water_mb = max(
                self.metrics.memory_high_water_mb, peak / (1024 * 1024)
            )
            await asyncio.sleep(1.0)

    async def run(self) -> None:
        tracemalloc.start()
        logger.info(
            "Starting stress simulation: workspace=%s agents=%d jobs=%d event_rate=%d duration=%d",
            self.workspace_id,
            self.agents,
            self.jobs_target,
            self.event_rate,
            self.duration,
        )

        job_db = self._setup_db()
        self._ensure_workspace_and_revision(job_db)
        self._seed_jobs(job_db)
        if self.base_url:
            self._session = ensure_admin_session(self.base_url)

        tasks = [
            asyncio.create_task(self._generate_events()),
            asyncio.create_task(self._update_job_states()),
            asyncio.create_task(self._sample_memory()),
        ]
        if self.base_url:
            tasks.append(asyncio.create_task(self._listen_sse()))

        await asyncio.gather(*tasks, return_exceptions=True)
        self._stop_event.set()

        tracemalloc.stop()
        self.metrics.finished_at = _iso_now()
        self.metrics.duration_seconds = time.monotonic() - self._start_monotonic
        self.metrics.raw_events_per_second = self.metrics.events_recorded / max(
            1, self.metrics.duration_seconds
        )
        self.metrics.sse_messages_per_second = self.metrics.sse_messages_received / max(
            1, self.metrics.duration_seconds
        )

        self._write_results()

    def _write_results(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = self.results_dir / "backend-metrics.json"
        metrics_path.write_text(
            json.dumps(self.metrics.summary(), indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Wrote backend metrics to %s", metrics_path)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synthetic backend load generator for agent concurrency stress tests."
    )
    parser.add_argument(
        "--workspace",
        default=_STRESS_WORKFLOW_KEY,
        help="Workspace id (= workflow key, schema v61)",
    )
    parser.add_argument("--agents", type=int, default=100, help="Concurrent synthetic agents")
    parser.add_argument("--jobs", type=int, default=5000, help="Target number of jobs")
    parser.add_argument(
        "--event-rate",
        type=int,
        default=500,
        help="Total raw events per second across all agents",
    )
    parser.add_argument("--duration", type=int, default=600, help="Run duration in seconds")
    parser.add_argument("--base-url", default=None, help="Backend base URL for SSE listening")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("stress-results/latest"),
        help="Directory to write metrics files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    simulator = StressSimulator(
        workspace_id=args.workspace,
        agents=args.agents,
        jobs=args.jobs,
        event_rate=args.event_rate,
        duration=args.duration,
        base_url=args.base_url,
        results_dir=args.results_dir,
    )
    try:
        asyncio.run(simulator.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception:  # noqa: BLE001
        logger.exception("Stress simulation failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
