"""Studio chat agent registry content revision primitives (#355).

Split from ``registry.py`` (file budget): the revision compares the *raw
stored* document, so it shares no normalization with the store's
defaults-synthesis view and can live as a pure function of the stored bytes.

The revision input is deliberately limited to the stored ``agents`` and an
*explicitly stored* ``api_base``: an unconfigured ``api_base`` is derived from
the process's actual serving address (#915), which changes across restarts and
ports — including the derived value would falsely mark every pre-restart
snapshot stale. Response-side derived fields (detection/availability/revision
itself) never participate.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class RegistryVersionMismatch(Exception):
    """PUT 携带的快照版本与行锁内读到的存储版本不一致：调用方须以 409 返回
    当前文档让客户端刷新，不能沿用旧快照覆盖写入。"""


def registry_revision(stored: dict[str, Any] | None) -> str:
    """注册表内容版本：存储文档 canonical JSON 的 sha256 前 16 位。

    同一存储内容始终得到同一值，任何改变 agents 或显式 api_base 的写入都
    得到新值——故「快照版本 == 行锁内读到的版本」即快照后无人改动过存储。
    """
    stored = stored or {}
    agents = stored.get("agents")
    payload = {
        "api_base": stored.get("api_base") or None,
        "agents": agents if isinstance(agents, list) else [],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
