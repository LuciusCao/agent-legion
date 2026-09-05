//! The velites tools: `read`, `write`, `bash` by default, plus the opt-in
//! utility tools `uuid` (#442) and `validate` (#443) (design §8).
//!
//! Sandbox invariant: every path a tool touches must canonicalize to a
//! location inside the process working directory (the job dir the worker
//! launched velites in) — except READS, which may additionally land inside
//! the explicit read-only roots (`--skill` directories and the session dir,
//! design §5). Writes stay cwd-only. Escapes (`../`, absolute paths,
//! symlinks) are rejected before any filesystem mutation happens.

pub mod bash;
pub mod command_guard;
pub(super) mod command_paths;
pub mod read;
mod specs;
pub mod truncate;
pub mod uuid;
pub mod validate;
pub mod write;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use serde_json::Value;

use crate::cancel::CancelToken;
use crate::events::ContentBlock;
use crate::provider::ToolSpec;
use crate::sandbox::Sandbox;

/// Wall-clock milliseconds since `started` (monotonic; a duration too large
/// for u64 saturates at u64::MAX). Shared by every tool's #469 timing
/// measurement.
pub(crate) fn elapsed_ms(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
}

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
    /// Canonicalized `--skill` directories the `validate` tool resolves the
    /// output contract from (subset of `read_roots`, first declarer wins).
    pub skill_dirs: Vec<PathBuf>,
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
    /// Phase timing (#469) surfaced on `tool_execution_end.timing`;
    /// `None` only for failures raised before any measurement (argument
    /// validation, guard rejection, disabled tools) — a measured failure
    /// calls [`ToolOutput::measured`] so the dispatch boundary still
    /// stamps its `totalMs`. In-process tools carry `total_ms` only.
    pub timing: Option<crate::events::ToolTiming>,
}

impl ToolOutput {
    pub fn text(text: String, is_error: bool) -> Self {
        let output_bytes = text.len() as u64;
        Self {
            content: vec![ContentBlock::Text { text }],
            is_error,
            output_bytes,
            timing: None,
        }
    }

    pub fn error(message: String) -> Self {
        Self::text(message, true)
    }

    /// Mark this output as measured (#469): the tool already did real work
    /// before producing this result, so the dispatch boundary must stamp
    /// its `totalMs` even though `is_error` is set (uuid's invalid-list
    /// verdicts, validate's contract violations, read/write I/O failures —
    /// dropping them from per-tool timing would systematically exclude the
    /// slow/failing samples). Contrast the unmeasured failures (argument
    /// validation, guard rejection), which stay `timing: None`.
    pub fn measured(mut self) -> Self {
        self.timing.get_or_insert_with(Default::default);
        self
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

/// The tool kinds, keyed by their wire name. `Uuid` and `Validate` are
/// opt-in only (never in the default `--tools` set).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolKind {
    Read,
    Write,
    Bash,
    Uuid,
    Validate,
}

impl ToolKind {
    pub fn from_name(name: &str) -> Option<Self> {
        match name {
            "read" => Some(Self::Read),
            "write" => Some(Self::Write),
            "bash" => Some(Self::Bash),
            "uuid" => Some(Self::Uuid),
            "validate" => Some(Self::Validate),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::Write => "write",
            Self::Bash => "bash",
            Self::Uuid => "uuid",
            Self::Validate => "validate",
        }
    }

    /// Tool specification handed to the provider.
    pub fn spec(self) -> ToolSpec {
        specs::spec(self)
    }

    /// Dispatch one tool execution. This is the single #469 timing boundary:
    /// `totalMs` (dispatch start → result ready) is filled here for every
    /// measured tool, so the in-process tools carry zero instrumentation of
    /// their own. `bash` additionally fills its subprocess phase fields
    /// (spawnMs / firstByteMs / restMs / reapMs) itself; it leaves
    /// `total_ms` untouched so the dispatch layer stays the single source
    /// for the total and the decomposition base stays consistent
    /// (total ≈ spawn + firstByte + rest + reap; see `events::ToolTiming`
    /// for what the residual covers).
    ///
    /// Tool errors raised before any measurement (argument validation, guard
    /// rejection, disabled tools) surface as error content with `timing:
    /// None` — the same convention as `RequestTiming` on `message_end`. An
    /// error output that ALREADY carries phases (bash timeout: is_error
    /// with spawn/firstByte/rest/reap filled) keeps them and still gets
    /// its totalMs here.
    pub async fn execute(self, args: &Value, ctx: &ToolContext) -> ToolOutput {
        let started = Instant::now();
        let mut output = match self {
            Self::Read => read::run(args, ctx).await,
            Self::Write => write::run(args, ctx).await,
            Self::Bash => bash::run(args, ctx).await,
            Self::Uuid => uuid::run(args, ctx).await,
            Self::Validate => validate::run(args, ctx).await,
        };
        // Unmeasured failures (error + no phases recorded) stay timing-free;
        // everything else gets its totalMs at this single boundary.
        if !(output.is_error && output.timing.is_none()) {
            let timing = output
                .timing
                .get_or_insert_with(crate::events::ToolTiming::default);
            timing.total_ms = Some(elapsed_ms(started));
        }
        output
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
