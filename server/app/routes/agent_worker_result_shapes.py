"""#843 PR-1：结果元数据双形态读编排（Host 读侧，v1 零行为变化）。

result 路由（``agent_workers.py``）按请求头把元数据读取分派到两形态：

- **v1（现行，零行为变化）**：无 format 标记或值非 ``2`` → ``X-Agent-Result``
  头携带 raw UTF-8 JSON 字节（Starlette latin-1 视图 →
  ``_recover_result_header`` 反解）。解析校验发生在尺寸门 / 预检 / spool
  **之前**——400 先行的既有次序是线上契约，原样保留。
- **v2（#843，本 PR 只做 Host 读侧，Worker 写侧随 PR-2）**：头改为固定
  ASCII 引导值 ``X-Agent-Result-Format: 2``（常量见
  shared/code_contract.py，Worker/Host 单一事实来源），元数据整体落结果
  归档的保留成员 ``result.json``（读回面在
  agent_broker/result_metadata_reader.py）。spool 完成后从 staging 文件
  读回（阻塞 tar 扫描走 threadpool），走**同一个** ``parse_result_metadata``
  校验链——stderr tail 4000 字符、产物清单 128 条、command 段数等防御性
  截断两形态同享。v2 请求若仍出现 ``X-Agent-Result`` 头一律忽略：权威在
  归档成员，头预算（h11 16KiB）不复存在。

失败语义：v2 成员缺失 / 非 JSON / 字段非法 → ValueError → 路由 400，与
v1 头非法 JSON 的 ValueError→400 语义对齐（v2 的 detail 带原因，可定位
到归档侧）。``result-output-artifacts.json`` 清单通道（#755 换轨标记语义）
与本通道互相独立、互不干扰。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from starlette import concurrency

from server.app.agent_broker.result_metadata_reader import read_archived_result_metadata
from server.app.agent_control.completion import AgentOutcome
from server.app.routes.agent_worker_results import (
    _recover_result_header,
    parse_result_metadata,
)
from shared.code_contract import RESULT_METADATA_FORMAT_HEADER, RESULT_METADATA_FORMAT_V2

# v1 头路径的 400 详情逐字保留（零行为变化纪律；Worker 侧对 4xx 终态的
# 处理按 status 而非 detail，但 detail 变化仍属可观测面）。
_V1_INVALID_DETAIL = "invalid Agent result metadata"


def header_is_v2(request: Request) -> bool:
    """v2 引导标记判定：``X-Agent-Result-Format`` 头值精确等于 ``2``。

    头名/值取自 shared/code_contract 的单一事实来源（PR-2 的 Worker 写侧
    同用；Starlette 头表大小写不敏感，常量名以规范大小写参与查询）。其他
    任何值（含缺席、非数字）一律按 v1 走——旧 Worker 不发该头，行为不变。"""
    return request.headers.get(RESULT_METADATA_FORMAT_HEADER) == RESULT_METADATA_FORMAT_V2


def prespool_metadata(request: Request) -> tuple[bool, AgentOutcome | None, dict[str, Any]]:
    """spool 之前的元数据面：v1 现场解析（400 先行次序），v2 只回占位。

    v2 返回 ``(True, None, {"status": None})``：outcome 在归档成员读回前
    不存在；占位 record 仅供 precheck 的审计读取
    （lease_reclaim_audit.reject_result 只 ``.get`` status / exit_code /
    output_artifacts 面，缺席即 None，与 v1 解析 400 先于 precheck 的既有
    形态同容）。"""
    if header_is_v2(request):
        return True, None, {"status": None}
    # #748 P2: the Worker ships the metadata as raw UTF-8 header bytes;
    # Starlette hands it over latin-1-decoded, so reverse the transport
    # decoding before parsing (no-op for legacy ASCII).
    try:
        outcome, record = parse_result_metadata(
            _recover_result_header(request.headers.get("x-agent-result", "{}"))
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_V1_INVALID_DETAIL) from exc
    return False, outcome, record


async def read_member(staged: Path) -> tuple[AgentOutcome, dict[str, Any]]:
    """v2：从 spool 后的归档读回 ``result.json`` 并走同一校验链。

    归档读回（阻塞 tar 顺序扫描）在 threadpool 执行——路由的事件循环不能
    被扫描占住（与 spool / commit 的下沉同一纪律）。ValueError（成员缺失 /
    非 JSON / 元数据字段非法）转换成 400，detail 带原因——与 v1 的固定
    detail 区分，排障可直接定位归档侧。归档解包自身的既有错误路径
    （completion 侧 AgentBundleError → 诚实判败）不经此函数，不变。"""
    try:
        raw = await concurrency.run_in_threadpool(read_archived_result_metadata, staged)
        return parse_result_metadata(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{_V1_INVALID_DETAIL}: {exc}") from exc
