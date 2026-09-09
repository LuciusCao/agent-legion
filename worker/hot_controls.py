"""Hot-reload of the executor's dynamic controls (split from executor.py).

executor.py's exemption text named this split as the next pressure valve:
one block per claim-loop pass reloads claim controls / code concurrency /
transfer controls / ramp_up / claim_batch_limit from the state file. Every
loader must succeed before ANY value applies — a half-applied reload would
make the "keeping previous values" log line a lie.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yaml import YAMLError

from worker.claim_batch import load_claim_batch_limit
from worker.ramp_up import apply_ramp_hot_reload, load_ramp_up_controls
from worker.runtime import controls as runtime_controls
from worker.transfer_controls import load_transfer_controls

# code 容量 0→>0 热开被拒（缺沙箱包装器）的一次性提示文案；守卫语义见
# runtime/controls.hot_code_concurrency（EXEC-CODE-003 fail-closed）。
CODE_HOT_REJECT_HINT = (
    "max_code_concurrency 0→>0 需要可解析的沙箱包装器（velites-sandbox"
    " 或 velites，启动预检项），热更拒绝生效；docker 形态该包装器内置"
    "镜像（此错误通常意味着镜像损坏），裸机请安装后重启 worker"
)


@dataclass
class DynamicControls:
    """The claim loop's hot-reloadable values, applied as one unit."""

    max_concurrency: int
    claim_enabled: bool
    max_code_concurrency: int
    transfer: Any
    claim_batch_limit: int
    ramp: Any
    code_hot_reject_logged: bool = False


def reload_controls(
    config_path: Path,
    current: DynamicControls,
    uploads: Any,
    log: Callable[[str], None],
) -> tuple[DynamicControls | None, str | None]:
    """Reload every dynamic control; (None, error) = keep previous values.

    code 容量 0→>0 无沙箱的热更拒绝是 fail-closed（#284/EXEC-CODE-003）：
    保留旧值并打一次性提示（code_hot_reject_logged 随状态携带）。"""
    try:
        max_concurrency, claim_enabled, _ = runtime_controls.load_claim_controls(config_path)
        new_code_concurrency = runtime_controls.load_code_concurrency(config_path)
        new_transfer = load_transfer_controls(config_path)
        new_ramp_controls = load_ramp_up_controls(config_path)
        new_claim_batch_limit = load_claim_batch_limit(config_path)
    except (OSError, ValueError, YAMLError) as exc:
        return None, str(exc)
    max_code_concurrency, code_rejected = runtime_controls.hot_code_concurrency(
        current.max_code_concurrency, new_code_concurrency
    )
    if code_rejected and not current.code_hot_reject_logged:
        log(CODE_HOT_REJECT_HINT)
    uploads.set_max_concurrency(new_transfer.upload_max_concurrency)
    # #471 热更：开着的窗口只换参数不重置进度；置 null 立即关窗。
    # #546：批上限即时生效（下一轮的 batch_request 即按新值折算）。
    return DynamicControls(
        max_concurrency=max_concurrency,
        claim_enabled=claim_enabled,
        max_code_concurrency=max_code_concurrency,
        transfer=new_transfer,
        claim_batch_limit=new_claim_batch_limit,
        ramp=apply_ramp_hot_reload(current.ramp, new_ramp_controls, log),
        code_hot_reject_logged=code_rejected,
    ), None
