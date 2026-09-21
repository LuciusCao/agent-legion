"""Worker 结果读视图的链接机制（#759 review P1-1、对抗复审 P2 族）。

自 ``completion_staged`` 拆出的文件预算姊妹模块：staging 目录即读视图，
归档成员不可信，而视图只是 finish 前的私有 scratch——链接对垃圾形状
（同名目录、文件祖先、symlink）全域，overwrite 遍清挡位垃圾，第一遍
遇挡位跳过（按未产出判 missing），源消失的 TOCTOU 同样按未产出跳过，
任何形状/竞态组合都不炸异常（炸穿结果提交在 overwrite 遍命中时就是
codex #774 P2 的同型现场：remote promote 已提交、staging key 已删）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def link_into_view(
    names: tuple[str, ...] | Any, job_dir: Path, view_dir: Path, *, overwrite: bool = False
) -> None:
    """把 job_dir 文件链进读视图。

    overwrite 遍清掉挡位的垃圾——spot 上的目录整棵删（预检的前缀互斥已
    保证目录内没有任何暂存源：有则那个 expected 名与 remote 落点撞前缀，
    结果在进此函数之前已被判 failed）、祖先链上的垃圾文件直接 unlink
    （文件祖先之下不可能存在暂存源——同一 staging 目录里「reports 是文
    件」与「reports/x 是文件」物理互斥）。不带 overwrite 的第一遍遇挡位
    一律跳过：该名按未产出判 missing（归档形状与 expected 声明自相矛盾，
    继承 job_dir 残留没有依据）。
    """
    for name in names:
        landed = job_dir / name
        view_spot = view_dir / name
        if not landed.is_file():
            continue
        if view_spot.is_symlink() or view_spot.is_file():
            if not overwrite:
                continue
            view_spot.unlink()
        elif view_spot.is_dir():
            if not overwrite:
                continue
            shutil.rmtree(view_spot)
        elif os.path.lexists(view_spot):
            # 其他特殊条目（防御：解包实际只产文件/目录）。
            if not overwrite:
                continue
            view_spot.unlink()
        blocker = _view_blocker(view_dir, view_spot)
        if blocker is not None:
            if not overwrite:
                continue
            blocker.unlink()
        view_spot.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(landed, view_spot)
        except OSError:
            try:
                # 不支持硬链接的挂载（P3 部署边缘）：退化为同内容拷贝，读视
                # 图语义不变——视图只用于 finish 前的读取，随后整个 staging
                # 目录被清理。
                shutil.copy2(landed, view_spot)
            except OSError:
                # 源侧 TOCTOU（#759 对抗复审 P2-A）：landed 在 is_file→link
                # 窗口内被并发 promote 的备份步 rename 走（同 job 跨节点同
                # 路径输出的病态声明），或盘满/权限等操作性故障——按「该名
                # 未产出」跳过，produced 判定兜底成 missing，与第一遍遇挡
                # 位跳过同一语义。
                continue


def _view_blocker(view_dir: Path, view_spot: Path) -> Path | None:
    """view_spot 在视图内的祖先链上第一个已存在的非目录（挡 mkdir 的垃圾
    文件）；lexists 判破 symlink，走到 view_dir 为止。"""
    parent = view_spot.parent
    while parent != view_dir and parent != parent.parent:
        if os.path.lexists(parent):
            return None if parent.is_dir() else parent
        parent = parent.parent
    return None
