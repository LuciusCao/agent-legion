"""Shared velites sandbox plumbing for sandboxed code-node execution.

Used by BOTH sides of the code-execution path — the Host's custom-code
executor (server/app/executors/_code_sandbox.py) and the Worker's kind='code'
runner (worker/code_runner.py). Previously the argv/env/read-roots/result
parsing was copied between the two with "keep in sync" comments; this module
is the single copy. The worker image ships only worker/ + shared/ (no repo
checkout), which is why the shared home must stay stdlib-only.

Design invariants carried by this module:
- ``child_env`` keeps database DSNs, vault keys and provider tokens OUT of
  the sandbox (VAULT-SECRET-001); PYTHONPATH points at an explicit import
  root (repo checkout on the Host, extracted bundle snapshot on the Worker).
- ``read_roots`` allow-lists only the import root(s) plus interpreter
  prefixes; a materials cache root, when passed, is the ONLY material path
  node code may read — a static root, never a per-material dynamic grant
  (MATERIAL-ACCESS-001).
- network is strictly opt-in: only ``sandbox_network is True`` grants it
  (P-0.5), anything else — including truthy non-bools — denies.
- the child result file sits in a sandbox-writable directory and is never
  trusted blindly (strict schema check).
"""

from __future__ import annotations

import json
import os
import shutil
import site
import sys
from pathlib import Path

# 契约常量经本模块 re-export（#282 之前它们就定义在这里；worker/host 的
# 既有 import 路径保持不变）。单一事实源是 shared/code_contract.py。
from shared.code_contract import AUTH_FAILURE_MARKER_PATH as AUTH_FAILURE_MARKER_PATH
from shared.code_contract import CODE_BUNDLE_LIBS_DIR as CODE_BUNDLE_LIBS_DIR
from shared.code_contract import CODE_BUNDLE_NODE_FILE as CODE_BUNDLE_NODE_FILE
from shared.code_contract import CODE_RESULT_LOG_MEMBER as CODE_RESULT_LOG_MEMBER
from shared.code_contract import CODE_RESULT_METADATA_KEYS as CODE_RESULT_METADATA_KEYS
from shared.code_contract import MAX_CONNECTION_KEY_CHARS as MAX_CONNECTION_KEY_CHARS

#: 沙箱包装器的候选二进制名（#383）：优先 velites-sandbox（独立 bin，烤进
#: worker 镜像的沙箱基础设施），兜底 velites（全量二进制的 sandbox wrap
#: 子命令，裸机形态）。两形态 argv 完全兼容（velites-sandbox 吞掉前导
#: sandbox wrap token），调用方不需要知道解析到的是哪个。
SANDBOX_BINARY_CANDIDATES: tuple[str, ...] = ("velites-sandbox", "velites")

#: 自带二进制目录（仓库根 data/bin）：Worker 裸机部署经
#: ``ensure-velites.sh --dest data/bin`` 安置的产物落点，沙箱解析与
#: worker/binary_resolution.py 的 runtime 解析共用（该模块 re-export 本
#: 常量为 BUNDLED_BINARY_DIR——单一事实源，mock 任一侧改变同一目录）。
#: Docker 镜像内此目录不存在（runtime 二进制经 compose 挂载、沙箱包装器
#: 在 /usr/local/bin），探测自然跳过；Host 侧同理。
BUNDLED_SANDBOX_DIR = Path(__file__).resolve().parents[1] / "data" / "bin"


