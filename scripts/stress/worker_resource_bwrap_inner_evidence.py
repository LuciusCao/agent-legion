"""Disposable preload evidence for bwrap namespace and mount failures."""

from __future__ import annotations

import base64
import fcntl
import json
from pathlib import Path

_C_SOURCE = (
    Path(__file__).with_name("worker_resource_bwrap_inner_events.c").read_text(encoding="utf-8")
)


_PRELOAD_GUARD = "test ! -e /etc/ld.so.preload && test ! -L /etc/ld.so.preload"
_SUPPORT = (
    "set -e; "
    f"{_PRELOAD_GUARD} || {{ echo 'existing /etc/ld.so.preload' >&2; exit 1; }}; "
    "touch /tmp/bwrap-inner.active; "
    "command -v cc || (apt-get update -qq && apt-get install -y -qq --no-install-recommends gcc libc6-dev); "
    f"printf '%s' '{base64.b64encode(_C_SOURCE.encode()).decode()}' | base64 -d > /tmp/bwrap-inner.c; "
    "cc -shared -fPIC -O2 -o /tmp/bwrap-inner.so /tmp/bwrap-inner.c -ldl; "
    "printf '%s\\n' /tmp/bwrap-inner.so > /etc/ld.so.preload"
)
_CLEANUP = "rm -f /etc/ld.so.preload /tmp/bwrap-inner.c /tmp/bwrap-inner.so /tmp/bwrap-inner-events.jsonl /tmp/bwrap-inner.active"
_LOCK, _MARKER = "/tmp/bwrap-inner.lock", "/tmp/bwrap-inner.active"
_EVENTS = "/tmp/bwrap-inner-events.jsonl"


def _root(run: object, command: str) -> str:
    return run.compose("exec", "-T", "--user", "0:0", "worker", "sh", "-c", command, capture=True)


def capture_inner_evidence(run: object) -> dict[str, object]:
    from scripts.stress.worker_resource_wrap_evidence import _probe

    with open(_LOCK, "a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("bwrap inner evidence is already running") from exc
        probe_error, reports = None, {}
        try:
            _root(run, f"rm -f {_MARKER}")
            _root(run, f"{_PRELOAD_GUARD} || {{ echo 'existing /etc/ld.so.preload' >&2; exit 1; }}")
            _root(run, _SUPPORT)
            for name, uid, gid in (
                ("root", "0", "0"),
                ("nonroot", run.env["MEASUREMENT_WORKER_UID"], run.env["MEASUREMENT_WORKER_GID"]),
            ):
                _root(run, f"rm -f {_EVENTS}")
                reports[name] = _probe(run, uid, gid)
                output = _root(run, f"test ! -f {_EVENTS} || cat {_EVENTS}")
                reports[name]["preload_events"] = [
                    json.loads(line) for line in output.splitlines() if line
                ]
            return reports
        except Exception as exc:
            probe_error = exc
            raise
        finally:
            try:
                _root(run, f"test ! -f {_MARKER} || {_CLEANUP}")
            except Exception as cleanup_error:
                if probe_error is not None:
                    raise RuntimeError(
                        f"probe failed: {probe_error}; cleanup failed: {cleanup_error}"
                    ) from probe_error
                raise
