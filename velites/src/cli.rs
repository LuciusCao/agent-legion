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

    /// Provider name. M1 implements `stub` (fixture-driven, no LLM); the
    /// OpenAI-compatible `gateway` provider lands in M2.
    #[arg(long)]
    pub provider: String,

    /// Model identifier passed to the provider and reported in message events.
    #[arg(long)]
    pub model: Option<String>,

    /// Thinking/reasoning effort; mapped per provider (ignored by stub).
    #[arg(long)]
    pub thinking: Option<String>,

    /// Maximum agent turns (assistant completions) before the loop ends.
    #[arg(long)]
    pub max_turns: Option<u32>,

    /// Maximum cumulative tokens (input+output+cacheRead) before the loop ends.
    #[arg(long)]
    pub max_tokens: Option<u64>,

    /// Files that must exist when the run ends. Repeatable. Parsed in M1;
    /// the enforcement/remediation semantics land in M3.
    #[arg(long = "require-output")]
    pub require_output: Vec<PathBuf>,

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
