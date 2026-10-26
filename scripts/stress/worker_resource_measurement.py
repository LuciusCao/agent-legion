"""Raw cgroup-v2 sampling and summaries for the disposable Worker measurement stack.

The kernel cgroup is the measurement boundary: memory includes the container's
whole descendant tree and page cache, while pids counts threads. Docker stats,
process RSS, host CPU counts, and protocol concurrency caps are not substitutes.
The summary reports observations and headroom against the *existing* cgroup
limit; it deliberately never derives a recommended production limit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

MEMORY_FILES = ("memory.current", "memory.peak", "memory.max", "memory.stat")
PIDS_FILES = ("pids.current", "pids.max", "pids.events")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _key_values(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split()
        values[key] = int(value)
    return values


def _integer(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def discover_cgroup_v2(
    container_pid: int,
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> Path:
    """Resolve a container's unified cgroup from the host procfs, fail closed."""
    entries = (proc_root / str(container_pid) / "cgroup").read_text(encoding="utf-8")
    unified = [line for line in entries.splitlines() if line.startswith("0::")]
    if len(unified) != 1:
        raise RuntimeError(f"container pid {container_pid} has no unique cgroup-v2 entry")
    relative = PurePosixPath(unified[0].partition("::")[2])
    if relative.is_absolute() and ".." not in relative.parts:
        path = cgroup_root.joinpath(*relative.parts[1:])
        if path.is_dir():
            missing = [name for name in (*MEMORY_FILES, *PIDS_FILES) if not (path / name).is_file()]
            if not missing:
                return path
            raise RuntimeError(f"cgroup {path} lacks required files: {', '.join(missing)}")
    raise RuntimeError(f"invalid or inaccessible cgroup-v2 path {relative} for pid {container_pid}")


class CgroupV2Reader:
    def __init__(self, root: Path) -> None:
        self.root = root
        missing = [name for name in (*MEMORY_FILES, *PIDS_FILES) if not (root / name).is_file()]
        if missing:
            raise RuntimeError(f"cgroup {root} lacks required files: {', '.join(missing)}")

    def memory_sample(self) -> dict[str, Any]:
        return {
            "current_bytes": _integer(self.root / "memory.current"),
            "peak_bytes": _integer(self.root / "memory.peak"),
            "max": (self.root / "memory.max").read_text(encoding="utf-8").strip(),
            "stat": _key_values(self.root / "memory.stat"),
        }

    def pids_sample(self) -> dict[str, Any]:
        return {
            "current": _integer(self.root / "pids.current"),
            "max": (self.root / "pids.max").read_text(encoding="utf-8").strip(),
            "events": _key_values(self.root / "pids.events"),
        }


def percentile_95(values: Iterable[int]) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _limit_headroom(limit: str, observed: int | None) -> int | None:
    if limit == "max" or observed is None:
        return None
    return int(limit) - observed


