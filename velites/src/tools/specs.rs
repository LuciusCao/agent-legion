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
            "Generate or validate UUIDs. NEVER hand-write UUIDs — models produce \
             invalid ones; always mint them here. `generate` returns fresh random \
             UUIDs: every call differs (replay included), so persist generated \
             values into your output files instead of expecting reproducibility. \
             `validate` fails on format, version, and variant problems (parseable \
             but anomalous values like nil/max UUIDs fail as non-RFC4122 variants).",
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
        ToolKind::Json => (
            "Read or modify one field of a JSON file via a JSON path — the \
             read-modify-write primitive for patching large JSON artifacts \
             you produced. NEVER rewrite a whole JSON file (write tool) to \
             change one field, and NEVER shell out to python for this. `get` \
             returns the value at the path (null when absent). `set` writes \
             any JSON value at the path and saves the file (pretty-printed, \
             atomically). `delete` removes the key/array element at the \
             path. Paths: dotted keys and [index] segments, e.g. \
             `steps[2].content` or `[\"a key.with.dots\"].sub`; missing \
             intermediate keys are an error for set/delete (no auto-create), \
             and get reports null instead.",
            serde_json::json!({
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["get", "set", "delete"], "description": "Operation to perform."},
                    "path": {"type": "string", "description": "JSON file path, relative to the working directory."},
                    "query": {"type": "string", "description": "JSON path to the field, e.g. `steps[2].content` or `[\"a key\"].sub` (max 512 chars)."},
                    "value": {"description": "set: any JSON value to write at the path (objects/arrays/strings/numbers/booleans/null)."}
                },
                "required": ["op", "path", "query"]
            }),
        ),
        ToolKind::Validate => (
            "Check working-directory outputs against the skill's output contract \
             (the ```yaml contract block in its references/output-contract.md). \
             No arguments. On failure returns a numbered violation list to fix; \
             when no skill declares a contract block, returns an informational \
             error. Use it to self-check outputs mid-run before stopping.",
            serde_json::json!({
                "type": "object",
                "properties": {}
            }),
        ),
    };
    ToolSpec {
        name: kind.name().to_string(),
        description: description.to_string(),
        parameters,
    }
}
