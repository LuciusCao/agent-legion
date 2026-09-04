//! Tool specifications handed to the provider (description + JSON Schema
//! parameters), one entry per [`ToolKind`]. Kept out of `mod.rs` to stay
//! inside the architecture file-size budget; behavioral wiring (execution,
//! sandbox rules) lives in the per-tool modules.

use super::ToolKind;
use crate::provider::ToolSpec;

/// Tool specification handed to the provider.
pub fn spec(kind: ToolKind) -> ToolSpec {
    let (description, parameters) = match kind {
        ToolKind::Read => (
            "Read a UTF-8 text file inside the working directory or an \
             enabled skill directory (read-only). \
             Optional 1-based `offset` and `limit` select a line range. \
             Output is truncated to the first 2000 lines or 50KB \
             (whichever is hit first). Use offset/limit for large files; \
             when you need the full file, continue with offset until \
             complete.",
            serde_json::json!({
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the working directory."},
                    "offset": {"type": "integer", "description": "1-based first line to read (default 1)."},
                    "limit": {"type": "integer", "description": "Maximum number of lines to read (default all)."}
                },
                "required": ["path"]
            }),
        ),
        ToolKind::Write => (
            "Write a file inside the working directory (atomic tmp+rename; \
             parent directories are created).",
            serde_json::json!({
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the working directory."},
                    "content": {"type": "string", "description": "Full file content."}
                },
                "required": ["path", "content"]
            }),
        ),
        ToolKind::Bash => (
            "Run a bash command in the working directory (env inherited). \
             Output is truncated to the last 2000 lines or 50KB \
             (whichever is hit first); if truncated, the full output is \
             saved to a temp file. On timeout the whole process group \
             gets SIGTERM, then SIGKILL after a grace period. \
             Full-disk scan commands (e.g. `find /`) are rejected; \
             search within the working directory or a specific \
             subdirectory, and use `command -v <name>` to locate \
             executables (python/python3 are on PATH).",
            serde_json::json!({
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command passed to `bash -c`."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 120, max 3600)."}
                },
                "required": ["command"]
            }),
        ),
        ToolKind::Uuid => (
            "Generate or validate UUIDs. NEVER hand-write UUIDs — models \
             produce invalid ones; always mint them here. \
             `generate` returns fresh random UUIDs: every call produces \
             different values (replay included), so persist generated \
             values into your output files instead of expecting \
             reproducibility. `validate` checks each value and fails on \
             format, version, and variant problems (parseable-but-anomalous \
             values like nil/max UUIDs fail as non-RFC4122 variants).",
            serde_json::json!({
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["generate", "validate"], "description": "Operation to perform."},
                    "count": {"type": "integer", "description": "generate: how many UUIDs to mint (default 1, max 100)."},
                    "version": {"type": "string", "enum": ["v4", "v7"], "description": "generate: UUID version — v4 random (default); v7 time-ordered, friendlier for database keys."},
                    "values": {"type": "array", "items": {"type": "string"}, "description": "validate: UUID strings to check (max 1000 entries, each max 512 chars, no control characters)."}
                },
                "required": ["op"]
            }),
        ),
    };
    ToolSpec {
        name: kind.name().to_string(),
        description: description.to_string(),
        parameters,
    }
}