def summarize_samples(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    materialized = list(samples)
    for sample in materialized:
        key = (str(sample.get("iteration", "unmarked")), str(sample.get("scenario", "unmarked")))
        grouped.setdefault(key, []).append(sample)
    grouped[("all", "all")] = materialized
    groups: dict[str, Any] = {}
    for (iteration, scenario), rows in sorted(grouped.items()):
        memory = [row["values"] for row in rows if row.get("kind") == "memory"]
        pids = [row["values"] for row in rows if row.get("kind") == "pids"]
        current = [int(row["current_bytes"]) for row in memory]
        peaks = [int(row["peak_bytes"]) for row in memory]
        pids_current = [int(row["current"]) for row in pids]
        memory_limit = str(memory[-1]["max"]) if memory else "max"
        pids_limit = str(pids[-1]["max"]) if pids else "max"
        stat_names = sorted({key for row in memory for key in row["stat"]})
        event_names = sorted({key for row in pids for key in row["events"]})
        groups[f"{iteration}/{scenario}"] = {
            "sample_counts": {"memory": len(memory), "pids": len(pids)},
            "memory": {
                "current_p95_bytes": percentile_95(current),
                "current_max_bytes": max(current, default=None),
                "kernel_peak_max_bytes": max(peaks, default=None),
                "limit": memory_limit,
                "headroom_p95_bytes": _limit_headroom(memory_limit, percentile_95(current)),
                "headroom_max_bytes": _limit_headroom(memory_limit, max(current, default=0)),
                "headroom_kernel_peak_bytes": _limit_headroom(memory_limit, max(peaks, default=0)),
                "stat_p95": {
                    key: percentile_95([row["stat"][key] for row in memory if key in row["stat"]])
                    for key in stat_names
                },
                "stat_max": {
                    key: max(
                        (row["stat"][key] for row in memory if key in row["stat"]), default=None
                    )
                    for key in stat_names
                },
            },
            "pids": {
                "current_p95": percentile_95(pids_current),
                "current_max": max(pids_current, default=None),
                "limit": pids_limit,
                "headroom_p95": _limit_headroom(pids_limit, percentile_95(pids_current)),
                "headroom_max": _limit_headroom(pids_limit, max(pids_current, default=0)),
                "events_max": {
                    key: max(
                        (row["events"][key] for row in pids if key in row["events"]), default=None
                    )
                    for key in event_names
                },
            },
        }
    return {
        "schema_version": 1,
        "percentile_method": "nearest-rank-p95",
        "headroom_semantics": "existing cgroup limit minus observed value; null when unlimited or unsampled",
        "recommended_limits": None,
        "groups": groups,
    }


class CgroupSampler:
    """Independent memory/pids writers with scenario markers and raw JSONL retention."""

    def __init__(
        self,
        reader: CgroupV2Reader,
        raw_path: Path,
        *,
        pids_interval_seconds: float = 0.1,
        memory_interval_seconds: float = 0.5,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if pids_interval_seconds <= 0 or memory_interval_seconds <= 0:
            raise ValueError("sampling intervals must be positive")
        self.reader = reader
        self.raw_path = raw_path
        self.pids_interval_seconds = pids_interval_seconds
        self.memory_interval_seconds = memory_interval_seconds
        self.monotonic = monotonic
        self.iteration = "unmarked"
        self.scenario = "unmarked"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._errors: list[str] = []
        self._file: Any = None

    def mark(self, iteration: str, scenario: str) -> None:
        with self._lock:
            self.iteration = iteration
            self.scenario = scenario
            if self._file is not None:
                self._write_locked("marker", {"iteration": iteration, "scenario": scenario})

    def _write_locked(self, kind: str, values: dict[str, Any]) -> None:
        self._file.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": kind,
                    "recorded_at": _utc_now(),
                    "monotonic_seconds": self.monotonic(),
                    "iteration": self.iteration,
                    "scenario": self.scenario,
                    "values": values,
                },
                sort_keys=True,
            )
            + "\n"
        )
        self._file.flush()

    def sample_memory(self) -> None:
        with self._lock:
            if self._file is not None:
                self._write_locked("memory", self.reader.memory_sample())

    def sample_pids(self) -> None:
        with self._lock:
            if self._file is not None:
                self._write_locked("pids", self.reader.pids_sample())

    def record_backlog(self, *, active: int, queued: int) -> None:
        with self._lock:
            if self._file is not None:
                self._write_locked(
                    "backlog",
                    {"upload_active_count": active, "upload_queued_count": queued},
                )

    def _loop(self, kind: str, interval: float) -> None:
        sample = self.sample_memory if kind == "memory" else self.sample_pids
        while not self._stop.is_set():
            started = self.monotonic()
            try:
                sample()
            except (OSError, ValueError, RuntimeError) as exc:
                with self._lock:
                    self._errors.append(f"{kind}: {exc}")
                return
            self._stop.wait(max(0.0, interval - (self.monotonic() - started)))

    def start(self) -> None:
        if self._file is not None:
            raise RuntimeError("sampler already started")
        self.raw_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.raw_path.open("x", encoding="utf-8")
        self._threads = [
            threading.Thread(
                target=self._loop, args=("memory", self.memory_interval_seconds), daemon=True
            ),
            threading.Thread(
                target=self._loop, args=("pids", self.pids_interval_seconds), daemon=True
            ),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> list[str]:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        if self._file is not None:
            self._file.close()
            self._file = None
        return list(self._errors)

    def write_summary(self, output_path: Path) -> dict[str, Any]:
        samples = [
            json.loads(line)
            for line in self.raw_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        summary = summarize_samples(sample for sample in samples if sample.get("kind") != "marker")
        summary["sampling_errors"] = list(self._errors)
        output_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary


def _command(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def collect_host_metadata(container: str, image: str, velites: Path) -> dict[str, Any]:
    """Collect only non-secret identity fields, never the container/image environment."""
    image_fields = _command(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{json .Id}}|{{json .RepoTags}}|{{json .RepoDigests}}|{{json .Os}}|{{json .Architecture}}",
            image,
        ]
    ).split("|")
    container_image = json.loads(
        _command(["docker", "inspect", "--format", "{{json .Image}}", container])
    )
    return {
        "captured_at": _utc_now(),
        "kernel": platform.release(),
        "kernel_version": platform.version(),
        "os": platform.system(),
        "arch": platform.machine(),
        "cgroup_version": 2,
        "cgroup_filesystem": _command(["stat", "-fc", "%T", "/sys/fs/cgroup"]),
        "docker_server": json.loads(
            _command(["docker", "version", "--format", "{{json .Server}}"])
        ),
        "worker_container": container,
        "worker_container_image_id": container_image,
        "worker_image": {
            "id": json.loads(image_fields[0]),
            "repo_tags": json.loads(image_fields[1]),
            "repo_digests": json.loads(image_fields[2]),
            "os": json.loads(image_fields[3]),
            "architecture": json.loads(image_fields[4]),
        },
        "velites": {
            "sha256": hashlib.sha256(velites.read_bytes()).hexdigest(),
            "version": _command([str(velites), "--version"]),
        },
    }


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization[\"'\s:=]+bearer\s+)[^\s,}\"']+"),
    re.compile(r"(?i)([\"']?(?:register_token|apiKey|api_key)[\"']?\s*[:=]\s*[\"'])[^\"']+"),
    re.compile(r"(?i)([?&](?:X-Amz-Credential|X-Amz-Signature)=)[^&\s]+"),
)


def sanitize_log_text(text: str, secrets: Iterable[str] = ()) -> str:
    sanitized = text
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        sanitized = sanitized.replace(secret, "<redacted>")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1<redacted>", sanitized)
    return sanitized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    summary = subparsers.add_parser("summarize", help="reproduce a summary from raw JSONL")
    summary.add_argument("--raw-samples", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    samples = [
        json.loads(line)
        for line in args.raw_samples.read_text(encoding="utf-8").splitlines()
        if line
    ]
    result = summarize_samples(sample for sample in samples if sample.get("kind") != "marker")
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
