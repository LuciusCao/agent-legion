"""SeaweedFS 派生 bucket 的 collection 残留卷只读核查（#824，clean-worktree.sh 调用）。

SeaweedFS 把 bucket ``/buckets/<b>`` 下的对象写进 collection ``<b>``，每个
collection 首次写入即预分配一批 volume（单机 replication=000 默认 7 个），
在 master 的卷槽位上限里各占一席。S3 DeleteBucket 正常会连带删 collection
（实测 4.45 如此），但 master 侧 DeleteCollection 失败/超时时 bucket 元数据
已删、卷仍占槽位。本模块在 bucket 确认不存在后经 master ``/vol/status``
列出同名 collection 仍在册的 volume，供调用方告警并给出手动回收命令。

刻意只读、不自动 ``/col/delete``：「bucket 不存在」由 S3 判定，删除却打在
master 上，两步之间同名 worktree 可能被重建并写入（TOCTOU），master 地址
也可能指向另一个实例——自动删除的目标身份无法在本脚本内钉死，交给人在
确认 bucket 未被复用后执行。

护栏：collection 名必须是派生命名（``agent-legion-`` 前缀、非裸前缀），且
不在受保护名单（prod 主 bucket ``agent-legion`` 与 develop 的派生名）内；
无 collection 标记的公共卷（``""``，filer 元数据变更日志
``/topics/.system/log`` 落在这里）结构上不会被报告。

master 地址：显式 ``AGENT_LEGION_SEAWEEDFS_MASTER_URL`` 优先；否则仅当本地
后端是 seaweedfs 且 S3 endpoint 是 compose 约定的 ``:8333`` 时推导同主机
``:9333``——rustfs/外部 S3 推导不出地址，整步跳过。
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit

DERIVED_PREFIX = "agent-legion-"
# prod 主 bucket 与 develop worktree 的派生名：即使 bucket 已删也不经本
# 模块报告或给出回收命令（develop 的派生 bucket 就是 develop 环境的共享数据）。
PROTECTED_COLLECTIONS = frozenset({"agent-legion", "agent-legion-develop", "agent-legion-prod"})
MASTER_URL_ENV = "AGENT_LEGION_SEAWEEDFS_MASTER_URL"
SEAWEEDFS_S3_PORT = 8333
SEAWEEDFS_MASTER_PORT = 9333
HTTP_TIMEOUT_SECONDS = 15

FetchJson = Callable[[str], Any]


class CollectionGuardError(ValueError):
    """collection 名不在允许核查的派生命名空间内。"""


def check_derived(collection: str) -> None:
    if (
        not collection.startswith(DERIVED_PREFIX)
        or collection == DERIVED_PREFIX
        or collection in PROTECTED_COLLECTIONS
    ):
        raise CollectionGuardError(f"拒绝核查非派生或受保护的 collection: {collection!r}")


def resolve_master_url(endpoint_url: str | None, env: Mapping[str, str]) -> str | None:
    explicit = env.get(MASTER_URL_ENV, "").strip()
    if explicit:
        return explicit.rstrip("/")
    if env.get("AGENT_LEGION_LOCAL_S3_BACKEND", "seaweedfs").strip() not in ("", "seaweedfs"):
        return None
    if not endpoint_url:
        return None
    parts = urlsplit(endpoint_url)
    if parts.hostname is None or parts.port != SEAWEEDFS_S3_PORT:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme or 'http'}://{host}:{SEAWEEDFS_MASTER_PORT}"


def collection_volume_ids(master_url: str, collection: str, fetch: FetchJson) -> tuple[int, ...]:
    """master ``/vol/status`` 中属于 ``collection`` 的 volume id（升序）。"""
    status = fetch(f"{master_url}/vol/status")
    data_centers = (status.get("Volumes") or {}).get("DataCenters") or {}
    ids: set[int] = set()
    for racks in data_centers.values():
        for nodes in (racks or {}).values():
            for volumes in (nodes or {}).values():
                for volume in volumes or []:
                    if volume.get("Collection") == collection:
                        ids.add(int(volume["Id"]))
    return tuple(sorted(ids))


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return json.load(resp)


def leftover_volume_ids(
    master_url: str, collection: str, *, fetch: FetchJson = _fetch_json
) -> tuple[int, ...]:
    """已删 bucket 遗留的同名 collection 卷；调用方须先确认 bucket 不存在。"""
    check_derived(collection)
    return collection_volume_ids(master_url, collection, fetch)


def manual_reclaim_command(master_url: str, collection: str) -> str:
    """确认 bucket 未被复用后手动回收残留卷的命令（本模块从不自动执行）。"""
    check_derived(collection)
    query = urlencode({"collection": collection})
    return f"curl -fsS -X POST '{master_url}/col/delete" + "?" + f"{query}'"
