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

1. 固定中转目录 ``.uv-cache.prewarm-incoming/``：入口无条件清空重建——
   自洁设计覆盖一切中断残留（含 SIGKILL，其残留由下一次调用的入口清空
   回收），替代 pid 标记与死 pid 清扫器。
2. 克隆：``cp -Rc``（macOS clonefile）/ ``cp -R --reflink=auto``（Linux）
   把基准 cache 克隆进中转目录——写时复制秒级零额外磁盘；不支持的卷上
   cp 内部各自回退普通复制，仍是磁盘速度、远快于网络。uv cache 内容寻址、
   append-only、路径无关，克隆副本可直接使用。
3. 落位：``os.rename(staging, final)``。POSIX rename(2) 对已存在**非空**
   目录原子失败（EEXIST/ENOTEMPTY）——天然 no-replace、无窗口的落位
   语义（git 对象库 link(2)+unlink、npm move-concurrently 同款原语），
   并发落败者丢弃中转目录走跳过路径。注意 rename 对已存在**空目录**会
   成功替换：并发 `uv run` 刚建的空 cache 被完整 cache 整体换掉——无害
   且有益（空 cache 无任何可丢失内容）。
4. ``try/finally`` 保证中转目录在一切正常/异常路径下被清（落位成功后
   staging 已不存在，清理是 no-op）。

并发同 worktree 双 init 的已知取舍（issue #1186 实测）：两个进程的
入口自洁会互相摧毁对方在飞的克隆，最坏双双丢弃——预暖丢失、退化为
冷启动（安全方向：无嵌套、无误删、落位唯一，后续 uv 调用按需自建
cache）。恢复「恰一个赢家」需要互斥原语，而那正是本设计删除的协议族
（mkdir 锁/pid/trap），故接受该取舍：并发 init 同一 worktree 本就罕见，
单人路径（真实 99%+ 场景）不受影响。
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

FINAL_NAME = ".uv-cache"
STAGING_NAME = ".uv-cache.prewarm-incoming"


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def prewarm(worktree_root: Path, base: Path) -> str:
    """把 ``base/.uv-cache`` 克隆落位为 ``worktree_root/.uv-cache``。

    返回状态串供测试断言；提示语打印到 stdout/stderr（风格与原 bash
    实现一致：幂等跳过静默，失败/并发落败 warn 到 stderr，成功提示到
    stdout）。调用方（init-worktree.sh）忽略状态——预暖失败不 fail-init。
    """
    final = worktree_root / FINAL_NAME
    staging = worktree_root / STAGING_NAME
    # 自洁：无条件清空中转目录，回收一切中断残留（含 SIGKILL）。
    shutil.rmtree(staging, ignore_errors=True)
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
        source = base_cache
        if base_cache.is_symlink():
            try:
                source = base_cache.resolve(strict=True)
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
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str]) -> int:
    base = Path(argv[1]) if len(argv) > 1 else None
    if base is None:
        return 0
    root = Path(__file__).resolve().parents[1]
    try:
        prewarm(root, base)
    except Exception as exc:  # 预暖是优化路径：任何意外只 warn，不 fail-init
        _warn(f"提示: .uv-cache 预暖异常（{exc!r}），已跳过——首次 uv 调用将冷启动拉取依赖")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
