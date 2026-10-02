"""Worker 结果读视图的链接机制（#759 review P1-1、对抗复审 P2 族）。

自 ``completion_staged`` 拆出的文件预算姊妹模块：staging 目录即读视图，
归档成员不可信，而视图只是 finish 前的私有 scratch。链入视图的只有两
类名：「本次 ref 校验提升」的产物（#779 终审 P1：job_dir 残留永不进视
图）与节点声明 inputs（#828/#830：Host 校验的跨文件对账数据面，见
``link_declared_inputs_into_view``）。链接一律覆盖——同名归档暂存字
节让位可信字节（#759 对抗复审 N2），挡位垃圾（同名目录、文件祖先、
symlink）全域清理，源消失的 TOCTOU 按未产出跳过，任何形状/竞态组合都
不炸异常（炸穿结果提交在覆盖链接命中时就是 codex #774 P2 的同型现场：
remote promote 已提交、staging key 已删）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any


def link_into_view(names: tuple[str, ...] | Any, job_dir: Path, view_dir: Path) -> None:
    """把 job_dir 文件链进读视图（一律覆盖）。

    覆盖语义保证 ref 字节胜过归档在同名位置暂存的字节。挡位垃圾全域清
    理：同名目录整棵删（预检的前缀互斥已保证目录内没有任何暂存源：有
    则那个 expected 名与 remote 落点撞前缀，结果在进此函数之前已被判
    failed）、祖先链上的垃圾文件直接 unlink（文件祖先之下不可能存在暂
    存源——同一 staging 目录里「reports 是文件」与「reports/x 是文件」
    物理互斥）、symlink 走 unlink（防御：解包禁止链接成员）。源在
    is_file→link 窗口内消失（并发 promote 的备份步 rename）按「该名未
    产出」跳过，produced 判 missing 兜底。
    """
    for name in names:
        landed = job_dir / name
        view_spot = view_dir / name
        if not landed.is_file():
            continue
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
                # 未产出」跳过，produced 判定兜底成 missing。
                continue


def link_declared_inputs_into_view(
    manifest: dict[str, Any], expected: tuple[str, ...], job_dir: Path, view_dir: Path
) -> None:
    """把节点声明 inputs 从 job_dir 链入校验视图（#828/#830）。

    Host 侧校验的 validator 会做跨文件事实回引对账（读上游产物/intake
    物化文件），staging 化（#759）把校验对象从 job_dir 换成读视图后
    inputs 不在视图内，这类 validator 全量失败。inputs 是上游节点经各
    自校验产出的可信字节，链入不放宽 #779 终审 P1 的安全属性：与
    expected 同名的 input 一律不链（produced 判定仍只认本次上报产物，
    残留永不补齐 expected），不安全名（绝对路径/``..``）跳过。覆盖语义
    让可信 input 字节胜过归档在同名位置暂存的不可信成员——与 staging
    化前「非 expected 归档成员永不可达 validator」同一姿态。job_dir 本
    地缺失（可淘汰缓存）时按缺席跳过，与 staging 化前校验直读 job_dir
    的暴露面一致。
    """
    expected_names = frozenset(expected)
    names = []
    for raw in manifest.get("inputs") or ():
        name = str(raw)
        relative = PurePosixPath(name)
        if name in expected_names or relative.is_absolute() or ".." in relative.parts:
            continue
        names.append(name)
    link_into_view(tuple(names), job_dir, view_dir)


def _view_blocker(view_dir: Path, view_spot: Path) -> Path | None:
    """view_spot 在视图内的祖先链上第一个已存在的非目录（挡 mkdir 的垃圾
    文件）；lexists 判破 symlink，走到 view_dir 为止。"""
    parent = view_spot.parent
    while parent != view_dir and parent != parent.parent:
        if os.path.lexists(parent):
            return None if parent.is_dir() else parent
        parent = parent.parent
    return None
