"""#831/#835 velites 副本新鲜度对账核心（软告警）——Host 与 Worker 共用。

对账对象是**每个消费角色实际解析到的** velites 家族二进制：把解析结果旁
的 ``.src-stamp`` 指纹与仓库当前 ``velites/`` 子树的 git tree hash 比对，
不一致即产出告警文案。#835 前对账只覆盖 Worker 的 agent runtime 面
（``resolve_binary("velites")``），恰好漏掉四轮 codex 评审的主战场——
code 沙箱面（``resolve_sandbox_binary``，Host 与 Worker 的 code 节点共
用）；本模块把两面的组合下沉到 shared（Host 启动钩子不得 import worker
包），Worker 侧（worker/runtime/staleness.py）与 Host 侧
（server/app/main.py 的 lifespan 钩子）共用同一核心。

背景：``native-prod-up.sh`` 历史版本只以 PATH 模式调 ``ensure-velites.sh``，
而解析顺序（自带副本优先）让首次 install-deps 安置的 ``data/bin`` 副本
永久优先命中——velites 升级在原生生产环境静默失效。修复后 prod-up 经
部署 planner（scripts/velites_deploy_plan.py，同样以本解析面为事实源）
刷新全部安置点，本对账是其可见性兜底：漂移仍在（刻意维持旧版 / 手工
安置 / 部署面将来再漏一个通道）时启动日志必须说话，而不是等下一轮
评审来发现。

不变量：**软告警绝不阻断启动**。漂移检查读文件系统、跑子进程、解字节
流，失败面是开放集合——点状 except 白名单封口必有洞（非 UTF-8 stamp 的
``UnicodeDecodeError`` 曾可穿透到 ``prepare_runtime_models`` 使 Worker 以
退出码 1 crash-loop）。因此 ``reconcile_velites_copies`` 最外层带总兜底，
「不可对账」一律静默：无 stamp（GitHub Release 产物/手工安置）、stamp
损坏、指纹不可得（docker 镜像无 repo、无 git）都跳过——刻意不
fail-closed，velites 版本线独立于仓库，允许刻意落后。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from shared.code_sandbox import resolve_sandbox_binary

#: 仓库根（.git 与 velites/ 源码树所在）：裸机形态进程自仓库根启动，指纹
#: 对账用它算期望指纹；docker 镜像只带 worker/ + shared/（无 repo），前置
#: 形态检查不过即静默跳过对账。
_REPO_ROOT = Path(__file__).resolve().parents[1]

#: 家族 stamp 后缀：与 scripts/ensure-velites.sh 的
#: ``STAMP="${VELITES_BIN}.src-stamp"`` 同约定。跨进程协议常量在此单一
#: 定义——部署 planner（scripts/velites_deploy_plan.py）与 Worker 侧
#: staleness 都从这里 import；与 bash 侧的一致性由
#: tests/scripts/test_velites_deploy_plan.py 钉住。
SRC_STAMP_SUFFIX = ".src-stamp"

#: 指纹形态：git tree hash——SHA-1 仓库 40 位、SHA-256 仓库 64 位 hex，
#: 两个明确长度（41–63 位的中间长度不存在于任何 git 对象 ID，是截断/
#: 损坏的征兆）。stamp 与 git 输出都须整体匹配——垃圾内容（跨版本格式
#: 变化、手工随意写入、PATH 上 git 包装器的额外输出行）按「不可对账」
#: 处理，防止必然不等的假告警与多行内容注入启动日志。
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

#: stamp 体积上限：正常 tree hash ≤ 64 字节 + 换行；超限视为损坏，不做
#: 全量读入（巨文件 OOM 防线）。
_STAMP_MAX_BYTES = 128


def read_src_stamp(binary: Path) -> str | None:
    """读取并校验二进制旁的 src-stamp；不可对账形态（缺失/损坏/垃圾内
    容）返回 None。

    is_file() 对 FIFO/目录返回 False：命名管道会挂死 open()（比崩溃更难
    诊断的启动挂起），目录会抛 IsADirectoryError——都按不可对账处理。"""
    stamp = Path(f"{binary}{SRC_STAMP_SUFFIX}")
    if not stamp.is_file():
        return None
    try:
        if stamp.stat().st_size > _STAMP_MAX_BYTES:
            return None
        stamped = stamp.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None  # 读失败/解码失败与缺失同级：无从对账，不告警
    return stamped if stamped and _FINGERPRINT_RE.fullmatch(stamped) else None


def expected_velites_fingerprint(repo_root: Path) -> str | None:
    """仓库当前 velites/ 源码指纹（git tree hash）。

    前置形态检查（仓库根有 .git——worktree 的 .git 是文件——且有
    velites/ 子树）不过时不调 git：``git -C`` 会向上发现无关的祖先仓库，
    若它恰有 velites/ 子树会拿到它的 tree hash 制造假告警。git 不可用、
    非仓库、HEAD 无 velites 子树时返回 None。"""

    if not (repo_root / ".git").exists() or not (repo_root / "velites").is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD:velites"],
            capture_output=True,
            text=True,
            # errors="replace"：指纹是 hex，替换解码无损，且封死 stdout/
            # stderr 在非 UTF-8 locale 下的解码异常（ValueError 家族）。
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fingerprint = result.stdout.strip()
    return fingerprint if _FINGERPRINT_RE.fullmatch(fingerprint) else None


def _drift_message(path: str, stamped: str, expected: str, roles: str) -> str:
    # 方向中性：tree hash 无时序，PATH 副本又跨 worktree 共享——副本可能
    # 落后于本仓库（修复未生效）也可能领先（来自其他 worktree 的更新
    # 构建），两种解读都给出，操作者才不会被反向误导。
    return (
        f"警告: 解析到的 velites 二进制 {path} 的源码指纹"
        f"（{stamped[:12]}）与仓库 velites/ 源码指纹（{expected[:12]}）不一致——"
        f"该副本将持续被{roles}使用（解析顺序：自带副本优先、PATH 兜底）。"
        "可能是副本落后于源码（velites 的修复如内存上限、输出截断未生效），"
        "也可能是 PATH 副本领先于本仓库版本线（PATH 上的 velites 跨 worktree "
        "共享）。对齐方式: make prod-up（自动刷新 PATH 与 data/bin 两处副本）"
        "或 ./scripts/ensure-velites.sh --dest data/bin；刻意维持该版本可忽略"
        "本告警"
    )


def _reconcile(consumers: list[tuple[str, str | None]], repo_root: Path) -> list[str]:
    """对账本体（不含总兜底）：每个漂移的解析路径一条告警。

    同一二进制被多个角色解析（裸机无独立包装器时 agent runtime 与 code
    沙箱命中同一 velites）按路径去重、角色合并——一条告警说清全部消费
    面，不做复读。"""
    by_path: dict[str, list[str]] = {}
    for roles, path in consumers:
        if path:
            by_path.setdefault(path, []).append(roles)
    expected = expected_velites_fingerprint(repo_root)
    if expected is None:
        return []
    warnings: list[str] = []
    for path, role_list in by_path.items():
        stamped = read_src_stamp(Path(path))
        if stamped is None or stamped == expected:
            continue
        warnings.append(_drift_message(path, stamped, expected, " 与 ".join(role_list)))
    return warnings


def reconcile_velites_copies(
    consumers: list[tuple[str, str | None]], *, repo_root: Path | None = None
) -> list[str]:
    """对 (角色, 解析路径) 列表做指纹对账；漂移路径各返回一条告警文案。

    不可对账或对账过程任何失败返回空列表——见模块 docstring 的不变量。
    consumers 由调用侧组合：Worker 传 agent runtime 面 +
    code 沙箱面（worker/runtime/staleness.py），Host 传
    ``host_staleness_warnings`` 的组合。"""
    try:
        return _reconcile(consumers, repo_root or _REPO_ROOT)
    except Exception:  # noqa: BLE001
        # #204 broad-except audit: 软告警的总兜底，「漂移可见」绝不演变为
        # 「阻断启动」——读取点/子进程/解码的失败面是开放集合，白名单封口
        # 必有洞（UnicodeDecodeError 穿透曾使 Worker crash-loop）。已知族
        # 已在 read_src_stamp/expected 内细分降级，到达这里的是未枚举形态，
        # 吞掉即「不可对账」语义，无重试价值也无需留痕（下次启动自然再试）。
        return []


def host_staleness_warnings() -> list[str]:
    """Host 侧消费角色对账：code 沙箱面 + 本地 agent runtime 面。

    code 沙箱（server/app/executors/_code_sandbox.py）与 Worker 共用
    ``resolve_sandbox_binary``（自带副本优先）；Host 的本地 agent 执行
    （server/app/workflows/velites_command.py）按裸名走 PATH——两个消费
    面都纳入对账（#835 前 Host 侧完全没有对账）。docker 形态（无 repo）
    自然静默。"""
    return reconcile_velites_copies(
        [
            ("code 沙箱（Host 与 Worker 的 code 节点）", resolve_sandbox_binary()),
            ("Host 本地 agent runtime（PATH 裸名解析）", shutil.which("velites")),
        ]
    )
