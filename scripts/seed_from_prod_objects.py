"""Cross-bucket object copy for ``seed_from_prod`` (no network in unit tests).

Source rows keep their workspace/job ids and storage keys. Only rows whose
source object was verified and whose destination object is usable are emitted
for the Layer 2 transaction. Copy failures are row-local, sanitized, and
retryable; dry-run performs HEAD reads only.
"""

from __future__ import annotations

import csv
import re
import urllib.parse
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from server.app.storage import ObjectHead, S3Settings

PROD_BUCKET = "agent-legion"
DERIVED_BUCKET_PREFIX = "agent-legion-"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_OBJECT_BATCH_SIZE = 100


class ObjectSeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObjectFailure:
    table: str
    row_id: str
    storage_key: str
    operation: str
    reason: str


@dataclass
class ObjectCopyReport:
    table: str
    selected: int = 0
    rows_ready: int = 0
    copied: int = 0
    existing: int = 0
    planned: int = 0
    failures: list[ObjectFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


class CrossBucketStorage(Protocol):
    def head_source(self, storage_key: str) -> ObjectHead | None: ...

    def head_target(self, storage_key: str) -> ObjectHead | None: ...

    def copy_to_target(self, storage_key: str) -> None: ...


def _error_reason(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error", {})
        if isinstance(error, Mapping) and error.get("Code"):
            return str(error["Code"])
    return type(exc).__name__


def _normalized_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ObjectSeedError("S3 endpoint 必须是带 http(s) scheme 的 URL。")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ObjectSeedError("S3 endpoint 不得带 path/query/fragment。")
    host = parsed.hostname.lower()
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    authority = f"[{host}]" if ":" in host else host
    if port is not None and port != default_port:
        authority += f":{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def expected_dev_resource_names(repo_root: Path) -> tuple[str, str]:
    name = repo_root.name
    if (
        name.startswith("wt-")
        and repo_root.parent.name == "worktrees"
        and repo_root.parent.parent.name == ".tower"
    ):
        name = repo_root.parent.parent.parent.name
    db_name = "agent_legion_" + re.sub(r"[^a-zA-Z0-9_]", "_", name)
    bucket = DERIVED_BUCKET_PREFIX + re.sub(r"[^a-z0-9-]", "-", name.lower())
    return db_name, bucket


def guard_object_copy(
    source: S3Settings,
    target: S3Settings,
    *,
    expected_target_bucket: str,
) -> None:
    if source.bucket != PROD_BUCKET:
        raise ObjectSeedError(f"源 bucket 必须固定为 {PROD_BUCKET!r}，拒绝反向或任意来源复制。")
    if not expected_target_bucket.startswith(DERIVED_BUCKET_PREFIX):
        raise ObjectSeedError("目标 bucket 护栏内部错误：预期名称不是派生 bucket。")
    if target.bucket != expected_target_bucket or target.bucket == source.bucket:
        raise ObjectSeedError(
            f"目标 bucket 必须是当前 dev worktree 的 {expected_target_bucket!r}，"
            "拒绝写共享/prod 或其他 worktree bucket。"
        )
    source_endpoint = _normalized_endpoint(source.endpoint_url)
    target_endpoint = _normalized_endpoint(target.endpoint_url)
    if source_endpoint != target_endpoint:
        raise ObjectSeedError("server-side copy 只支持同一本机 S3 实例；源/目标 endpoint 不一致。")
    host = urllib.parse.urlsplit(target_endpoint).hostname or ""
    if host not in LOOPBACK_HOSTS:
        raise ObjectSeedError("目标 S3 endpoint 不是本机回环地址，拒绝写外部 bucket。")


def _lookup_env(environ: Mapping[str, str], env_file: Path, key: str) -> tuple[bool, str]:
    if key in environ:
        return True, environ[key].strip()
    if not env_file.is_file():
        return False, ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        match = re.match(rf"^\s*(?:export\s+)?{re.escape(key)}=(.*)$", line)
        if match is None:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return True, value
    return False, ""


def _value(environ: Mapping[str, str], env_file: Path, key: str, default: str = "") -> str:
    found, value = _lookup_env(environ, env_file, key)
    return value if found else default


def _secret(environ: Mapping[str, str], env_file: Path, key: str) -> str:
    value = _value(environ, env_file, key)
    if value:
        return value
    path = _value(environ, env_file, f"{key}_FILE")
    return Path(path).read_text(encoding="utf-8").strip() if path else ""


def target_s3_settings(environ: Mapping[str, str], env_file: Path) -> S3Settings | None:
    bucket = _value(environ, env_file, "AGENT_LEGION_S3_BUCKET")
    if not bucket:
        return None
    return S3Settings(
        bucket=bucket,
        endpoint_url=_value(environ, env_file, "AGENT_LEGION_S3_ENDPOINT"),
        region=_value(environ, env_file, "AGENT_LEGION_S3_REGION", "us-east-1"),
        access_key=_secret(environ, env_file, "AGENT_LEGION_S3_ACCESS_KEY"),
        secret_key=_secret(environ, env_file, "AGENT_LEGION_S3_SECRET_KEY"),
    )


def source_s3_settings(
    environ: Mapping[str, str],
    prod_env_file: Path,
    target: S3Settings,
    *,
    endpoint_override: str | None = None,
) -> S3Settings:
    if endpoint_override is not None:
        endpoint = endpoint_override.strip()
    else:
        found, endpoint = _lookup_env(environ, prod_env_file, "SEED_PROD_S3_ENDPOINT")
        if not found:
            found, endpoint = _lookup_env({}, prod_env_file, "AGENT_LEGION_S3_ENDPOINT")
        if not found:
            endpoint = target.endpoint_url
    return S3Settings(
        bucket=_value(environ, prod_env_file, "SEED_PROD_S3_BUCKET", PROD_BUCKET)
        or _value({}, prod_env_file, "AGENT_LEGION_S3_BUCKET", PROD_BUCKET),
        endpoint_url=endpoint,
        region=_value(environ, prod_env_file, "SEED_PROD_S3_REGION", target.region),
        access_key=_value(environ, prod_env_file, "SEED_PROD_S3_ACCESS_KEY")
        or _secret({}, prod_env_file, "AGENT_LEGION_S3_ACCESS_KEY"),
        secret_key=_value(environ, prod_env_file, "SEED_PROD_S3_SECRET_KEY")
        or _secret({}, prod_env_file, "AGENT_LEGION_S3_SECRET_KEY"),
    )


def _boto_client(settings: S3Settings) -> Any:
    import boto3
    from botocore.config import Config

    kwargs: dict[str, Any] = {
        "region_name": settings.region,
        "config": Config(
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 1},
            signature_version="s3v4",
        ),
    }
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    if settings.access_key:
        kwargs["aws_access_key_id"] = settings.access_key
        kwargs["aws_secret_access_key"] = settings.secret_key
    return boto3.client("s3", **kwargs)


class S3CrossBucketStorage:
    def __init__(
        self,
        source: S3Settings,
        target: S3Settings,
        *,
        source_client: Any | None = None,
        target_client: Any | None = None,
    ) -> None:
        self.source = source
        self.target = target
        self._source_client = source_client or _boto_client(source)
        self._target_client = target_client or _boto_client(target)

    @staticmethod
    def _head(client: Any, bucket: str, key: str) -> ObjectHead | None:
        from botocore.exceptions import ClientError

        try:
            response = client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return ObjectHead(
            size_bytes=int(response.get("ContentLength", 0)),
            etag=str(response.get("ETag", "")).strip('"'),
        )

    def check_buckets(self) -> None:
        self._source_client.head_bucket(Bucket=self.source.bucket)
        self._target_client.head_bucket(Bucket=self.target.bucket)

    def head_source(self, storage_key: str) -> ObjectHead | None:
        return self._head(self._source_client, self.source.bucket, storage_key)

    def head_target(self, storage_key: str) -> ObjectHead | None:
        return self._head(self._target_client, self.target.bucket, storage_key)

    def copy_to_target(self, storage_key: str) -> None:
        self._target_client.copy_object(
            Bucket=self.target.bucket,
            CopySource={"Bucket": self.source.bucket, "Key": storage_key},
            Key=storage_key,
        )


def _batches(rows: Iterator[dict[str, str]], size: int) -> Iterator[list[dict[str, str]]]:
    if size < 1:
        raise ObjectSeedError("object batch size 必须 >= 1")
    batch: list[dict[str, str]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _row_id(table: str, row: Mapping[str, str]) -> str:
    if table == "materials":
        return row.get("id", "")
    return "/".join((row.get("job_id", ""), row.get("node_key", ""), row.get("name", "")))


def copy_rows_with_objects(
    table: str,
    source_csv: Path,
    destination_csv: Path,
    columns: Sequence[str],
    storage: CrossBucketStorage,
    *,
    job_workspaces: Mapping[str, str],
    dry_run: bool,
    overwrite: bool,
    batch_size: int = DEFAULT_OBJECT_BATCH_SIZE,
) -> ObjectCopyReport:
    if table not in {"materials", "job_artifacts"}:
        raise ObjectSeedError(f"不支持的对象清单表: {table}")
    report = ObjectCopyReport(table=table)
    with (
        source_csv.open(newline="", encoding="utf-8") as src,
        destination_csv.open("w", newline="", encoding="utf-8") as dst,
    ):
        reader = csv.DictReader(src)
        missing_columns = {"storage_key", "size_bytes"} - set(reader.fieldnames or [])
        if missing_columns:
            raise ObjectSeedError(f"{table} 导出缺少列: {sorted(missing_columns)}")
        writer = csv.DictWriter(dst, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for batch in _batches((dict(row) for row in reader), batch_size):
            outcomes: dict[tuple[str, int], tuple[ObjectHead | None, ObjectFailure | None]] = {}
            for row in batch:
                report.selected += 1
                key = row.get("storage_key", "")
                row_id = _row_id(table, row)
                try:
                    size = int(row.get("size_bytes", ""))
                except ValueError:
                    size = -1
                failure: ObjectFailure | None = None
                if not key or size < 0:
                    failure = ObjectFailure(table, row_id, key, "validate", "invalid_row")
                elif table == "job_artifacts":
                    workspace_id = job_workspaces.get(row.get("job_id", ""), "")
                    expected = f"jobs/{workspace_id}/{row.get('job_id', '')}/"
                    if not workspace_id or not key.startswith(expected):
                        failure = ObjectFailure(table, row_id, key, "validate", "job_key_mismatch")
                if failure is not None:
                    report.failures.append(failure)
                    continue
                cache_key = (key, size)
                if cache_key not in outcomes:
                    outcomes[cache_key] = _copy_one(
                        table,
                        row_id,
                        key,
                        size,
                        storage,
                        dry_run=dry_run,
                        overwrite=overwrite,
                        report=report,
                    )
                head, failure = outcomes[cache_key]
                if failure is not None:
                    report.failures.append(
                        ObjectFailure(table, row_id, key, failure.operation, failure.reason)
                    )
                    continue
                assert head is not None
                if not dry_run and table == "materials" and "object_etag" in columns:
                    row["object_etag"] = head.etag
                writer.writerow({column: row.get(column, "") for column in columns})
                report.rows_ready += 1
    return report


def _copy_one(
    table: str,
    row_id: str,
    key: str,
    size: int,
    storage: CrossBucketStorage,
    *,
    dry_run: bool,
    overwrite: bool,
    report: ObjectCopyReport,
) -> tuple[ObjectHead | None, ObjectFailure | None]:
    operation = "head_source"
    try:
        source = storage.head_source(key)
        if source is None:
            raise _CopyRejected("missing_source")
        if source.size_bytes != size:
            raise _CopyRejected("source_size_mismatch")
        operation = "head_target"
        target = storage.head_target(key)
        if target is not None and target.size_bytes == size:
            report.existing += 1
            return target, None
        if target is not None and not overwrite:
            raise _CopyRejected("target_size_conflict")
        if dry_run:
            report.planned += 1
            return source, None
        operation = "copy"
        storage.copy_to_target(key)
        report.copied += 1
        operation = "head_target_after_copy"
        target = storage.head_target(key)
        if target is None or target.size_bytes != size:
            raise _CopyRejected("copy_validation_failed")
        return target, None
    except _CopyRejected as exc:
        return None, ObjectFailure(table, row_id, key, operation, str(exc))
    except Exception as exc:
        return None, ObjectFailure(table, row_id, key, operation, _error_reason(exc))


class _CopyRejected(RuntimeError):
    pass
