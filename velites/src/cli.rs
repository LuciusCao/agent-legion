//! CLI definition (clap derive). See docs/architecture/velites-harness.md §6.
//!
//! Unknown flags are a hard error (clap default, intentionally kept): silent
//! flag drift between the worker command builder and the harness is how Pi's
//! `--no-*` flags rotted, and velites must fail loudly instead.

use std::path::PathBuf;

use anyhow::Context;
use clap::Parser;

#[derive(Debug, Parser)]
#[command(
    name = "velites",
    version,
    about = "Agent Legion lightweight agent harness (headless, pi-compatible NDJSON event stream)."
)]
pub struct Cli {
    /// Output mode. Only `json` exists: velites is headless-only.
    #[arg(long, default_value = "json", value_parser = ["json"])]
    pub mode: String,

    /// Directory where the append-only session mirror (session.jsonl) is written.
    #[arg(long)]
    pub session_dir: Option<PathBuf>,

    /// Session identifier, written into the `session` event as sessionId.
    #[arg(long)]
    pub name: Option<String>,

    /// Skill directory whose SKILL.md is injected into the system prompt.
    /// Repeatable. Together with --system-prompt and the instruction this is
    /// one of the ONLY three context sources: velites never auto-discovers
    /// AGENTS.md, skill directories, templates, or user-level config.
    #[arg(long)]
    pub skill: Vec<PathBuf>,

    /// Enabled tools: comma-separated subset of read,write,bash.
    #[arg(long, value_delimiter = ',', default_value = "read,write,bash")]
    pub tools: Vec<String>,

    /// Provider name: `stub` (fixture-driven, no LLM) or `gateway` /
    /// `openai_compat` (OpenAI-compatible SSE chat completions; credentials
    /// from ~/.velites/config.json or VELITES_BASE_URL/VELITES_API_KEY).
    #[arg(long)]
    pub provider: String,

    /// Model identifier passed to the provider and reported in message events.
    #[arg(long)]
    pub model: Option<String>,

    /// Thinking/reasoning effort; mapped per provider (ignored by stub).
    #[arg(long)]
    pub thinking: Option<String>,

    /// Maximum retries for transient model-call failures (network errors,
    /// 429, 5xx, interrupted streams) with exponential backoff. Deterministic
    /// failures (other 4xx) are never retried.
    #[arg(long, default_value_t = 3)]
    pub max_retries: u32,

    /// Wall-clock bound for the whole run, in seconds. Also caps each
    /// provider HTTP request. When the deadline (or any other budget) runs
    /// out, the model gets one wrap-up turn before the run ends with
    /// `agent_end{reason: "budget_exceeded"}`. The default matches the
    /// gateway's long-generation scenarios (design §7).
    #[arg(long, default_value_t = 600)]
    pub timeout_seconds: u64,

    /// Maximum agent turns (assistant completions) before the budget is
    /// exhausted (one wrap-up turn follows, then the run ends).
    #[arg(long)]
    pub max_turns: Option<u32>,

    /// Maximum cumulative tokens (input+output+cacheRead) before the budget
    /// is exhausted (one wrap-up turn follows, then the run ends).
    #[arg(long)]
    pub max_tokens: Option<u64>,

    /// Files that must exist when the run ends. Repeatable. Paths must
    /// resolve inside the working directory (same sandbox as the tools);
    /// missing files trigger one remediation turn, and an
    /// `outputs_validation` event reports the final state.
    #[arg(long = "require-output")]
    pub require_output: Vec<PathBuf>,

    /// Disable the OS-level filesystem sandbox for the bash tool (macOS
    /// seatbelt / Linux bubblewrap). The sandbox is ON by default and the
    /// harness fails closed at startup when it is unavailable; this flag is
    /// the only escape hatch (design §5, M4.5).
    #[arg(long)]
    pub no_sandbox: bool,

    /// System prompt text. Combined with --skill SKILL.md contents.
    #[arg(long)]
    pub system_prompt: Option<String>,

    /// JSON fixture with scripted responses; required for --provider stub.
    #[arg(long)]
    pub stub_fixture: Option<PathBuf>,

    /// Node instruction. Arguments starting with `@` are expanded from files
    /// (e.g. `@prompt.md`); the rest are used literally. Parts join with a
    /// blank line.
    #[arg(required = true)]
    pub instruction: Vec<String>,
}

/// Expand `@file` arguments and join instruction parts.
pub fn expand_instruction(parts: &[String]) -> anyhow::Result<String> {
    let mut expanded = Vec::with_capacity(parts.len());
    for part in parts {
        if let Some(path) = part.strip_prefix('@') {
            let content = std::fs::read_to_string(path)
                .with_context(|| format!("failed to read prompt file `{path}`"))?;
            expanded.push(content);
        } else {
            expanded.push(part.clone());
        }
    }
    Ok(expanded.join("\n\n"))
}

/// `velites sandbox wrap` arguments: run one command inside the OS sandbox.
/// Kept separate from [`Cli`] so the agent-run CLI stays untouched; main
/// dispatches on the leading `sandbox wrap` tokens before clap sees them.
#[derive(Debug, Parser)]
#[command(
    name = "velites-sandbox-wrap",
    about = "Run a command inside the velites OS sandbox (fail-closed)."
)]
pub struct SandboxWrapCli {
    /// Working directory, read-write inside the sandbox (also the process cwd).
    #[arg(long)]
    pub cwd: PathBuf,

    /// Additional read-only root (repeatable; Linux covers reads via the
    /// read-only `/` bind).
    #[arg(long = "allow-read")]
    pub allow_read: Vec<PathBuf>,

    /// Additional read-write root (repeatable).
    #[arg(long = "allow-write")]
    pub allow_write: Vec<PathBuf>,

    /// Allow outbound+inbound network (denied by default).
    #[arg(long)]
    pub allow_network: bool,

    /// Command to run inside the sandbox (everything after `--`).
    #[arg(required = true, trailing_var_arg = true, allow_hyphen_values = true)]
    pub command: Vec<String>,
}
