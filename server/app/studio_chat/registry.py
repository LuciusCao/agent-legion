"""Instance-level ACP agent registry for Studio chat (phase 3 chunk 4).

Admins maintain the list of launchable ACP agents ({id, label, command,
args[]}, plus a server-managed ``source`` provenance marker: detected
catalog entries merge in without ever overriding manual rows, #332) plus
the API base URL the bundled MCP server should call back. The
document lives in ``global_settings`` under its own key (``studio_agents``)
rather than inside the monolithic ``instance`` settings document: that
document has whole-document replace semantics and an admin UI that rebuilds
the payload field-by-field, so a new nested block would be clobbered by any
unrelated settings save. Non-admin users only ever pick an agent id from this
list — arbitrary command lines never cross the API boundary (RCE guard).

Reads go to the DB per use (like the skill-source store), so registry edits
take effect without a restart. SQL lives in the queries layer
(``global_settings`` KV mixin, issue #281); this module keeps the
defaults-synthesis domain logic.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)

GLOBAL_SETTINGS_KEY = "studio_agents"
DEFAULT_API_BASE = "http://127.0.0.1:8000"


def default_registry_document() -> dict[str, Any]:
    return {"api_base": DEFAULT_API_BASE, "agents": []}


class RegistryVersionMismatch(Exception):
    """PUT 携带的版本与行锁内读到的版本不一致（#355）：调用方须以 409
    返回当前文档让前端刷新，不能沿用旧快照覆盖写入。"""


def registry_revision(document: dict[str, Any]) -> str:
    """注册表内容版本（#355）：对序列化后的存储文档取 sha256 前 16 位。

    不变量：仅由存储文档推导、不含任何响应端派生字段——探测/可用性结果
    不改变版本；任何改变 agents/api_base 的写入（admin PUT、探测合并）
    都会得到新值。同一文档始终得到同一值，故「快照版本 == 存储版本」
    即写入期间无人改动过。
    """
    canonical = json.dumps(
        {"api_base": document.get("api_base"), "agents": document.get("agents")},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def api_base_host_is_internal(api_base: str) -> bool:
    """Whether the api_base host keeps scoped tokens inside the network (#158).

    api_base is the egress target for the per-session scoped Bearer token; a
    host that is neither loopback nor a private address means the token
    leaves the machine, which the admin route warns about. Unresolvable
    hostnames cannot be classified locally and count as external.
    """
    host = (urlsplit(api_base).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class StudioAgentRegistryStore:
    """Read/write the ``studio_agents`` document in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def get(self) -> dict[str, Any]:
        """Return the effective document: stored values over code defaults."""
        return self._effective(self._kv().get_global_settings_document(GLOBAL_SETTINGS_KEY))

    @staticmethod
    def _effective(stored: dict[str, Any] | None) -> dict[str, Any]:
        """Defaults synthesis over the raw stored document (get 与版本比对
        共用)：GET 响应与 RMW 里的 revision 必须基于同一归一化视图——
        否则探测在空注册表上写入的裸文档（缺 api_base）会让两侧版本
        永不相等，当前版本也被误判为陈旧（#355）。"""
        document = default_registry_document()
        if stored is None:
            return document
        if stored.get("api_base"):
            document["api_base"] = str(stored["api_base"])
        if isinstance(stored.get("agents"), list):
            document["agents"] = stored["agents"]
        return document

    def put(self, document: dict[str, Any]) -> None:
        self._kv().put_global_settings_document(GLOBAL_SETTINGS_KEY, document)

    def update(self, updater: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """Transactionally read-modify-write the stored document (#332): the
        detected/manual source merges must not lose a concurrent admin edit
        or detection pass (single-transaction RMW, issue #281 KV mixin)."""
        self._kv().update_global_settings_document(GLOBAL_SETTINGS_KEY, updater)

    def conditional_put(
        self,
        expected_revision: str,
        updater: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """带版本冲突检查的 RMW 写入（#355，方案 1）。

        不变量：版本比对发生在 FOR UPDATE 行锁开启的同一事务内——版本
        判断与写入之间不存在任何窗口，探测提交的新 detected 行必然先改
        变存储版本、使陈旧 PUT 以 RegistryVersionMismatch 拒绝，而非被
        整份覆盖静默删除。比对基于与 GET 相同的归一化视图（_effective）。
        匹配则执行 updater(incoming=document, stored)（source 重导等合并）
        写入并返回合并前的存储文档；不匹配抛出，存储不动。
        expected_revision 为空表示不做检查（smoke 脚本等一次性客户端）。
        """
        current: dict[str, Any] | None = None

        def _rmw(stored: dict[str, Any]) -> dict[str, Any]:
            nonlocal current
            if not expected_revision:
                return updater(document, stored)
            current = stored
            effective = self._effective(stored)
            if registry_revision(effective) != expected_revision:
                raise RegistryVersionMismatch(
                    f"registry revision is now {registry_revision(effective)}, "
                    f"not {expected_revision}"
                )
            return updater(document, stored)

        self.update(_rmw)
        return current if current is not None else document

    def find_agent(self, agent_id: str) -> dict[str, Any] | None:
        for agent in self.get()["agents"]:
            if agent.get("id") == agent_id:
                return cast(dict[str, Any], agent)
        return None

    def _kv(self) -> GlobalSettingsKVQueriesMixin:
        """The KV accessor: the facade itself, or an adapter for a bare DSN
        (``ConnectSource`` contract, #187; SQL centralization #281)."""
        if isinstance(self._dsn, str):
            return global_settings_kv_from_dsn(self._dsn)
        return self._dsn
