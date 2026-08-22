"""Instance-level S3-compatible object storage configuration (env-only).

Material storage is platform infrastructure (same tier as the database), not
a business connector: endpoint/bucket/credentials are injected exclusively
through ``AGENT_LEGION_S3_*`` environment variables (MATERIAL-SECRET-001) —
never tracked yaml, the DB, API payloads, or logs. Secret values support the
``_FILE`` variant like the vault master key and worker register token.

An unconfigured store (no bucket) is a valid state: ``load_s3_settings``
returns None and the materials API degrades to 503 without affecting the
rest of the service.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_REGION = "us-east-1"


@dataclass(frozen=True)
class S3Settings:
    """Connection parameters for the instance object store."""

    bucket: str
    endpoint_url: str = ""
    region: str = _DEFAULT_REGION
    access_key: str = ""
    secret_key: str = ""


def _read_secret(env: str) -> str:
    """Resolve a secret env value, honouring its ``<env>_FILE`` variant."""
    value = os.environ.get(env, "").strip()
    if value:
        return value
    path = os.environ.get(f"{env}_FILE", "").strip()
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return ""


def load_s3_settings() -> S3Settings | None:
    """Load the object-store config from env; None when not configured.

    Only the bucket marks the store as configured. Endpoint and static
    credentials are optional: an empty endpoint targets AWS S3, and empty
    keys defer to the boto3 default credential chain (instance roles).
    """
    bucket = os.environ.get("AGENT_LEGION_S3_BUCKET", "").strip()
    if not bucket:
        return None
    return S3Settings(
        bucket=bucket,
        endpoint_url=os.environ.get("AGENT_LEGION_S3_ENDPOINT", "").strip(),
        region=os.environ.get("AGENT_LEGION_S3_REGION", "").strip() or _DEFAULT_REGION,
        access_key=_read_secret("AGENT_LEGION_S3_ACCESS_KEY"),
        secret_key=_read_secret("AGENT_LEGION_S3_SECRET_KEY"),
    )
