"""节点声明 inputs 的校验视图链入（#828/#830 + codex 对抗复审 P1/P2）。

Host 侧校验的 validator 会做跨文件事实回引对账（读上游产物/intake 物
化文件），staging 化（#759）把校验对象从 job_dir 换成读视图后 inputs
不在视图内，这类 validator 全量失败。本模块决定哪些 input 名进视图、
字节取自哪里：

- 名单来自 dispatch 时冻结的 ``manifest["inputs"]``（DB 请求行，Worker
  不可改）；名字先规范化（``PurePosixPath`` 折叠 ``./`` 与 ``//``），不
  安全名（绝对路径/``..``）跳过；
- 与 expected 规范化同名的 input 一律不链——produced/missing/镜像判定
  仍只认本次上报产物，残留永不补齐 expected（#779 终审 P1 在 input 通
  道的复刻；codex P2：非规范拼法 ``./out.json`` 与 ``out.json`` 落同一
  视图位置，原始字符串比较挡不住）；
- 字节来源优先 CAS 冻结副本：dispatch 时 ``stage_agent_inputs`` 把
  Worker 实际消费的 input 字节 put 进内容寻址存储并按 (job, node) 持
  ref（job 存活期间不会被 GC）——校验必须对 Worker 实际看到的字节做，
  job_dir 同名文件可能在 dispatch→completion 之间被并行生产者覆盖
  （codex P1）；无 ref（遗留 manifest、claim 期 presigned dict 形态）或
  CAS blob 缺失时回落 job_dir 链接，即 staging 化前校验直读 job_dir 的
  暴露面；
- 与 reserved（staged move 源：events.jsonl / node.log 等）同名的名不
  链——input 链接永不消耗 finish 代次闸的提升源。

覆盖语义（``link_source_into_view``）让可信 input 字节胜过归档在同名
位置暂存的不可信成员——与 staging 化前「非 expected 归档成员永不可达
validator」同一姿态。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from server.app.agent_control.completion_view import link_source_into_view
from server.app.services.artifact_store import ArtifactNotFoundError, ArtifactStore


def link_declared_inputs_into_view(
    artifact_store: ArtifactStore | None,
    manifest: dict[str, Any],
    expected: tuple[str, ...],
    job_dir: Path,
    view_dir: Path,
    staged_moves: list[tuple[Path, Path]] | tuple[tuple[Path, Path], ...] = (),
) -> None:
    """把节点声明 inputs 链入校验视图（规则见模块 docstring）。

    视图即 job_dir（无归档通道）时无需链接——job_dir 本来就含 inputs。
    reserved 集取 staged_moves 的视图相对源路径（expected 产物与
    events.jsonl / node.log 等观测 move），input 链接永不消耗 finish
    代次闸的提升源。
    """
    if view_dir is job_dir:
        return
    reserved = (
        relative
        for _target, source in staged_moves
        if (relative := _view_relative(view_dir, source)) is not None
    )
    excluded = {
        normalized
        for raw in (*expected, *reserved)
        if (normalized := _normalize_name(str(raw))) is not None
    }
    refs = manifest.get("input_artifacts")
    refs = refs if isinstance(refs, dict) else {}
    for raw in manifest.get("inputs") or ():
        name = _normalize_name(str(raw))
        if name is None or name in excluded:
            continue
        digest = _cas_digest(refs.get(str(raw)))
        if digest is not None and artifact_store is not None:
            try:
                link_source_into_view(artifact_store.open(digest), name, view_dir)
                continue
            except ArtifactNotFoundError:
                # blob 缺失（GC 竞态/陈旧 ref）：回落 job_dir，与遗留
                # manifest 同一暴露面，validator 按既有语义判缺失。
                pass
        link_source_into_view(job_dir / name, name, view_dir)


def _view_relative(view_dir: Path, source: Path) -> str | None:
    """source 的视图相对名；不在视图内（防御：move 源约定上都在）返回 None。"""
    try:
        return source.relative_to(view_dir).as_posix()
    except ValueError:
        return None


def _normalize_name(raw: str) -> str | None:
    """折叠 ``./`` 与 ``//`` 的视图相对名；空/绝对路径/含 ``..`` 返回 None。"""
    if not raw:
        return None
    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return str(relative)


def _cas_digest(ref: Any) -> str | None:
    """``sha256:<digest>`` 形式的 input ref → digest；其他形态（如 claim
    期注入的 presigned dict）返回 None 走 job_dir 回落。"""
    if isinstance(ref, str) and ref.startswith("sha256:"):
        return ref.split(":", 1)[-1]
    return None
