"""Persistent worker configuration storage with atomic writes and validation."""

from __future__ import annotations

import re
import secrets
import threading
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

from worker import worker_declarations
from worker._atomic import atomic_write
from worker.registration_token import normalized_registration_token
from worker.runtime_controls import MAX_DYNAMIC_CONCURRENCY, validate_claim_controls

_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_EDITABLE_FIELDS = {
    "claim_enabled",
    "capabilities",
    "host_url",
    "worker_id",
    "name",
    "runtimes",
    "max_concurrency",
    "upload_max_concurrency",
    "models",
    "labels",
    "poll_interval_seconds",
    "heartbeat_interval_seconds",
    "shutdown_grace_seconds",
}
_DEFAULTS: dict[str, Any] = {
    "claim_enabled": False,
    "capabilities": [],
    "host_url": "",
    "worker_id": "",
    "name": "",
    "runtimes": ["velites"],
    "max_concurrency": 1,
    "upload_max_concurrency": 4,
    "models": [],
    "labels": {},
    "register_token_file": "/run/secrets/agent_worker_register_token",
    "work_root": "/var/lib/agent-legion-worker",
    "poll_interval_seconds": 2,
    "heartbeat_interval_seconds": 15,
    "shutdown_grace_seconds": 25,
    "environment": {},
}


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return fields that are safe and useful to edit in the local UI."""
    return {key: config.get(key, _DEFAULTS[key]) for key in sorted(_EDITABLE_FIELDS)}


def validate_config(raw: dict[str, Any], *, require_identity: bool = True) -> dict[str, Any]:
    """Normalize a Worker config while rejecting surprising local control input."""
    if not isinstance(raw, dict):
        raise ValueError("配置必须是对象")
    config = {**_DEFAULTS, **raw}
    host_url = str(config["host_url"]).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(host_url)
    if require_identity and (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Host 地址必须是无用户名、查询参数和锚点的 http(s) URL")
    worker_id = str(config["worker_id"]).strip()
    if require_identity and not _WORKER_ID.fullmatch(worker_id):
        raise ValueError("Worker ID 需以字母或数字开头，且只能包含字母、数字、_、-")
    name = str(config.get("name", "")).strip() or worker_id
    if len(name) > 128:
        raise ValueError("Worker 名称不能超过 128 个字符")
    runtimes = sorted(set(str(value) for value in config.get("runtimes", [])))
    if not runtimes or any(value not in {"pi", "openclaw", "velites"} for value in runtimes):
        raise ValueError("运行时必须至少选择 pi、openclaw 或 velites 之一")
    concurrency = config.get("max_concurrency")
    claim_enabled = config.get("claim_enabled")
    validate_claim_controls(concurrency, claim_enabled)
    upload_concurrency = config.get("upload_max_concurrency")
    if (
        isinstance(upload_concurrency, bool)
        or not isinstance(upload_concurrency, int)
        or not 1 <= upload_concurrency <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError(f"上传并发数必须是 1 到 {MAX_DYNAMIC_CONCURRENCY} 的整数")
    normalized_labels = worker_declarations.normalize_labels(config.get("labels", {}))
    capabilities = worker_declarations.normalize_capabilities(config.get("capabilities", []))
    models = worker_declarations.normalize_models(config.get("models", []))
    for field in (
        "poll_interval_seconds",
        "heartbeat_interval_seconds",
        "shutdown_grace_seconds",
    ):
        value = config.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.2 <= value <= 3600
        ):
            raise ValueError(f"{field} 必须在 0.2 到 3600 秒之间")
    environment = config.get("environment", {})
    if not isinstance(environment, dict):
        raise ValueError("environment 必须是对象")
    return {
        **config,
        "host_url": host_url,
        "worker_id": worker_id,
        "name": name,
        "runtimes": runtimes,
        "max_concurrency": concurrency,
        "upload_max_concurrency": upload_concurrency,
        "claim_enabled": claim_enabled,
        "capabilities": capabilities,
        "labels": normalized_labels,
        "models": models,
    }


class WorkerConfigStore:
    def __init__(self, state_dir: Path, bootstrap_path: Path | None = None) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "worker.yaml"
        self.bootstrap_path = bootstrap_path
        self.bootstrap_error: str | None = None
        self._write_lock = threading.Lock()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() and bootstrap_path is not None and bootstrap_path.is_file():
            try:
                bootstrap = yaml.safe_load(bootstrap_path.read_text(encoding="utf-8"))
                self.write(validate_config(bootstrap))
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self.bootstrap_error = str(exc)

    def configured(self) -> bool:
        return self.path.is_file()

    def read(self, *, require_identity: bool = True) -> dict[str, Any]:
        if not self.path.is_file():
            return validate_config({}, require_identity=False)
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        return validate_config(raw, require_identity=require_identity)

    def update_public(
        self, payload: dict[str, Any], *, registration_token: str | None = None
    ) -> dict[str, Any]:
        # 写锁包住整个 read-modify-write，避免并发 PUT 互相覆盖。
        with self._write_lock:
            unknown = set(payload) - _EDITABLE_FIELDS
            if unknown:
                raise ValueError(f"不支持的配置项: {', '.join(sorted(unknown))}")
            current = self.read(require_identity=False)
            updated = validate_config({**current, **payload})
            if registration_token is not None:
                token = normalized_registration_token(registration_token)
                token_path = self.state_dir / "register_token"
                atomic_write(token_path, token + "\n", mode=0o600)
                updated["register_token_file"] = str(token_path)
            self.write(updated)
            return updated

    def write(self, config: dict[str, Any]) -> None:
        # 同目录临时文件 + fsync + os.replace，保证并发/掉电时状态文件不被写坏。
        atomic_write(
            self.path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False), mode=0o600
        )

    def control_token(self) -> str:
        """Read or create the local control-plane bearer token (0600 in state_dir)."""
        path = self.state_dir / "control_token"
        try:
            if token := path.read_text(encoding="utf-8").strip():
                return token
        except OSError:
            pass
        token = secrets.token_urlsafe(32)
        atomic_write(path, token + "\n", mode=0o600)
        return token
