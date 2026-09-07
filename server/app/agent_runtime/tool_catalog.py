"""Runtime 工具目录数据（#476）：velites 静态镜像 + pi 静态登记。

velites 的目录与 ``velites tools list --json`` 输出全等——由
``tests/agent_runtime/test_runtime_tool_catalog.py`` 的跨二进制契约测试
钉住（binary 缺失时跳过对账、保留静态断言）。pi 是外部 runtime，无法动态
发现，按实测行为静态登记（#449 提议先实测再登记；pi 对未知工具名的容错
未承诺，这里只登记平台侧已验证过的三件套）。

同名工具交集不是契约：read/write/bash 在两个 runtime 都出现只是命名巧合，
参数 schema 与行为语义（截断、沙箱、超时）不保证一致——description /
parameters 必须随 runtime 走，不得借交集建立跨 runtime 统一语义。
"""

from __future__ import annotations

from server.app.agent_runtime.adapter import ToolCatalogEntry

# velites 工具目录：与 `velites tools list --json` 全等（tier/activation 语义
# 见 adapter.ToolCatalogEntry docstring）。validate 是 forced 档——退出契约门
# 由 --require-output 激活、与 --tools 无关，harness 在激活条件成立时自动
# 广告该工具（velites 侧联动，host 不重复实现）。
VELITES_TOOL_CATALOG: tuple[ToolCatalogEntry, ...] = (
    ToolCatalogEntry(
        name="read",
        tier="default",
        description=(
            "Read a UTF-8 text file inside the working directory or an enabled "
            "skill directory (read-only). Optional 1-based `offset` and `limit` "
            "select a line range. Output is truncated to the first 2000 lines "
            "or 50KB (whichever is hit first). Use offset/limit for large "
            "files; when you need the full file, continue with offset until "
            "complete."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, relative to the working directory.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based first line to read (default 1).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default all).",
                },
            },
            "required": ["path"],
        },
    ),
    ToolCatalogEntry(
        name="write",
        tier="default",
        description=(
            "Write a file inside the working directory (atomic tmp+rename; "
            "parent directories are created)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, relative to the working directory.",
                },
                "content": {"type": "string", "description": "Full file content."},
            },
            "required": ["path", "content"],
        },
    ),
    ToolCatalogEntry(
        name="bash",
        tier="default",
        description=(
            "Run a bash command in the working directory (env inherited). "
            "Output is truncated to the last 2000 lines or 50KB "
            "(whichever is hit first); if truncated, the full output is "
            "saved to a temp file. On timeout the whole process group "
            "gets SIGTERM, then SIGKILL after a grace period. "
            "Full-disk scan commands (e.g. `find /`) are rejected; "
            "search within the working directory or a specific "
            "subdirectory, and use `command -v <name>` to locate "
            "executables (python/python3 are on PATH)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command passed to `bash -c`.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120, max 3600).",
                },
            },
            "required": ["command"],
        },
    ),
    ToolCatalogEntry(
        name="uuid",
        tier="opt-in",
        description=(
            "Generate or validate UUIDs. NEVER hand-write UUIDs — models produce "
            "invalid ones; always mint them here. `generate` returns fresh random "
            "UUIDs: every call differs (replay included), so persist generated "
            "values into your output files instead of expecting reproducibility. "
            "`validate` fails on format, version, and variant problems (parseable "
            "but anomalous values like nil/max UUIDs fail as non-RFC4122 variants)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["generate", "validate"],
                    "description": "Operation to perform.",
                },
                "count": {
                    "type": "integer",
                    "description": "generate: how many UUIDs to mint (default 1, max 100).",
                },
                "version": {
                    "type": "string",
                    "enum": ["v4", "v7"],
                    "description": (
                        "generate: UUID version — v4 random (default); v7 "
                        "time-ordered, friendlier for database keys."
                    ),
                },
                "values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "validate: UUID strings to check (max 1000 entries, each "
                        "max 512 chars, no control characters)."
                    ),
                },
            },
            "required": ["op"],
        },
    ),
    # #518：JSON 字段级读-改-写原语——模型不再为改一个大 JSON 的一个字段
    # 整文件重写或 bash heredoc 手写 python。get/set/delete 共用 op 模式
    # （与 uuid 一致）；文件级沙箱与原子写回（tmp+rename）与 write 同协议。
    ToolCatalogEntry(
        name="json",
        tier="opt-in",
        description=(
            "Read or modify one field of a JSON file via a JSON path — the "
            "read-modify-write primitive for patching large JSON artifacts "
            "you produced. NEVER rewrite a whole JSON file (write tool) to "
            "change one field, and NEVER shell out to python for this. `get` "
            "returns the value at the path (null when absent). `set` writes "
            "any JSON value at the path and saves the file (pretty-printed, "
            "atomically). `delete` removes the key/array element at the "
            "path. Paths: dotted keys and [index] segments, e.g. "
            '`steps[2].content` or `["a key.with.dots"].sub`; missing '
            "intermediate keys are an error for set/delete (no auto-create), "
            "and get reports null instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["get", "set", "delete"],
                    "description": "Operation to perform.",
                },
                "path": {
                    "type": "string",
                    "description": "JSON file path, relative to the working directory.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "JSON path to the field, e.g. `steps[2].content` or "
                        '`["a key"].sub` (max 512 chars).'
                    ),
                },
                "value": {
                    "description": (
                        "set: any JSON value to write at the path "
                        "(objects/arrays/strings/numbers/booleans/null)."
                    )
                },
            },
            "required": ["op", "path", "query"],
        },
    ),
    ToolCatalogEntry(
        name="validate",
        tier="forced",
        activation="--require-output",
        description=(
            "Check working-directory outputs against the skill's output contract "
            "(the ```yaml contract block in its references/output-contract.md). "
            "No arguments. On failure returns a numbered violation list to fix; "
            "when no skill declares a contract block, returns an informational "
            "error. Use it to self-check outputs mid-run before stopping."
        ),
        parameters={"type": "object", "properties": {}},
    ),
)

# pi 工具目录：外部 runtime 静态登记（#476 设计讨论——pi 无自描述通道）。
# 平台侧只对经实测确认的工具面负责：pi 的 `--tools` 透传同名三件套，
# 行为语义由 pi 自身决定，参数 schema 不在此声明（未知键不为契约）。
PI_TOOL_CATALOG: tuple[ToolCatalogEntry, ...] = (
    ToolCatalogEntry(name="read", tier="default"),
    ToolCatalogEntry(name="write", tier="default"),
    ToolCatalogEntry(name="bash", tier="default"),
)


def selectable_tool_names(entries: tuple[ToolCatalogEntry, ...]) -> frozenset[str]:
    """用户可选集：forced 档排除（#449 dispatch 校验与 Studio 勾选面共用）。"""
    return frozenset(entry.name for entry in entries if entry.selectable)


def default_tool_names(entries: tuple[ToolCatalogEntry, ...]) -> tuple[str, ...]:
    """默认集（tier=default，声明顺序）：Agent 定义缺省 tools 的预选来源。"""
    return tuple(entry.name for entry in entries if entry.tier == "default")
