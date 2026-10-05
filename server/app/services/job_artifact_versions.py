"""不可变产物版本 key 的被取代对象清理（#853，设计见
docs/architecture/artifact-direct-url-pinning.md）。

#853 起新写入的 authority key 是一次性版本 key
（``jobs/{ws}/{job}/.v/{version}/{name}``，``job_artifact_objects.
artifact_version_key``）：同名产物再次登记不再覆盖既有对象，而是把清单行
改指新 key——此前签发的直连 URL 绑定旧 key，只会返回旧字节或 404，永远
不会返回新字节。代价是清单行改指后旧 key 成为被取代对象：登记事务内由
``executors._artifact_supersede.superseded_key_tx`` 找出，登记提交后由本
模块经共享的 ``artifact-authority`` 锁复核删除，让旧 URL 随之 404、对象
存储不随重登记膨胀、吊销 SOP「删除 job 即删除其对象」与 #853 之前同强度。
删除是 best-effort：失败只留孤儿（旧 URL 仍只能返回旧字节），由
``scripts/gc-s3-jobs.py`` 与 bucket lifecycle 兜底。
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.services.job_artifact_objects import KEY_PREFIX

logger = logging.getLogger(__name__)

# dot-prefixed segment: ``is_downloadable_artifact_name`` rejects dot-prefixed
# parts, so no servable artifact name can ever collide with the version space.
VERSION_SEGMENT = ".v"


def artifact_version_key(workspace_id: str, job_id: str, version: str, name: str) -> str:
    """Immutable per-write authority key: every registration lands on a fresh
    key, so a presigned URL minted for an earlier row can only return that
    row's bytes (or 404 once the superseded object is removed) — never a
    later run's bytes. Stays inside the ``jobs/{ws}/{job}/`` read-side prefix
    (``job_artifact_row_prefix``); the ``.gz`` form marker (#338) is appended
    by the caller."""
    return f"{KEY_PREFIX}/{workspace_id}/{job_id}/{VERSION_SEGMENT}/{version}/{name}"


def discard_superseded_objects(object_store: Any, keys: list[str], job_id: str) -> None:
    """登记提交后删除被取代对象；永不抛出（调用方的登记已提交成功）。

    走 ``delete_objects_guarded``（promote 同款 ``artifact-authority`` 锁 +
    锁内清单复核）：仍被本 job 任何清单行引用的 key（#853 前跨节点同名产物
    共用的 legacy key）跳过，锁被在途 promote 持有时跳过（保守方向：孤儿
    交 GC）。"""
    if not keys or object_store is None:
        return
    guarded = getattr(object_store, "delete_objects_guarded", None)
    if guarded is None:
        return
    try:
        guarded([{"storage_key": key} for key in keys], job_id)
    except Exception:
        # #204 broad-except audit: post-commit best-effort cleanup — the
        # manifest registration that made these keys superseded has already
        # COMMITTED, so a failure here (the lock transaction's psycopg
        # surface; the storage SDK/network surface is already contained per
        # object inside delete_objects_guarded) must not fail the upload/
        # promote that succeeded. The residue is an unreferenced object that
        # still holds the OLD bytes (a pinned URL never sees new bytes); the
        # S3 jobs GC and bucket lifecycle reap it. logger.exception keeps the
        # traceback with the job id.
        logger.exception("superseded artifact object cleanup failed for job %s", job_id)
