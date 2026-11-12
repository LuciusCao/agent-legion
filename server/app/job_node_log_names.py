"""``<logs_dir>/jobs`` 下节点日志的命名（#1113）。

从 ``storage_paths`` 拆出（文件预算）；``storage_paths`` 原样再导出，写入方
（claim_submit / shards）仍经 ``storage_paths.job_node_log_name`` 取名。
"""

import hashlib

from server.app.jobs.storage_layout import job_shard

# #1113：节点日志按 job 分目录的根；#1340 起为
# ``<logs_dir>/jobs/by-job/<shard>/<job_id>/``。
# 旧扁平名一律以 ``.log`` 结尾，此目录名不可能与任何旧日志文件同名，新旧
# 命名空间物理上不相交。
JOB_NODE_LOG_ROOT = "by-job"


# 文件名字节上限（Linux / macOS 常见 NAME_MAX）。
_NAME_MAX_BYTES = 255


def _escape_node_log_char(char: str) -> str:
    # 只转义会破坏「单一文件名分量 + 后缀可分」的字符：``%``（转义符自身）、
    # ``/``（路径分隔）、``.``（与 ``.shard-<i>`` / ``.log`` 后缀及 ``..`` 分量
    # 区分）与 ASCII 控制字符（含 NUL）。其余字符（含非 ASCII）原样保留——
    # 百分号编码会把每个 UTF-8 字节膨胀成 3 字节，中文 key 很快超过 NAME_MAX
    # （#1215 codex P1）。
    # 被转义者都是单字节 ASCII（quote 不转义 ``.``，故手写）。
    if char in "%/." or ord(char) < 0x20 or char == "\x7f":
        return f"%{ord(char):02X}"
    return char


def _encode_node_log_key(node_key: str) -> str:
    """node_key 的可逆文件名编码：仅把 ``%`` ``/`` ``.`` 与控制字符写成
    ``%XX``——结果不含 ``/`` 与 ``.``，故不会产生子目录、``.`` / ``..`` 分量，
    ``.shard-<i>`` 后缀也不可能出现在 key 部分；字面 ``%`` 恒被转义，
    ``urllib.parse.unquote`` 即可还原。"""
    return "".join(_escape_node_log_char(char) for char in node_key)


def _bounded_node_log_stem(node_key: str, suffix: str) -> str:
    """编码 stem；整个文件名（stem + suffix + ``.log``）超出 NAME_MAX 时改用
    有界形态 ``<编码前缀>.<sha256(node_key)>``。

    单射性：常规 stem 不含 ``.``，有界 stem 恰含一个 ``.`` 且其后是 64 位十六
    进制摘要——两种形态互不相等，有界形态之间由摘要区分。前缀按字符截断后
    再编码，不会切断 ``%XX`` 或多字节字符。
    """
    stem = _encode_node_log_key(node_key)
    fixed = len(suffix.encode()) + len(".log")
    if len(stem.encode()) + fixed <= _NAME_MAX_BYTES:
        return stem
    digest = hashlib.sha256(node_key.encode("utf-8", "surrogatepass")).hexdigest()
    budget = _NAME_MAX_BYTES - fixed - len(digest) - 1
    prefix = ""
    for char in node_key:
        piece = _escape_node_log_char(char)
        if len(prefix.encode()) + len(piece.encode()) > budget:
            break
        prefix += piece
    return f"{prefix}.{digest}"


def job_node_log_dir_name(job_id: str) -> str:
    """#1340 新布局 ``by-job/<shard>/<job_id>``（POSIX 相对路径）。

    shard 与 job 产物目录共用 ``job_shard``。job_id = ``<ws>_<wf>_<source_id>``
    （source_id 的 ``/`` 已换成 ``_``），不含 ``/``、恒带前缀，不会是 ``.`` /
    ``..``；不满足即违反不变式，直接拒绝。
    """
    if not job_id or "/" in job_id or "\0" in job_id or job_id in {".", ".."}:
        raise ValueError(f"job id cannot name a log directory: {job_id!r}")
    return f"{JOB_NODE_LOG_ROOT}/{job_shard(job_id)}/{job_id}"


def job_node_log_dir_names(job_id: str) -> tuple[str, str]:
    """返回新分片目录与 #1113 旧 ``by-job/<job_id>`` 目录，供删除侧兼容。"""
    return job_node_log_dir_name(job_id), f"{JOB_NODE_LOG_ROOT}/{job_id}"


def job_node_log_name(job_id: str, node_key: str, shard_index: int | None = None) -> str:
    """``<logs_dir>/jobs`` 下节点日志的唯一命名规则（POSIX 相对路径）：
    普通节点 ``by-job/<shard>/<job_id>/<enc(node_key)>.log``，分片
    ``by-job/<shard>/<job_id>/<enc(node_key)>.shard-<i>.log``。

    job_id 独占目录、node_key 经 ``_encode_node_log_key`` 编码（不含 ``.``；
    文件名超 NAME_MAX 时改为有界的 ``<前缀>.<sha256>`` 形态，见
    ``_bounded_node_log_stem``），故 (job_id, node_key, shard_index) → 路径是单射：旧扁平名
    ``<job_id>-<node_key>[-shard-<i>].log`` 在 job_id 与 node_key 都可含连字符时
    跨 job 碰撞的问题不再存在。写入方（claim_submit / shards）唯一入口；job
    删除按 ``job_node_log_dir_names`` 清理新旧两个独占目录，旧扁平名见
    ``legacy_job_node_log_name``。
    """
    suffix = "" if shard_index is None else f".shard-{shard_index}"
    stem = _bounded_node_log_stem(node_key, suffix)
    return f"{job_node_log_dir_name(job_id)}/{stem}{suffix}.log"


def legacy_job_node_log_name(job_id: str, node_key: str, shard_index: int | None = None) -> str:
    """#1113 之前的扁平命名 ``<job_id>-<node_key>[-shard-<i>].log``。

    只供 job 删除识别升级前写下的存量日志（读取侧按 node_runs.log_path 原样
    读取，不需要它）；新写入一律走 ``job_node_log_name``。
    """
    if shard_index is None:
        return f"{job_id}-{node_key}.log"
    return f"{job_id}-{node_key}-shard-{shard_index}.log"
