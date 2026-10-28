"""Docker Desktop functional-only cgroup reader lifecycle."""

from __future__ import annotations

import http.server
import json
import platform
import subprocess
import urllib.request
from typing import Any

from scripts.stress.worker_resource_measurement import CgroupV2Reader, discover_cgroup_v2


class RemoteCgroupReader:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _read(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            return json.loads(response.read())

    def memory_sample(self) -> dict[str, Any]:
        return self._read("/memory")

    def pids_sample(self) -> dict[str, Any]:
        return self._read("/pids")


def serve_cgroup_reader(container_pid: int, port: int) -> None:
    reader = CgroupV2Reader(discover_cgroup_v2(container_pid))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                payload: dict[str, Any] = {"status": "ok"}
            elif self.path == "/memory":
                payload = reader.memory_sample()
            elif self.path == "/pids":
                payload = reader.pids_sample()
            else:
                self.send_error(404)
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


def cgroup_reader(run: Any, container_pid: int) -> Any:
    if platform.system() == "Linux":
        return CgroupV2Reader(discover_cgroup_v2(container_pid))
    command = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        f"{run.project}-cgroup-reader",
        "--pid=host",
        "-v",
        "/sys/fs/cgroup:/sys/fs/cgroup:ro",
        "-p",
        f"127.0.0.1:{run.args.cgroup_reader_port}:8080",
        "agent-legion-host:measurement",
        "python",
        "-m",
        "scripts.stress.worker_resource_desktop",
        "serve",
        "--container-pid",
        str(container_pid),
        "--port",
        "8080",
    ]
    run.cgroup_reader_id = subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout.strip()
    base_url = f"http://127.0.0.1:{run.args.cgroup_reader_port}"
    run._wait_http(f"{base_url}/health")
    return RemoteCgroupReader(base_url)


def refresh_cgroup_reader(run: Any, container: str) -> None:
    if run.cgroup_reader_id:
        subprocess.run(
            ["docker", "rm", "-f", run.cgroup_reader_id],
            check=False,
            capture_output=True,
            env=run.env,
        )
        run.cgroup_reader_id = ""
    pid = int(
        subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Pid}}", container],
            check=True,
            capture_output=True,
            text=True,
            env=run.env,
        ).stdout.strip()
    )
    cgroup_reader(run, pid)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--container-pid", type=int, required=True)
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    serve_cgroup_reader(args.container_pid, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
