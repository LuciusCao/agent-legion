"""Isolated S3-compatible storage for the real browser smoke runner."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

_IMAGE = "chrislusf/seaweedfs:4.45"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class TemporaryObjectStorage:
    env: dict[str, str]
    container_name: str = ""

    def close(self) -> None:
        if not self.container_name:
            return
        subprocess.run(
            ["docker", "rm", "--force", self.container_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )


def ensure_object_storage(data_dir: Path, worktree_name: str) -> TemporaryObjectStorage:
    if os.environ.get("AGENT_LEGION_S3_BUCKET", "").strip():
        return TemporaryObjectStorage({})
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("Browser smoke needs S3; configure AGENT_LEGION_S3_* or install Docker")
    slug = re.sub(r"[^a-z0-9-]", "-", worktree_name.lower()).strip("-")[:24]
    suffix = f"{os.getpid()}-{secrets.token_hex(3)}"
    container_name = f"agent-legion-e2e-s3-{slug}-{suffix}"
    bucket = f"agent-legion-e2e-{slug}-{suffix}"[:63]
    access_key = secrets.token_hex(10)
    secret_key = secrets.token_hex(20)
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    storage_dir = data_dir / "seaweedfs"
    storage_dir.mkdir(parents=True, exist_ok=True)
    command = """
if [ "$(stat -c %u /data 2>/dev/null)" != "1000" ]; then
  chown -R seaweed:seaweed /data 2>/dev/null || true
fi
umask 077
printf '%s\n' '{"identities":[{"name":"agent-legion","credentials":[{"accessKey":"'"$AGENT_LEGION_S3_ACCESS_KEY"'","secretKey":"'"$AGENT_LEGION_S3_SECRET_KEY"'"}],"actions":["Admin","Read","Write","List","Tagging"]}]}' > /tmp/s3.json
chown seaweed:seaweed /tmp/s3.json
exec su-exec seaweed weed server -dir=/data -s3 -s3.port=8333 -s3.config=/tmp/s3.json -master.volumeSizeLimitMB=64 -volume.max=8
"""
    try:
        started = subprocess.run(
            [
                docker,
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--publish",
                f"127.0.0.1:{port}:8333",
                "--env",
                f"AGENT_LEGION_S3_ACCESS_KEY={access_key}",
                "--env",
                f"AGENT_LEGION_S3_SECRET_KEY={secret_key}",
                "--volume",
                f"{storage_dir}:/data",
                "--entrypoint",
                "/bin/sh",
                _IMAGE,
                "-c",
                command,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except subprocess.SubprocessError:
        TemporaryObjectStorage({}, container_name).close()
        raise
    runtime = TemporaryObjectStorage(
        {
            "AGENT_LEGION_S3_ENDPOINT": endpoint,
            "AGENT_LEGION_S3_PUBLIC_ENDPOINT": endpoint,
            "AGENT_LEGION_S3_BUCKET": bucket,
            "AGENT_LEGION_S3_REGION": "us-east-1",
            "AGENT_LEGION_S3_ACCESS_KEY": access_key,
            "AGENT_LEGION_S3_SECRET_KEY": secret_key,
        },
        container_name,
    )
    if started.returncode != 0:
        runtime.close()
        raise RuntimeError(f"Unable to start temporary SeaweedFS: {started.stderr.strip()}")
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if requests.get(f"{endpoint}/healthz", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Temporary SeaweedFS did not become healthy within 90 seconds")
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
        )
        try:
            client.create_bucket(Bucket=bucket)
        finally:
            client.close()
    except BaseException:
        runtime.close()
        raise
    return runtime
