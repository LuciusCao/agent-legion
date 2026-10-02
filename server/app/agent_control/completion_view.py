"""Worker 结果读视图的链接机制（#759 review P1-1、对抗复审 P2 族）。

自 ``completion_staged`` 拆出的文件预算姊妹模块：staging 目录即读视图，
归档成员不可信，而视图只是 finish 前的私有 scratch。链入视图的只有两
类名：「本次 ref 校验提升」的产物（#779 终审 P1：job_dir 残留永不进视
图）与节点声明 inputs（#828/#830，名单与字节来源裁决在姊妹模块
``completion_view_inputs``）。链接一律覆盖——同名归档暂存字节让位可
信字节（#759 对抗复审 N2），挡位垃圾（同名目录、文件祖先、symlink）
全域清理，源消失的 TOCTOU 按未产出跳过，任何形状/竞态组合都不炸异常
（炸穿结果提交在覆盖链接命中时就是 codex #774 P2 的同型现场：remote
promote 已提交、staging key 已删）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def link_into_view(names: tuple[str, ...] | Any, job_dir: Path, view_dir: Path) -> None:
    """把 job_dir 文件链进读视图（一律覆盖，语义见 ``link_source_into_view``）。"""
    for name in names:
        link_source_into_view(job_dir / name, name, view_dir)


def link_source_into_view(source: Path, name: str, view_dir: Path) -> None:
    """把 ``source`` 的字节以 ``name`` 链进读视图（一律覆盖）。

    覆盖语义保证可信字节胜过归档在同名位置暂存的字节。挡位垃圾全域清
    理：同名目录整棵删（预检的前缀互斥已保证目录内没有任何暂存源：有
    则那个 expected 名与 remote 落点撞前缀，结果在进此函数之前已被判
    failed）、祖先链上的垃圾文件直接 unlink（文件祖先之下不可能存在暂
    存源——同一 staging 目录里「reports 是文件」与「reports/x 是文件」
    物理互斥）、symlink 走 unlink（防御：解包禁止链接成员）。源不是文
    件（含在 is_file→link 窗口内消失——并发 promote 的备份步 rename，
    #759 对抗复审 P2-A）按「该名未产出」跳过，produced 判 missing 兜底。
    """
    view_spot = view_dir / name
    if not source.is_file():
        return
    if view_spot.is_symlink() or view_spot.is_file():
        view_spot.unlink()
    elif view_spot.is_dir():
        shutil.rmtree(view_spot)
    elif os.path.lexists(view_spot):
        # 其他特殊条目（防御：解包实际只产文件/目录）。
        view_spot.unlink()
    blocker = _view_blocker(view_dir, view_spot)
    if blocker is not None:
        blocker.unlink()
    view_spot.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, view_spot)
    except OSError:
        try:
            # 不支持硬链接的挂载（P3 部署边缘）：退化为同内容拷贝，读视
            # 图语义不变——视图只用于 finish 前的读取，随后整个 staging
            # 目录被清理。
            shutil.copy2(source, view_spot)
        except OSError:
            # 源侧 TOCTOU 或盘满/权限等操作性故障——按「该名未产出」跳过，
            # produced 判定兜底成 missing。
            return


def _view_blocker(view_dir: Path, view_spot: Path) -> Path | None:
    """view_spot 在视图内的祖先链上第一个已存在的非目录（挡 mkdir 的垃圾
    文件）；lexists 判破 symlink，走到 view_dir 为止。"""
    parent = view_spot.parent
    while parent != view_dir and parent != parent.parent:
        if os.path.lexists(parent):
            return None if parent.is_dir() else parent
        parent = parent.parent
    return None
