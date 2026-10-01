"""Manifest-row workspace-prefix authority（#631 攻击复审 H1，拆自
``job_artifact_objects``：文件预算——三个纯函数是读侧兜底的独立主题，
与写侧 key 构造解耦）。读路径（raw 与文本两条）打开对象前据此拒绝
跨 job 前缀的行：损坏/误写的 manifest 行降级 404，而非读穿 workspace
边界。"""

from __future__ import annotations

import logging
from typing import Any

from server.app.services.job_artifact_objects import KEY_PREFIX

logger = logging.getLogger(__name__)


def artifact_key_prefix(workspace_id: str, job_id: str) -> str:
    """Read-side authority prefix ``jobs/{ws}/{job}/``（.gz 变体同前缀）。"""
    return f"{KEY_PREFIX}/{workspace_id}/{job_id}/"


def row_key_in_job_prefix(row: dict[str, Any], job: dict[str, Any]) -> bool:
    """storage_key 是否在本 job 前缀内；job_id 取 job 记录行，不信行自述。"""
    return str(row.get("storage_key") or "").startswith(
        artifact_key_prefix(str(job.get("workspace_id") or ""), str(job.get("id") or ""))
    )


def refuse_row_outside_job_prefix(row: dict[str, Any], job: dict[str, Any]) -> bool:
    """前缀外行统一拒绝口（raw 与文本读路径共用）：不匹配记 warning
    （不含 key 全文，只记长度与 job 标识——key 可能含敏感段落）并返回
    True，调用方按 NotFound/None 处理。"""
    if row_key_in_job_prefix(row, job):
        return False
    prefix = artifact_key_prefix(str(job.get("workspace_id") or ""), str(job.get("id") or ""))
    logger.warning(
        "job %s manifest row for %r has a storage_key outside the job's "
        "workspace prefix (length %d, expected prefix length %d); refusing to open",
        str(job.get("id") or ""),
        str(row.get("name") or ""),
        len(str(row.get("storage_key") or "")),
        len(prefix),
    )
    return True
