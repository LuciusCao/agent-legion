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

    /// Provider key from ~/.velites/models.json (`stub` is fixture-driven).
    #[arg(long)]
    pub provider: String,

    /// Model identifier; the provider/model pair must exist in models.json.
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

    /// Wall-clock bound for the whole run, in seconds. Does NOT cap
    /// individual provider HTTP requests — long generations stream for many
    /// minutes; per-request bounds live in the provider's connect/idle
    /// timeouts (design §7). When the deadline (or any other budget) runs
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
    /// missing files trigger one remediation turn, an `outputs_validation`
    /// event reports the final state, and files still missing when a
    /// non-cancelled run ends make the process exit 1 (output contract).
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

/// `velites models list --json`: machine-readable runtime capability probe.
#[derive(Debug, Parser)]
#[command(name = "velites-models-list")]
pub struct ModelsListCli {
    /// Emit the normalized provider/model array consumed by Agent Worker.
    #[arg(long)]
    pub json: bool,
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
    version,
    about = "Run a command inside the velites OS sandbox (fail-closed)."
)]
pub struct SandboxWrapCli {
    /// Working directory, read-write inside the sandbox (also the process cwd).
    #[arg(long)]
    pub cwd: PathBuf,

    /// Additional read-only root (repeatable).
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn parses_flags_and_joinable_instruction() {
        let cli = Cli::try_parse_from([
            "velites",
            "--provider",
            "stub",
            "--session-dir",
            "/tmp/session",
            "--name",
            "node-7",
            "--skill",
            "/skills/review",
            "--system-prompt",
            "be terse",
            "--tools",
            "read,bash",
            "--thinking",
            "high",
            "--max-turns",
            "3",
            "--max-tokens",
            "1000",
            "--require-output",
            "out/a.json",
            "--require-output",
            "out/b.json",
            "--stub-fixture",
            "/fixtures/two-turns.json",
            "--no-sandbox",
            "do",
            "the",
            "thing",
        ])
        .unwrap();
        assert_eq!(cli.provider, "stub");
        assert_eq!(cli.session_dir.as_deref(), Some(Path::new("/tmp/session")));
        assert_eq!(cli.name.as_deref(), Some("node-7"));
        assert_eq!(cli.skill, vec![PathBuf::from("/skills/review")]);
        assert_eq!(cli.system_prompt.as_deref(), Some("be terse"));
        assert_eq!(cli.tools, vec!["read", "bash"]);
        assert_eq!(cli.thinking.as_deref(), Some("high"));
        assert_eq!(cli.max_turns, Some(3));
        assert_eq!(cli.max_tokens, Some(1000));
        assert_eq!(
            cli.require_output,
            vec![PathBuf::from("out/a.json"), PathBuf::from("out/b.json")]
        );
        assert_eq!(
            cli.stub_fixture.as_deref(),
            Some(Path::new("/fixtures/two-turns.json"))
        );
        assert!(cli.no_sandbox);
        assert_eq!(cli.instruction, vec!["do", "the", "thing"]);
    }

    #[test]
    fn defaults_match_the_documented_surface() {
        let cli = Cli::try_parse_from(["velites", "--provider", "stub", "work"]).unwrap();
        assert_eq!(cli.mode, "json");
        assert_eq!(cli.tools, vec!["read", "write", "bash"]);
        assert_eq!(cli.max_retries, 3);
        assert_eq!(cli.timeout_seconds, 600);
        assert_eq!(cli.max_turns, None);
        assert_eq!(cli.max_tokens, None);
        assert!(cli.require_output.is_empty());
        assert!(cli.skill.is_empty());
        assert!(!cli.no_sandbox);
        assert_eq!(cli.stub_fixture, None);
    }

    #[test]
    fn mode_is_restricted_to_json() {
        // Headless-only: the value_parser allowlist must reject other modes.
        let err = Cli::try_parse_from(["velites", "--provider", "stub", "--mode", "text", "work"])
            .unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::InvalidValue);
    }

    #[test]
    fn instruction_is_required() {
        let err = Cli::try_parse_from(["velites", "--provider", "stub"]).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::MissingRequiredArgument);
    }

    #[test]
    fn provider_is_required() {
        let err = Cli::try_parse_from(["velites", "do work"]).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::MissingRequiredArgument);
    }

    #[test]
    fn unknown_flag_is_a_hard_error() {
        // Silent flag drift is how Pi's --no-* flags rotted (see module
        // docs); velites must fail loudly instead.
        let err = Cli::try_parse_from(["velites", "--provider", "stub", "--verbose", "work"])
            .unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::UnknownArgument);
    }

    #[test]
    fn sandbox_wrap_parses_the_trailing_command() {
        let cli = SandboxWrapCli::try_parse_from([
            "velites-sandbox-wrap",
            "--cwd",
            "/job",
            "--allow-read",
            "/skills/a",
            "--allow-read",
            "/skills/b",
            "--allow-write",
            "/out",
            "--allow-network",
            "--",
            "bash",
            "-c",
            "echo hi",
        ])
        .unwrap();
        assert_eq!(cli.cwd, PathBuf::from("/job"));
        assert_eq!(
            cli.allow_read,
            vec![PathBuf::from("/skills/a"), PathBuf::from("/skills/b")]
        );
        assert_eq!(cli.allow_write, vec![PathBuf::from("/out")]);
        assert!(cli.allow_network);
        assert_eq!(cli.command, vec!["bash", "-c", "echo hi"]);
    }

    #[test]
    fn sandbox_wrap_keeps_hyphenated_command_words() {
        // trailing_var_arg + allow_hyphen_values: flags inside the command
        // stay command words instead of being parsed as wrap flags.
        let cli = SandboxWrapCli::try_parse_from([
            "velites-sandbox-wrap",
            "--cwd",
            "/job",
            "--",
            "python",
            "-u",
            "tool.py",
            "--flag=value",
        ])
        .unwrap();
        assert_eq!(cli.command, vec!["python", "-u", "tool.py", "--flag=value"]);
    }

    #[test]
    fn models_list_json_flag_toggles() {
        let cli = ModelsListCli::try_parse_from(["velites-models-list", "--json"]).unwrap();
        assert!(cli.json);
        let cli = ModelsListCli::try_parse_from(["velites-models-list"]).unwrap();
        assert!(!cli.json);
    }

    #[test]
    fn expand_instruction_joins_parts_with_a_blank_line() {
        let parts = vec!["first".to_string(), "second".to_string()];
        assert_eq!(expand_instruction(&parts).unwrap(), "first\n\nsecond");
    }

    #[test]
    fn expand_instruction_expands_at_file_arguments() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("prompt.md"), "# Prompt\n").unwrap();
        let parts = vec![
            format!("@{}", dir.path().join("prompt.md").display()),
            "and a literal part".to_string(),
        ];
        // File content is injected verbatim (trailing newline included);
        // only the parts themselves are joined with a blank line.
        assert_eq!(
            expand_instruction(&parts).unwrap(),
            "# Prompt\n\n\nand a literal part"
        );
    }

    #[test]
    fn expand_instruction_missing_at_file_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let parts = vec![format!("@{}", dir.path().join("missing.md").display())];
        let err = expand_instruction(&parts).unwrap_err();
        assert!(err.to_string().contains("failed to read prompt file"));
    }
}
