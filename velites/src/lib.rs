//! velites — Agent Legion lightweight agent harness.
//!
//! See docs/architecture/velites-harness.md. The library surface exists so
//! integration tests (and future embedding) can drive the agent loop without
//! spawning the binary.

pub mod agent;
pub mod budget;
pub mod cancel;
pub mod cli;
pub mod config;
pub mod events;
pub mod provider;
pub mod sandbox;
pub mod session;
pub mod skill;
pub mod tools;

use anyhow::{anyhow, Context};

use crate::cli::Cli;
use crate::events::{EventSink, StdoutJsonlSink};
use crate::tools::ToolKind;

/// Run one headless session from parsed CLI args. Returns the process exit
/// code: 0 even for unrecovered model errors (Pi semantics) and for
/// SIGTERM-cancelled runs (cancellation is a deliberate Host action, not a
/// harness fault); non-zero only for harness failures (bad args, fixture
/// errors, I/O).
pub async fn run(cli: Cli) -> anyhow::Result<u8> {
    let cwd = std::env::current_dir()
        .context("failed to resolve current directory")?
        .canonicalize()
        .context("failed to canonicalize current directory")?;

    let mut tools = Vec::new();
    for name in &cli.tools {
        match ToolKind::from_name(name) {
            Some(kind) if !tools.contains(&kind) => tools.push(kind),
            Some(_) => {}
            None => {
                return Err(anyhow!(
                    "unknown tool `{name}` in --tools (available: read,write,bash)"
                ))
            }
        }
    }

    // Explicit context assembly: --system-prompt + --skill SKILL.md files.
    // Nothing else is ever read into the context (zero auto-discovery).
    let mut system_parts = Vec::new();
    if let Some(system_prompt) = &cli.system_prompt {
        system_parts.push(system_prompt.clone());
    }
    for dir in &cli.skill {
        system_parts.push(skill::load_skill(dir)?);
    }
    let system_prompt = system_parts.join("\n\n");

    let instruction = cli::expand_instruction(&cli.instruction)?;

    let session = match &cli.session_dir {
        Some(dir) => Some(session::SessionLog::open(dir)?),
        None => None,
    };

    // OS-level filesystem sandbox (design §5, M4.5). Fail-closed: when the
    // bash tool is enabled and the sandbox backend is unavailable, startup
    // fails here — before the agent loop — instead of degrading to an
    // unsandboxed run. `--no-sandbox` is the only escape hatch. Without the
    // bash tool the sandbox has no enforcement point and is skipped.
    let sandbox = if cli.no_sandbox || !tools.contains(&ToolKind::Bash) {
        None
    } else {
        Some(std::sync::Arc::new(
            sandbox::Sandbox::new(&cwd, cli.session_dir.as_deref(), &cli.skill).context(
                "filesystem sandbox unavailable (fail-closed); pass --no-sandbox to bypass",
            )?,
        ))
    };

    let model = cli
        .model
        .clone()
        .unwrap_or_else(|| match cli.provider.as_str() {
            "stub" => "stub".to_string(),
            _ => "unknown".to_string(),
        });

    let config = agent::AgentConfig {
        name: cli.name.clone(),
        provider_name: cli.provider.clone(),
        model,
        thinking: cli.thinking.clone(),
        system_prompt,
        instruction,
        tools,
        budget: budget::Budget::new(
            cli.max_turns,
            cli.max_tokens,
            std::time::Duration::from_secs(cli.timeout_seconds),
        ),
        require_output: cli.require_output.clone(),
        session,
        cwd,
        sandbox,
        cancel: cancel::CancelToken::install_sigterm_handler(),
    };

    let mut sink = StdoutJsonlSink::new();
    match cli.provider.as_str() {
        "stub" => {
            let fixture = cli
                .stub_fixture
                .as_ref()
                .ok_or_else(|| anyhow!("--provider stub requires --stub-fixture <path>"))?;
            let provider = provider::stub::StubProvider::from_fixture(fixture)?;
            agent::run(config, &provider, &mut sink).await
        }
        "gateway" | "openai_compat" => {
            let credentials = config::resolve()?;
            // No HTTP-layer total timeout: long generations stream for many
            // minutes; the run wall-clock budget (cli.timeout_seconds, §5)
            // lives in the agent loop's deadline, not here.
            let provider = provider::openai_compat::OpenAiCompatProvider::new(
                cli.provider.clone(),
                credentials.base_url,
                credentials.api_key,
            )?;
            // Pi-compatible retry observability: each failed transient
            // attempt emits `message_end`(error) + `auto_retry_start` before
            // the backoff sleep, straight to stdout. The sink is stateless,
            // and ordering against the agent loop's sink is guaranteed
            // because the loop is awaiting this call while the callback runs.
            let provider_name = config.provider_name.clone();
            let model = config.model.clone();
            let retrying = provider::retry::RetryProvider::new(
                provider,
                cli.max_retries,
                std::time::Duration::from_millis(DEFAULT_RETRY_BASE_DELAY_MS),
            )
            .with_on_attempt_failed(move |attempt, max_attempts, delay, err| {
                let events = events::retry_attempt_events(
                    &provider_name,
                    &model,
                    attempt,
                    max_attempts,
                    delay.as_millis() as u64,
                    &err.to_string(),
                );
                let mut sink = StdoutJsonlSink::new();
                for event in &events {
                    sink.emit(event);
                }
            });
            agent::run(config, &retrying, &mut sink).await
        }
        other => Err(anyhow!(
            "unknown provider `{other}`; available: stub, gateway, openai_compat"
        )),
    }
}

/// Base delay for the internal exponential backoff on transient provider
/// errors (`base * 2^attempt`). Not a CLI flag on purpose: only the retry
/// COUNT is an operator concern (`--max-retries`).
const DEFAULT_RETRY_BASE_DELAY_MS: u64 = 1_000;