def resolve_sandbox_binary() -> str | None:
    """Resolve the sandbox wrapper; None when no candidate exists.

    解析面（Host 与 Worker 共用）：候选名按序探测「自带副本目录 → PATH」；
    裸机 Worker 的 data/bin 自带副本（ensure-velites.sh 安置）与 PATH 上的
    全量二进制都命中。与 agent runtime 解析（worker/binary_resolution.py
    的 runtime 目录语义）刻意分开：沙箱是基础设施、不是 runtime。
    """
    for name in SANDBOX_BINARY_CANDIDATES:
        bundled = BUNDLED_SANDBOX_DIR / name
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def child_env(import_root: Path) -> dict[str, str]:
    """Minimal environment for the sandboxed child.

    Everything else — database DSNs, vault master key, provider tokens
    (AGENT_LEGION_* and friends) — stays out of the sandbox. PYTHONPATH
    points at ``import_root`` (repo checkout on the Host, extracted bundle
    snapshot on the Worker) plus the interpreter's site-packages.
    """
    env: dict[str, str] = {}
    # sandbox-exec/bwrap are spawned by name inside the wrapper.
    if path := os.environ.get("PATH"):
        env["PATH"] = path
    # tempfile and locale basics for the interpreter and node code.
    for key in ("TMPDIR", "HOME"):
        if value := os.environ.get(key):
            env[key] = value
    for key, value in os.environ.items():
        if key == "LANG" or key.startswith("LC_"):
            env[key] = value
    python_paths = [str(import_root), *(str(Path(p).resolve()) for p in site.getsitepackages())]
    if python_path := os.environ.get("PYTHONPATH"):
        python_paths.append(python_path)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    return env


def read_roots(import_roots: list[Path], materials_cache_root: Path | None = None) -> list[str]:
    """Read-only allowlist: import roots plus the interpreter prefixes.

    The venv prefix (``sys.prefix``) keeps site-packages importable and
    pyvenv.cfg readable; the base prefix (``sys.base_prefix``) covers
    @rpath-loaded libpython — passed explicitly instead of relying on the
    wrapper's PATH python3 probe, which may resolve a different interpreter
    than ``sys.executable``.
    """
    roots = [str(root) for root in import_roots]
    if materials_cache_root is not None and materials_cache_root.is_dir():
        roots.append(str(materials_cache_root))
    for prefix in {sys.prefix, sys.base_prefix}:
        if prefix:
            roots.append(str(Path(prefix).resolve()))
    return roots


def build_sandbox_argv(
    velites: str,
    job_dir: Path,
    import_roots: list[Path],
    result_path: Path,
    *,
    sandbox_network: object,
    materials_cache_root: Path | None = None,
    marker: str | None = None,
) -> list[str]:
    """``velites sandbox wrap`` argv for one code node (EXEC-CODE-003).

    Network is strictly opt-in (P-0.5): only ``sandbox_network is True``
    appends ``--allow-network``; the sandbox default denies everything else.
    ``marker`` (optional, Worker-only) rides the child argv after the result
    path so a ps-based orphan reaper can attribute the process group to its
    execution — the same identity convention as the agent path's
    ``--name agent-legion-<execution_id>``; ``code_child`` reads only argv[1]
    and never consumes it (#186).
    """
    command = [velites, "sandbox", "wrap", "--cwd", str(job_dir)]
    for root in read_roots(import_roots, materials_cache_root):
        command += ["--allow-read", root]
    if sandbox_network is True:
        command.append("--allow-network")
    command += [
        "--",
        str(Path(sys.executable).resolve()),
        "-m",
        "workspace_libs.code_child",
        str(result_path),
    ]
    if marker is not None:
        command.append(marker)
    return command


def read_result_error(result_path: Path) -> str | None:
    """Parse the child's JSON result with a strict schema check.

    Returns None for a successful run (outputs still need checking); any
    non-conforming content yields a failure message — the file sits in a
    sandbox-writable directory and must never be trusted blindly. Callers
    wrap the message into their own result types.
    """
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = None
    if (
        not isinstance(document, dict)
        or set(document) != {"status", "message"}
        or document["status"] not in ("ok", "error")
        or not (document["message"] is None or isinstance(document["message"], str))
    ):
        return "sandboxed code node did not return a result"
    if document["status"] == "error":
        return str(document["message"])
    return None
