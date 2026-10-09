"""Cross-process contract constants for code-node execution (#282).

Separated from ``shared/code_sandbox`` (argv/env plumbing) so that the
contract surface — member names, the result-metadata key set — reads as its
own artifact. Still stdlib-only: the worker image ships shared/ wholesale
without a repo checkout.

Lives here rather than only in code_sandbox because these constants are the
Worker↔Host boundary itself: the Worker writes exactly these members/keys
(``worker/code_runner.py``, ``worker/upload/result_manifest.py`` for the v2
``result.json`` member, #843), the Host reads them
(``server/app/agent_broker/result_unpack.py`` for members,
``server/app/routes/agent_worker_results.py`` for metadata keys, #843 v2
also ``server/app/agent_broker/result_metadata_reader.py`` for the
``result.json`` member), and the guard tests in
``tests/workers/test_protocol_sync.py`` pin both sides to this single
definition.
"""

from __future__ import annotations

# Code bundle member names (batch 2 contract, shared by the Host-side packer
# server/app/agent_broker/agent_bundle.py and the Worker-side runner).
CODE_BUNDLE_NODE_FILE = "node_code.py"
CODE_BUNDLE_LIBS_DIR = "workspace_libs"
# Result-archive member carrying the node's captured stdout/stderr for
# kind='code' results (batch 2 decision 10); the Host promotes it to the
# run's canonical log path.
CODE_RESULT_LOG_MEMBER = "node.log"
# 结果归档里携带直传产物清单的保留成员名（#755 codex P1）：结果头字节预算
# 装不下直传 dict ref 清单时，Worker 把完整 {"name": ref} 映射写成该成员
# （首成员），头里只留 output_artifacts_in_archive 标记；产物字节已在 S3
# （presigned 通道），不重复传输。#843 PR-2 起 Worker 写侧切 v2 后不再产生
# 该成员（清单整体留在 result.json）；Host 读侧保留 v1 旧 Worker 兼容窗
# （server/app/agent_broker/result_output_manifest.py）；该成员永不进
# expected outputs 提升面。
RESULT_OUTPUT_ARTIFACTS_MEMBER = "result-output-artifacts.json"
# 与清单成员配套的头部布尔标记键（同 #755 codex P1 协议）：#843 PR-2 起
# Worker 不再写它（v2 契约明文禁止 v2 payload 携带本标记）；Host 读侧保留
# v1 旧 Worker 兼容窗，且 v2 形态下显式忽略（agent_worker_result_shapes 的
# read_member 在 parse 前剥离）。单一事实来源在此，两侧字面量漂移即断。
RESULT_OUTPUT_ARTIFACTS_FLAG = "output_artifacts_in_archive"
# #843 结果元数据 v2 双形态：头里的 v1 JSON 整体迁入结果归档的保留成员
# ``result.json``（UTF-8 JSON 文本，Worker 写侧在产物清单终态后由
# worker/upload/result_manifest.py 写成首成员——含完整 output_artifacts），
# 请求头改为固定 ASCII 引导值 ``X-Agent-Result-Format: 2``。头名与值在
# 此单一事实来源（PR-1 Host 读侧、PR-2 Worker 写侧同用）；16KiB 头上限
# 与 #748/#755 的降级链随 Worker 写侧切换退役——v2 无头预算。成员名与
# RESULT_OUTPUT_ARTIFACTS_MEMBER 同属归档保留成员命名空间。
RESULT_METADATA_FORMAT_HEADER = "X-Agent-Result-Format"
RESULT_METADATA_FORMAT_V2 = "2"
RESULT_METADATA_MEMBER = "result.json"
# ``agent_workers.max_archive_bytes`` 的合法下限（#1082）：1 KiB 才能容纳
# 空 tar.gz（判败降级路径的 metadata-only 归档），Host 配置模型
# （server/app/configuration/executor_runtime.py）与实例设置 PUT 契约
# （server/app/routes/instance_settings_contracts.py）共用本单一来源。
MIN_RESULT_ARCHIVE_BYTES = 1024
# 结果元数据 ``command`` 面的段数上限（#822）。command 是纯观测面（Host 只
# 记录、不参与完成判定），但 agent argv 会把每个 expected output 以
# ``--require-output <name>`` 重复进去，产物一多段数即线性膨胀。两侧同一
# 语义——超限截断保前缀、不拒收：#843 PR-2 起 Worker 写侧不再主动收缩
# （v1 头序列化器已退役，result.json 无段数预算），Host 解析
# （agent_worker_results.py 的 ``parse_result_metadata``）防御性截断两形态
# 同享。旧版 Host 对超限直接 400 的死循环历史见 v1 兼容窗说明。
MAX_RESULT_COMMAND_PARTS = 64
# Mirrors workspace_libs/node_sdk.py NODE_RUNTIME_DIR / AUTH_FAILURE_MARKER.
# node_sdk must stay import-self-contained (the code bundle ships only the
# workspace_libs snapshot), so that mirror keeps a comment pointer instead of
# importing this module. Equality with the node_sdk side is pinned by
# tests/workers/test_protocol_sync.py (issue #282).
AUTH_FAILURE_MARKER_PATH = ".node_runtime/auth_failure"
# Connection keys reported by node code via report_auth_failure; bounded on
# both sides (Host route agent_worker_results, Worker result metadata).
MAX_CONNECTION_KEY_CHARS = 128
# Keys of the kind='code' result-metadata dict (issue #282): the Worker's
# ``prepare_code_result`` (worker/code_runner.py) writes exactly these —
# ``auth_failure_connection`` only when the node actually reported one — and
# the Host reads them in ``parse_result_metadata``
# (server/app/routes/agent_worker_results.py) via ``.get`` with defaults, so
# an absent optional key is not an error. A process-boundary contract with no
# compiler and no schema; before #282 both sides were handwritten literals
# kept in sync by comment only. ``run_dir`` is deliberately NOT part of this
# set: it is agent-path-only and code results never carry it. Shard outputs
# (#389) also never ride this channel — they ship as regular archive members
# (``shard_output-<index>.json`` expected outputs), avoiding the header-size
# ceiling entirely.
CODE_RESULT_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "status",
        "exit_code",
        "error_message",
        "command",
        "output_artifacts",
        "auth_failure_connection",
    }
)
