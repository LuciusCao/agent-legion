"""失败 finish 的闸安全 staged move 过滤（#759 对抗复审 P2 族）。

自 ``completion_preflight`` 拆出（文件体积预算）：预检模块只回答「形状
是否兼容」，本模块回答「冲突/失败收尾时哪些 move 可以安全挂上失败
finish」——祖先挡位探测（``blocking_ancestor``）与冲突摘除
（``gate_safe_staged_moves``）。
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


def gate_safe_staged_moves(
    staged_moves: list[tuple[Path, Path]],
    *,
    job_dir: Path | None = None,
    excluding: frozenset[PurePosixPath] = frozenset(),
) -> list[tuple[Path, Path]]:
    """过滤出闸内提升安全、且未参与预检冲突的 moves（失败 finish 的日志
    parity 用）。

    两层过滤：祖先被非目录挡住的 moves 不挂（闸内必炸的那批）；落点名在
    ``excluding``（``LandingConflict.names``）里的 moves 也不挂——它们
    正是预检判死的形状。保留冲突 move 会让失败 finish 的闸内提升把冲突
    输出照样写进 job_dir（失败结果污染现场），共享同一 staging source 的
    观测 move（node.log 双 move）还会因源已被先消耗而被误当事务重放跳
    过——日志反成旧内容（codex #774 P2）。target 不在 job_dir 内的观测
    moves（node.log 落 logs 树）无落点名，不参与 ``excluding`` 判定。
    """
    safe = []
    for move in staged_moves:
        if job_dir is not None and relative_or_none(move[0], job_dir) in excluding:
            continue
        if blocking_ancestor(move[0]) is not None:
            continue
        safe.append(move)
    return safe


def blocking_ancestor(target: Path) -> Path | None:
    """target 的祖先链上第一个已存在的条目是非目录时返回它。

    mkdir(parents=True, exist_ok=True) 恰恰只在这种条目上炸开；向上走
    到第一个已存在条目即停——它是目录则其上全是目录。lexists：破 symlink
    同样挡住 mkdir（exists 会漏判）。
    """
    parent = target.parent
    while parent != parent.parent:
        if os.path.lexists(parent):
            return None if parent.is_dir() else parent
        parent = parent.parent
    return None


def relative_or_none(path: Path, base: Path) -> PurePosixPath | None:
    """path 相对 base 的规范名；越界（不在 base 树下）返回 None。"""
    try:
        return PurePosixPath(path.relative_to(base).as_posix())
    except ValueError:
        return None
