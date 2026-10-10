#!/usr/bin/env python3
"""预暖 per-worktree .uv-cache：克隆基准 worktree 的 uv 缓存（纯 stdlib）。

由 scripts/init-worktree.sh 以系统 python3 调用：

    python3 scripts/uv_cache_prewarm.py "$BASE"

为什么不经 uv 调用本脚本：`uv run --frozen python ...` 在执行脚本前会先
同步整个项目环境——fresh worktree 空 cache 上这就是预暖要消除的冷启动
本身（实测分钟级），且 uv 会就地创建非空 .uv-cache，预暖永远不会生效。
纯 stdlib + 系统 python3（install-deps.sh 本就要求的机器前置）让预暖
发生在第一次 uv 调用之前。预暖是优化路径：一切失败只 warn，退出码恒 0，
init 永不因预暖失败（冷启动是合法路径）。

落位协议（issue #1186，替代原 bash 的 pid 标记临时目录 + mkdir 互斥锁 +
EXIT trap + 死 pid 清扫器整个协议族）：

1. **调用级独立中转目录** ``.uv-cache.prewarm-incoming.<pid>/``：每个
   调用独占自己的中转目录（PR #1188 codex P1）——任何其他进程既不
   rmtree 也不 rename 它，因此不存在「在飞克隆被他人清空后续写、或
   半成品被他人落位」的交错；克隆完整（cp 退出 0）后才竞争落位，
   落位者必然是完整克隆。入口按 pid 后缀判活清扫其他调用者的残留：
   纯数字后缀且 ``kill -0`` 判死（或就是本进程 pid——同进程重入时
   自己的旧中转目录也要清得掉）才清，非数字后缀与活跃 pid 一律保留
   （失败方向永远是保留死重，绝不误删活跃数据）；SIGKILL 残留由此
   覆盖，替代死 pid 清扫器协议。
2. 中转路径清理一律 lstat 区分（PR #1188 codex P2）：symlink/普通文件
   ``unlink``、真实目录才 ``rmtree``——``rmtree(ignore_errors=True)``
   对 symlink 静默保留，会让 cp 穿透写入链接目标（可为任意目录）、
   rename 再把 symlink 本身落位成 .uv-cache。清理后 cp 在缺失路径上
   自建真实目录（不预建：cp -R 对已存在目录是「拷入」语义会产生
   staging/.uv-cache 嵌套）。
3. 克隆：``cp -Rc``（macOS clonefile）/ ``cp -R --reflink=auto``（Linux）
   把基准 cache 克隆进中转目录——写时复制秒级零额外磁盘；不支持的卷上
   cp 内部各自回退普通复制，仍是磁盘速度、远快于网络。uv cache 内容寻址、
   append-only、路径无关，克隆副本可直接使用。
4. 落位：``os.rename(staging, final)``。POSIX rename(2) 对已存在**非空**
   目录原子失败（EEXIST/ENOTEMPTY）——天然 no-replace、无窗口的落位
   语义（git 对象库 link(2)+unlink、npm move-concurrently 同款原语），
   并发落败者丢弃自己的中转目录走跳过路径。注意 rename 对已存在**空
   目录**会成功替换：并发 `uv run` 刚建的空 cache 被完整 cache 整体换
   掉——无害且有益（空 cache 无任何可丢失内容）。
5. ``try/finally`` 只清自己的中转目录（落位成功后 staging 已不存在，
   清理是 no-op），绝不动其他调用者的活跃中转目录。

并发同 worktree 双 init 语义：双方各自完整克隆后竞争 rename，恰一个
落位（完整克隆），落败者丢弃自己的克隆——磁盘浪费有界（一份克隆），
残留由后续调用的入口判活清扫回收；无嵌套、无误删、无部分落位。
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

FINAL_NAME = ".uv-cache"
STAGING_PREFIX = ".uv-cache.prewarm-incoming"


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _remove_path(path: Path) -> None:
    # lstat 区分（codex P2）：symlink/普通文件 unlink，真实目录才 rmtree。
    # 清理失败保留死重（安全方向），不影响主流程。
    try:
        if path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但属其他用户：视为活跃（安全方向）
    return True


def _sweep_staging_leftovers(root: Path) -> None:
    # 首版脚本固定名残留（无 pid 后缀，glob 匹配不到）一并清，lstat 安全。
    _remove_path(root / STAGING_PREFIX)
    self_pid = os.getpid()
    for stale in root.glob(f"{STAGING_PREFIX}.*"):
        suffix = stale.name.removeprefix(f"{STAGING_PREFIX}.")
        # 纯数字后缀且（本进程 pid 重入 或 判死）才清；其余一律保留。
        if suffix.isdigit() and (int(suffix) == self_pid or not _pid_alive(int(suffix))):
            _remove_path(stale)


def prewarm(worktree_root: Path, base: Path) -> str:
    """把 ``base/.uv-cache`` 克隆落位为 ``worktree_root/.uv-cache``。

    返回状态串供测试断言；提示语打印到 stdout/stderr（风格与原 bash
    实现一致：幂等跳过静默，失败/并发落败 warn 到 stderr，成功提示到
    stdout）。调用方（init-worktree.sh）忽略状态——预暖失败不 fail-init。
    """
    final = worktree_root / FINAL_NAME
    staging = worktree_root / f"{STAGING_PREFIX}.{os.getpid()}"
    # 入口清扫：回收其他调用者的死 pid 残留（含 SIGKILL）与本进程重入的
    # 旧中转目录；活跃 pid 的中转目录一律不动。
    _sweep_staging_leftovers(worktree_root)
    try:
        # 幂等重跑：目标已有 cache 静默跳过。
        if final.exists():
            return "skipped-existing"
        base_cache = base / FINAL_NAME
        # symlink 基准解引用：克隆 symlink 会让「独立」cache 实为指向共享
        # 目标的链接（目标随被清理的 worktree 消失时还留悬空链接）；解引用
        # 后克隆实体目录，保住 per-worktree 隔离。解引用失败（如悬空链接）
        # warn 跳过。注意判定顺序：dangling symlink 的 exists() 为 False，
        # 必须先判 is_symlink 再判存在性。
        try:
            source = base_cache.resolve(strict=True) if base_cache.is_symlink() else base_cache
        except OSError:
            _warn(
                "提示: 基准 .uv-cache 为 symlink 且解引用失败，跳过预暖（首次 uv 调用将冷启动拉取依赖）"
            )
            return "skipped-symlink"
        # 基准无 cache（本机第一个 worktree）静默跳过——冷启动是合法路径。
        if not source.is_dir():
            return "skipped-no-base"
        flags = ["-Rc"] if sys.platform == "darwin" else ["-R", "--reflink=auto"]
        # cp 失败（I/O 错误、磁盘满）：中转目录由 finally 清掉，降级冷启动。
        if subprocess.run(["cp", *flags, str(source), str(staging)], check=False).returncode != 0:
            _warn(
                "提示: .uv-cache 预暖克隆失败，已跳过——首次 uv 调用将冷启动拉取依赖（正常路径，仅较慢）"
            )
            return "failed-clone"
        try:
            os.rename(staging, final)
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
                # 并发落败：final 已被兄弟 init / uv 落位，丢弃自己的克隆。
                _warn("提示: .uv-cache 已由并发 init 预暖，丢弃重复克隆")
                return "skipped-concurrent"
            _warn(
                "提示: .uv-cache 预暖落位失败，已跳过——首次 uv 调用将冷启动拉取依赖（正常路径，仅较慢）"
            )
            return "failed-landing"
        print(f"已预暖 .uv-cache <- {base}（后续 uv 调用将命中已缓存依赖）")
        return "prewarmed"
    finally:
        # 只清自己的中转目录——活跃兄弟的目录由判活清扫保护。
        _remove_path(staging)


def main(argv: list[str]) -> int:
    if len(argv) <= 1:
        return 0
    root = Path(__file__).resolve().parents[1]
    try:
        prewarm(root, Path(argv[1]))
    except Exception as exc:  # 预暖是优化路径：任何意外只 warn，不 fail-init
        _warn(f"提示: .uv-cache 预暖异常（{exc!r}），已跳过——首次 uv 调用将冷启动拉取依赖")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
