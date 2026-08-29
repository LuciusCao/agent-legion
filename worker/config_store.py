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
from worker.registration.token import TOKEN_FILE_PATTERN, validated_registration_token
from worker.runtime.controls import MAX_DYNAMIC_CONCURRENCY, validate_claim_controls

_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_EDITABLE_FIELDS = {
    "claim_enabled",
    "capabilities",
    "host_url",
    "worker_id",
    "name",
    "runtimes",
    "max_concurrency",
    "max_code_concurrency",
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
    "max_code_concurrency": 0,
    "upload_max_concurrency": 4,
    "models": [],
    "labels": {},
    # scoped token 目录：state_dir/register_tokens/，每个 token 一个
    # "<id>.token" 文件（issue #35：多 workspace scoped token 注册）。
    "register_token_dir": "",
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
    # 批次 2 code 执行池；0 = 仅 agent。上限与 Host 注册契约（le=1024）一致。
    code_concurrency = config.get("max_code_concurrency", 0)
    if (
        isinstance(code_concurrency, bool)
        or not isinstance(code_concurrency, int)
        or not 0 <= code_concurrency <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError(f"code 并发数必须是 0 到 {MAX_DYNAMIC_CONCURRENCY} 的整数")
    upload_concurrency = config.get("upload_max_concurrency")
    if (
        isinstance(upload_concurrency, bool)
        or not isinstance(upload_concurrency, int)
        or not 1 <= upload_concurrency <= MAX_DYNAMIC_CONCURRENCY
    ):
        raise ValueError(f"上传并发数必须是 1 到 {MAX_DYNAMIC_CONCURRENCY} 的整数")
    normalized_labels = worker_declarations.normalize_labels(config.get("labels", {}))
    capabilities = worker_declarations.normalize_capabilities(config.get("capabilities", []))
    models = worker_declarations.normalize_models(config.get("models", []), runtimes)
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
        "max_code_concurrency": code_concurrency,
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

    def token_dir(self) -> Path:
        """Directory holding one "<id>.token" file per scoped register token."""
        configured = str(self.read(require_identity=False).get("register_token_dir") or "")
        return Path(configured) if configured else self.state_dir / "register_tokens"

    def read_registration_tokens(self) -> list[dict[str, Any]]:
        """List configured scoped tokens as [{'token_id', 'token'}] rows."""
        directory = self.token_dir()
        tokens: list[dict[str, Any]] = []
        try:
            files = sorted(directory.iterdir())
        except OSError:
            return tokens
        for path in files:
            if not TOKEN_FILE_PATTERN.fullmatch(path.name):
                continue
            try:
                token = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if token:
                tokens.append({"token_id": path.name[: -len(".token")], "token": token})
        return tokens

    def upsert_registration_token(self, token: str) -> dict[str, Any]:
        """Atomically add (or replace) one scoped register token; returns its row.

        The token file name is derived from the token's own id prefix so the
        console can correlate a card with its file. Writing is idempotent."""
        token_id, normalized = validated_registration_token(token)
        path = self.token_dir() / f"{token_id}.token"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, normalized + "\n", mode=0o600)
        return {"token_id": token_id, "token": normalized}

    def remove_registration_token(self, token_id: str) -> bool:
        """Delete one scoped token file; True when a file was removed."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", token_id):
            return False
        path = self.token_dir() / f"{token_id}.token"
        try:
            path.unlink()
        except OSError:
            return False
        return True

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
                # 兼容路径：老客户端/脚本仍以单 token 字段提交；落成
                # register_tokens/ 下的一个文件，与 UI 的添加操作同构。
                # 先校验 token：校验失败时配置不落盘，避免 API 422 但配置
                # 已生效的半应用状态。写入顺序保持 配置 → token 文件：中途
                # 崩溃只留孤儿 token 文件（重新提交即可收敛），而配置引用的
                # 是目录而非单个 token，不会出现配置指向缺失文件的状态。
                validated_registration_token(registration_token)
                updated["register_token_dir"] = str(self.state_dir / "register_tokens")
                self.write(updated)
                self.upsert_registration_token(registration_token)
                return updated
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
