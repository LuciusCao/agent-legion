"""Worker 结果落盘面的路径形态预检（#759 对抗复审 P2 族）。

validate-then-apply 的形状半边：任何字节移动之前（remote promote、读视
图链接、finish 闸内文件提升），先核算全部计划的 job_dir 落点——归档
暂存提升、Worker 直传 ref 的下载落盘、events.jsonl——做两条纯路径判
定（只 stat、不改现场）：

1. 跨通道前缀互斥：没有任何落点名是另一落点名的真祖先。「reports 是
   文件、reports/out.json 也是文件」在单文件系统上不可能同时成立——两
   个通道各自宣称这种形状时，继续 apply 只会在中途炸开（视图链接
   unlink 到目录、闸内 mkdir 撞上文件），或把先落盘的 remote 文件随目
   录备份一起静默删掉；且此时 authority copy、清单行、staging key 清
   理等其他面已经提交。预检失败 = 整个结果干净 failed，零字节应用。
2. 祖先畅通：每个落点的祖先链上不存在「已是非目录」的现场文件（前代
   次残留、输入水合或本地执行的污染）。闸内 promote 的
   mkdir(parents=True) 撞上会整批回滚再上抛，把 lease 毒化成重试循环；
   预检把它提前成一次干净 failed（闸内仍留兜底转换，见
   executors._lease_lifecycle——预检无锁，盖不住跨节点 finish 之间现
   场变坏的残余竞态）。
3. 保留源保护：logs 树 move（node.log）的 **source** 与 remote 落点共
   用同一个 staging 命名空间，而它的 target 不在 job_dir 内、不进前两条
   的落点集。overwrite 遍的 spot/blocker 清理会删掉与它同位（或位于它
   之下）的视图条目——落点名等于保留源名（spot unlink）或以保留源名
   为真前缀（blocker unlink）都会把 log source 抹掉，闸内 promote 随之
   FileNotFoundError（#759 对抗复审 P2-B）。归档落点与保留源同名同时
   判死：那是「同 source 双 move」病态声明（expected 输出名撞
   CODE_RESULT_LOG_MEMBER），闸内必炸，提前成干净 failed。

判失败时调用方仍把「闸安全的」归档 moves（``gate_safe_staged_moves``
过滤后的日志类条目）挂上失败 finish：node.log / events.jsonl 照常落
盘（#759 review P3 的可观测性 parity），被挡的输出 moves 不挂——它
们正是会在闸内炸开的那批。
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


def find_landing_conflict(
    *,
    job_dir: Path,
    view_dir: Path,
    staged_moves: list[tuple[Path, Path]],
    remote_landing_names: tuple[str, ...],
) -> str | None:
    """核算计划的落点与保留源，返回冲突描述；None = 形状兼容，可以 apply。

    ``staged_moves`` 是 ``plan_agent_result_moves`` 规划的 (target, source)
    绝对路径对——target 在 job_dir 树内的计入落点集（前缀互斥 + 祖先畅
    通），target 越界的（node.log 落 logs 树）取 source 相对 ``view_dir``
    （staging 根）的名计入保留源集；``remote_landing_names`` 是本会下载
    落盘的 dict-ref 名（调用方已按 cancelled / expected 过滤）。所有名只
    做路径数学，不信任的 Worker 名（``..``、非规范形）由 apply 阶段另行
    拒绝，这里撞上它们不会做任何文件系统写。
    """
    landings: list[tuple[PurePosixPath, str]] = []
    protected_sources: list[PurePosixPath] = []
    for target, source in staged_moves:
        relative = _relative_or_none(target, job_dir)
        if relative is not None:
            landings.append((relative, "archive"))
            continue
        protected = _relative_or_none(source, view_dir)
        if protected is not None:
            protected_sources.append(protected)
    landings.extend((PurePosixPath(name), "remote ref") for name in remote_landing_names)
    for index, (name, channel) in enumerate(landings):
        for other, other_channel in landings[index + 1 :]:
            if _is_proper_prefix(name, other) or _is_proper_prefix(other, name):
                return (
                    f"conflicting output paths: {name.as_posix()!r} ({channel}) vs "
                    f"{other.as_posix()!r} ({other_channel})"
                )
        for protected in protected_sources:
            if name == protected or _is_proper_prefix(protected, name):
                return (
                    f"output path {name.as_posix()!r} ({channel}) conflicts with "
                    f"the reserved result member {protected.as_posix()!r}"
                )
    for name, channel in landings:
        blocker = blocking_ancestor(job_dir / name)
        if blocker is not None:
            return (
                f"output path {name.as_posix()!r} ({channel}) is blocked by "
                f"existing non-directory {blocker}"
            )
    return None


def gate_safe_staged_moves(
    staged_moves: list[tuple[Path, Path]],
) -> list[tuple[Path, Path]]:
    """过滤出闸内提升不会撞祖先的 moves（失败 finish 的日志 parity 用）。"""
    return [move for move in staged_moves if blocking_ancestor(move[0]) is None]


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


def _relative_or_none(path: Path, base: Path) -> PurePosixPath | None:
    try:
        return PurePosixPath(path.relative_to(base).as_posix())
    except ValueError:
        return None


def _is_proper_prefix(name: PurePosixPath, other: PurePosixPath) -> bool:
    return len(name.parts) < len(other.parts) and other.parts[: len(name.parts)] == name.parts
