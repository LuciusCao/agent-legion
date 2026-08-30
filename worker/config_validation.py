"""Pure validation and normalization for the Worker service config.

Split from ``worker/config_store.py`` (#250 budget floors): the store keeps
persistence (atomic yaml writes, bootstrap import, scoped-token files), while
the identity/shape/normalization rules that reject surprising local control
input live here as module functions. Behavior is unchanged — the store
re-exports ``validate_config`` / ``public_config`` so existing callers
(supervisor, service, tests) keep their import paths.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from worker import worker_declarations
from worker.runtime.catalog import SUPPORTED_RUNTIMES, resolve_config_runtimes
from worker.runtime.controls import MAX_DYNAMIC_CONCURRENCY, validate_claim_controls

_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_EDITABLE_FIELDS = {
    "claim_enabled",
    "capabilities",
    "host_url",
    "worker_id",
    "name",
    "disabled_runtimes",
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
    "disabled_runtimes": [],
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
    # 生效声明 = 本机探测到的已安装 runtime − 停用集合（issue #254：探测即
    # 默认启用，反选停用；旧 opt-in runtimes 键由 catalog 迁移为补集停用）。
    # 空集合合法：本机只承接 code 任务或暂不接 agent。
    disabled, runtimes = resolve_config_runtimes(raw)
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
    # models allowlist 的 runtime 取值校验对齐支持全集而非生效集合：生效集合
    # 随机器安装状态浮动，持久化校验不该跟着漂（发现阶段仍按生效集合取交集）。
    models = worker_declarations.normalize_models(config.get("models", []), SUPPORTED_RUNTIMES)
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
        "disabled_runtimes": disabled,
        "runtimes": runtimes,
        "max_concurrency": concurrency,
        "max_code_concurrency": code_concurrency,
        "upload_max_concurrency": upload_concurrency,
        "claim_enabled": claim_enabled,
        "capabilities": capabilities,
        "labels": normalized_labels,
        "models": models,
    }
