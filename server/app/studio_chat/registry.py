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

Stale-snapshot protection (#355): GET responses carry a content ``revision``
(see ``registry_revision``) that the admin editor round-trips on PUT; the
store compares it inside the FOR UPDATE RMW transaction and rejects a
mismatch with ``RegistryVersionMismatch`` instead of letting a whole-document
replace silently drop rows the client never saw (e.g. freshly detected
entries merged by a concurrent detection pass).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)
from server.app.studio_chat.registry_revision import (
    RegistryVersionMismatch,
    registry_revision,
)
from server.app.studio_chat.serving_address import derived_api_base

GLOBAL_SETTINGS_KEY = "studio_agents"
DEFAULT_API_BASE = "http://127.0.0.1:8000"

#: conditional_put 的合并器签名：updater(incoming=document, stored) → 落库文档。
RegistryMerger = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def default_registry_document() -> dict[str, Any]:
    """Code defaults. An unconfigured ``api_base`` falls back to this process's
    observed serving address (#915, serving_address.py), never blindly to the
    prod port; the constant is only the before-first-request last resort."""
    return {"api_base": derived_api_base() or DEFAULT_API_BASE, "agents": []}


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
        return self.get_with_revision()[0]

    def get_with_revision(self) -> tuple[dict[str, Any], str]:
        """Effective document plus its content revision (#355): both derive
        from the same raw stored read, so the version a GET hands out always
        matches what a PUT compare will recompute inside the write
        transaction."""
        stored = self._kv().get_global_settings_document(GLOBAL_SETTINGS_KEY)
        return self._effective(stored), registry_revision(stored)

    @staticmethod
    def _effective(stored: dict[str, Any] | None) -> dict[str, Any]:
        """Defaults synthesis over the raw stored document."""
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
        self, expected_revision: str, updater: RegistryMerger, document: dict[str, Any]
    ) -> dict[str, Any]:
        """带版本冲突检查的 RMW 写入（#355）。

        不变量：版本比对发生在 FOR UPDATE 行锁开启的同一事务内——判断与
        写入之间没有窗口，快照后落地的任何写入（探测合并、并发 PUT）必然
        先改变存储版本，使陈旧 PUT 以 RegistryVersionMismatch 被拒绝（事务
        回滚、存储不动），而非被整份覆盖静默删行。匹配则执行
        ``updater(incoming=document, stored)``（source 重导等合并）写入并
        返回**合并后的最终文档**——调用方据此构建响应，revision 即刚落库
        的这次写入（事务外再 get 可能把并发写入者的结果冒充本次保存返
        回）。空 ``expected_revision`` 跳过检查（一次性客户端的整份替换语
        义不变）。
        """
        merged: list[dict[str, Any]] = []

        def _rmw(stored: dict[str, Any]) -> dict[str, Any]:
            if expected_revision and registry_revision(stored) != expected_revision:
                raise RegistryVersionMismatch(
                    f"registry at {registry_revision(stored)}, not {expected_revision}"
                )
            merged.append(updater(document, stored))
            return merged[-1]

        self.update(_rmw)
        return merged[0]

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
