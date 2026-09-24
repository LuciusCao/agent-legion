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
盘（#759 review P3 的可观测性 parity）；被挡的输出 moves 与**参与冲
突的落点 moves**（``LandingConflict.names``）不挂——前者会在闸内炸
开，后者会让失败结果污染 job_dir、并把共享同一 staging source 的观
测 move 误当重放跳过（codex #774 P2）。过滤器本体在姊妹模块
``completion_moves``（体积预算拆分）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from server.app.agent_control.completion_moves import (
    blocking_ancestor,
)
from server.app.agent_control.completion_moves import (
    relative_or_none as _relative_or_none,
)


@dataclass(frozen=True)
class LandingConflict:
    """落点冲突：人读描述（第一处目击）+ 全部冲突的 job_dir 相对落点名集。

    ``names`` 供失败 finish 过滤 staged moves：参与冲突的落点 move 不挂
    （它们正是预检判死的形状），未参与的观测 move（node.log 等）照常随
    失败 finish 落盘。names 必须是全集——只带第一对会让兄弟落点/第二
    对冲突漏摘（#774 对抗复审 P2）。"""

    message: str
    names: frozenset[PurePosixPath]

    def full_message(self) -> str:
        """第一处目击 + 全部冲突落点名（operator 诊断面：只带第一处时无法
        解释其余被摘除落点的去向，#774 对抗复审）。"""
        return (
            f"{self.message}; conflicting outputs: "
            f"{', '.join(sorted(name.as_posix() for name in self.names))}"
        )


def find_landing_conflict(
    *,
    job_dir: Path,
    view_dir: Path,
    staged_moves: list[tuple[Path, Path]],
    remote_landing_names: tuple[str, ...],
) -> LandingConflict | None:
    """核算计划的落点与保留源，返回冲突；None = 形状兼容，可以 apply。

    ``staged_moves`` 是 ``plan_agent_result_moves`` 规划的 (target, source)
    绝对路径对——target 在 job_dir 树内的计入落点集（前缀互斥 + 祖先畅
    通），target 越界的（node.log 落 logs 树）取 source 相对 ``view_dir``
    （staging 根）的名计入保留源集；``remote_landing_names`` 是本会下载
    落盘的 dict-ref 名（调用方已按 cancelled / expected 过滤）。所有名只
    做路径数学，不信任的 Worker 名（``..``、非规范形）由 apply 阶段另行
    拒绝，这里撞上它们不会做任何文件系统写。

    不短路、收集**全部**冲突：``names`` 被调用方当作失败 finish 的摘除
    集消费，只带回第一对会让同前缀兄弟落点与第二对冲突的 moves 漏摘—
    —它们照样随失败 finish 落盘污染 job_dir，或在闸内炸开连带观测
    moves 被整体回滚（node.log 静默丢失，#774 对抗复审 P2）。message
    保留第一处目击（人读定位起点），names 是全集。
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
    first_message: str | None = None
    names: set[PurePosixPath] = set()
    for index, (name, channel) in enumerate(landings):
        for other, other_channel in landings[index + 1 :]:
            if _is_proper_prefix(name, other) or _is_proper_prefix(other, name):
                if first_message is None:
                    first_message = (
                        f"conflicting output paths: {name.as_posix()!r} ({channel}) vs "
                        f"{other.as_posix()!r} ({other_channel})"
                    )
                names.update({name, other})
        for protected in protected_sources:
            if name == protected or _is_proper_prefix(protected, name):
                if first_message is None:
                    first_message = (
                        f"output path {name.as_posix()!r} ({channel}) conflicts with "
                        f"the reserved result member {protected.as_posix()!r}"
                    )
                names.add(name)
    for name, channel in landings:
        blocker = blocking_ancestor(job_dir / name)
        if blocker is not None:
            if first_message is None:
                first_message = (
                    f"output path {name.as_posix()!r} ({channel}) is blocked by "
                    f"existing non-directory {blocker}"
                )
            names.add(name)
    if first_message is None:
        return None
    return LandingConflict(message=first_message, names=frozenset(names))


def _is_proper_prefix(name: PurePosixPath, other: PurePosixPath) -> bool:
    return len(name.parts) < len(other.parts) and other.parts[: len(name.parts)] == name.parts
