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
EXIT trap + 死 pid 清扫器整个协议族）：调用级独立中转目录
``.uv-cache.prewarm-incoming.<pid>/`` → cp 克隆完整 → ``os.rename`` 原子
落位 → finally 清理。staging 生命周期阶段：S0 入口判活清扫 → S1 克隆写入
（危险区）→ S2 rename 落位 → S3 finally 清理 → S4 下次调用的 S0。

staging 生命周期 × 危害源矩阵（族级模型；review 请按格查证，勿逐格发现）：

1. 并发兄弟调用（同 worktree 双 init）——**设计消除**：S1 各写独立中转
   目录，互不 rmtree/rename；S2 rename(2) 对已存在非空目录原子失败
   （EEXIST/ENOTEMPTY，git link+unlink / npm move-concurrently 同款
   原语），恰一个完整克隆落位、落败者丢弃自己的克隆——无部分落位交错
   （PR #1188 codex P1 修复：首版固定中转目录被共享清空续写，实测部分
   落位）；S0/S4 判活清扫只动死 pid，活跃中转目录受保护。
2. 锁外 actor（并发 uv run 在窗口内创建 final）——**设计消除**：rename
   对非空 final 原子失败走落败路径；对**空目录** final 成功替换（并发
   uv 刚建的空 cache 被完整 cache 换掉——无害且有益，空 cache 无任何
   可丢失内容）。
3. SIGTERM——**本轮修复**（PR #1188 codex P2 第三轮）：Python 默认动作
   直接终止、不跑 finally，S1 在飞克隆会残留到 S4。进入危险区前安装
   handler 把 SIGTERM 转化为 SystemExit(143)，清理路径因此被执行；
   落位后 finally 恢复原 handler（helper 可被库式调用，不泄漏进程状态；
   恢复窗口内 staging 已清/已落位，无清理义务）。handler 内再被信号
   打断：清理全是幂等的 except-OSError 操作，后果 = 保留死重——安全
   方向，由 S4 兜底。SIGINT 无需 handler：KeyboardInterrupt 是异常，
   天然穿透 finally（设计消除）。
4. SIGKILL——**兜底路径**：无法拦截，残留由 S4 判活清扫回收（接受窗口：
   残留存活至下次 init）。若 helper 被单独 SIGKILL（非进程组），孤儿 cp
   会续写 staging，可能与 S4 清扫交错留下有界死重——cp 退出后下一次
   清扫回收（接受：与已合并 #1182 bash 版同性质，方向安全）。
5. 清理失败（权限/旗标/TOCTOU）——**本轮加固**（reviewer minor 1）：
   S0/S3 清理全部 lstat 区分 + except-OSError 幂等降级；cp 前 lexists
   断言 staging 不存在（含悬空 symlink——exists() 对之返回 False，必须
   用 lexists），清理失败留下预存目录时走 warn 降级而非让 cp 以「拷入」
   语义把 staging/.uv-cache 嵌套落位成 final。
6. symlink 残留 staging——**设计消除**（上一轮 codex P2）：清理一律
   lstat 区分，symlink/普通文件 unlink、真实目录才 rmtree——
   rmtree(ignore_errors=True) 对 symlink 静默保留会让 cp 穿透写入链接
   目标（可为任意目录）、rename 把 symlink 本身落位成 .uv-cache。
7. pid 复用 vs 在飞清扫窗口——**登记，不改代码**（reviewer minor 2）：
   死 pid 的 staging 被判活清扫时 pid 恰好被复用 → kill -0 成功 → 保留
   死重。与已合并 #1182 bash 版同性质；方向安全（绝不误删活跃数据），
   残留由该 pid 真正死亡后的 S4 回收。
8. cp I/O 失败（磁盘满/读写错）——**设计路径**：S3 finally 清 staging，
   warn 降级冷启动，不 fail-init。
"""

from __future__ import annotations

import errno
import os
import shutil
import signal
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
        return True
    except OSError as exc:  # 不存在→死；权限错=存在但属他人→视为活跃（安全方向）
        return not isinstance(exc, ProcessLookupError)


def _sweep_staging_leftovers(root: Path) -> None:
    # 首版脚本固定名残留（无 pid 后缀，glob 匹配不到）一并清，lstat 安全。
    _remove_path(root / STAGING_PREFIX)
    for stale in root.glob(f"{STAGING_PREFIX}.*"):
        suffix = stale.name.removeprefix(f"{STAGING_PREFIX}.")
        # 纯数字后缀且（本进程 pid 重入 或 判死）才清；其余一律保留。
        if suffix.isdigit() and (int(suffix) == os.getpid() or not _pid_alive(int(suffix))):
            _remove_path(stale)


def prewarm(worktree_root: Path, base: Path) -> str:
    """把 ``base/.uv-cache`` 克隆落位为 ``worktree_root/.uv-cache``。

    返回状态串供测试断言；提示语打印到 stdout/stderr（风格与原 bash
    实现一致：幂等跳过静默，失败/并发落败 warn 到 stderr，成功提示到
    stdout）。调用方（init-worktree.sh）忽略状态——预暖失败不 fail-init。
    """
    final = worktree_root / FINAL_NAME
    staging = worktree_root / f"{STAGING_PREFIX}.{os.getpid()}"
    # SIGTERM → SystemExit（矩阵格 3）：Python 默认动作不跑 finally，在飞
    # 克隆会残留到下次 init。finally 恢复原 handler，不泄漏进程状态。
    prev_term = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        # S0 入口判活清扫：回收死 pid 残留（含 SIGKILL）与本进程重入的旧
        # 中转目录；活跃 pid 的中转目录一律不动。
        _sweep_staging_leftovers(worktree_root)
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
        # 清理失败加固（矩阵格 5）：staging 仍被预存内容占住时 warn 降级，
        # 否则 cp 对已存在目录走「拷入」语义会把嵌套落位成 final。lexists
        # 不跟随 symlink（exists() 对悬空链接返回 False，会漏判）。
        if os.path.lexists(staging):
            _warn(
                "提示: .uv-cache 预暖中转目录清理失败，已跳过——首次 uv 调用将冷启动拉取依赖（正常路径，仅较慢）"
            )
            return "failed-staging-dirty"
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
        signal.signal(signal.SIGTERM, prev_term)


def main(argv: list[str]) -> int:
    if len(argv) <= 1:
        return 0
    try:
        prewarm(Path(__file__).resolve().parents[1], Path(argv[1]))
    except Exception as exc:  # 预暖是优化路径：任何意外只 warn，不 fail-init
        _warn(f"提示: .uv-cache 预暖异常（{exc!r}），已跳过——首次 uv 调用将冷启动拉取依赖")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
