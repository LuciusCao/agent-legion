//! The three velites tools: `read`, `write`, `bash` (design §8).
//!
//! Sandbox invariant: every path a tool touches must canonicalize to a
//! location inside the process working directory (the job dir the worker
//! launched velites in) — except READS, which may additionally land inside
//! the explicit read-only roots (`--skill` directories and the session dir,
//! design §5). Writes stay cwd-only. Escapes (`../`, absolute paths,
//! symlinks) are rejected before any filesystem mutation happens.

pub mod bash;
pub mod command_guard;
pub mod read;
pub mod truncate;
pub mod write;

use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde_json::Value;

use crate::cancel::CancelToken;
use crate::events::ContentBlock;
use crate::provider::ToolSpec;
use crate::sandbox::Sandbox;

/// Execution context shared by all tools.
pub struct ToolContext {
    /// Canonicalized working directory; the sandbox root.
    pub cwd: PathBuf,
    /// Cancellation flag; `bash` watches it while a child runs (TERM → grace
    /// → KILL on cancel, same as the timeout path). A default token is never
    /// cancelled.
    pub cancel: CancelToken,
    /// OS-level filesystem sandbox wrapping the `bash` child process
    /// (`None` = `--no-sandbox`; read/write keep the in-process check above
    /// either way).
    pub sandbox: Option<Arc<Sandbox>>,
    /// Canonicalized extra READ-ONLY roots the `read` tool may resolve into
    /// alongside the cwd: the explicit `--skill` directories and the session
    /// dir (design §5; mirrors the OS sandbox's read allowlist). The `write`
    /// tool never consults this list — writes stay cwd-only.
    pub read_roots: Vec<PathBuf>,
}

/// Outcome of one tool execution. Tool failures are reported as
/// `is_error: true` content (they go back to the model), not as harness
/// errors.
#[derive(Debug)]
pub struct ToolOutput {
    pub content: Vec<ContentBlock>,
    pub is_error: bool,
    /// Output volume before truncation (design §8).
    pub output_bytes: u64,
}

impl ToolOutput {
    pub fn text(text: String, is_error: bool) -> Self {
        let output_bytes = text.len() as u64;
        Self {
            content: vec![ContentBlock::Text { text }],
            is_error,
            output_bytes,
        }
    }

    pub fn error(message: String) -> Self {
        Self::text(message, true)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ToolError {
    #[error("path escapes the working directory sandbox: {0}")]
    SandboxEscape(String),
    #[error("invalid tool arguments: {0}")]
    InvalidArgs(String),
    #[error("command blocked by velites guard: {0}")]
    CommandBlocked(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

/// The three tool kinds, keyed by their wire name.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolKind {
    Read,
    Write,
    Bash,
}

impl ToolKind {
    pub fn from_name(name: &str) -> Option<Self> {
        match name {
            "read" => Some(Self::Read),
            "write" => Some(Self::Write),
            "bash" => Some(Self::Bash),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::Write => "write",
            Self::Bash => "bash",
        }
    }

    /// Tool specification handed to the provider.
    pub fn spec(self) -> ToolSpec {
        let (description, parameters) = match self {
            Self::Read => (
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
            Self::Write => (
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
            Self::Bash => (
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
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)."}
                    },
                    "required": ["command"]
                }),
            ),
        };
        ToolSpec {
            name: self.name().to_string(),
            description: description.to_string(),
            parameters,
        }
    }

    pub async fn execute(self, args: &Value, ctx: &ToolContext) -> ToolOutput {
        match self {
            Self::Read => read::run(args, ctx).await,
            Self::Write => write::run(args, ctx).await,
            Self::Bash => bash::run(args, ctx).await,
        }
    }
}

/// Resolve `raw` against the sandbox root, rejecting escapes.
///
/// The target may not exist yet (write), so resolution canonicalizes the
/// deepest existing ancestor — which collapses `..`, `.`, and symlinks —
/// then re-appends the non-existing tail and checks the result is still
/// inside the canonicalized root.
pub fn resolve_in_cwd(cwd_canonical: &Path, raw: &str) -> Result<PathBuf, ToolError> {
    resolve_within(cwd_canonical, &[], raw)
}

/// Resolve `raw` for the `read` tool: the path may land inside the cwd OR
/// any of the canonicalized extra read-only roots (`--skill` dirs, session
/// dir; design §5). Same escape protection as [`resolve_in_cwd`] — `..`
/// segments and symlinks that leave every allowed root are rejected.
pub fn resolve_readable(
    cwd_canonical: &Path,
    read_roots: &[PathBuf],
    raw: &str,
) -> Result<PathBuf, ToolError> {
    resolve_within(cwd_canonical, read_roots, raw)
}

fn resolve_within(
    cwd_canonical: &Path,
    extra_roots: &[PathBuf],
    raw: &str,
) -> Result<PathBuf, ToolError> {
    let raw_path = Path::new(raw);
    let candidate = if raw_path.is_absolute() {
        raw_path.to_path_buf()
    } else {
        cwd_canonical.join(raw_path)
    };

    let mut ancestor = candidate.clone();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    while !ancestor.exists() {
        match ancestor.file_name() {
            Some(name) => tail.push(name.to_os_string()),
            // Reached the filesystem root without hitting an existing path.
            None => return Err(ToolError::SandboxEscape(raw.to_string())),
        }
        if !ancestor.pop() {
            return Err(ToolError::SandboxEscape(raw.to_string()));
        }
    }

    let canonical_ancestor = ancestor.canonicalize()?;
    let mut resolved = canonical_ancestor;
    for component in tail.iter().rev() {
        resolved.push(component);
    }

    let inside = resolved.starts_with(cwd_canonical)
        || extra_roots.iter().any(|root| resolved.starts_with(root));
    if inside {
        Ok(resolved)
    } else {
        Err(ToolError::SandboxEscape(raw.to_string()))
    }
}
