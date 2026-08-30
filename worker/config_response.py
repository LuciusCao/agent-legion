"""GET/PUT /api/config 的响应组装（从 service.py 拆出，#250 预算纪律）。

除可编辑字段外附三类派生观测值：生效 runtimes（探测 − 停用）、Host 登记
集合 registered_runtimes、逐 runtime 的 runtime_status（含 pending_restart
标记，见 worker/runtime/status.py）。
"""

from __future__ import annotations

from typing import Any

from worker.registration.token import registration_token_configured
from worker.runtime.status import runtime_status_with_registration
from worker.supervisor import WorkerSupervisor, public_config


def public_config_response(supervisor: WorkerSupervisor, config: dict[str, Any]) -> dict[str, Any]:
    host_worker = supervisor.status().get("host_worker") or {}
    registered = host_worker.get("runtimes") if isinstance(host_worker, dict) else None
    rows, registered_runtimes = runtime_status_with_registration(
        config.get("disabled_runtimes", []),
        registered if isinstance(registered, list) else None,
    )
    return {
        **public_config(config),
        "runtimes": config.get("runtimes", []),
        "registered_runtimes": registered_runtimes,
        "runtime_status": rows,
        "register_token_configured": registration_token_configured(
            config, supervisor.store.state_dir
        ),
    }
