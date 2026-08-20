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
pub mod models;
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
/// harness fault); 1 when declared `--require-output` artifacts are still
/// missing at the end of a non-cancelled run (output contract violation);
/// 2 for harness failures (bad args, fixture errors, I/O, missing gateway
/// credentials).
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

    // Extra read-only roots for the read tool (design §5): the --skill dirs
    // and the session dir — the same locations the OS sandbox allows reads
    // from. Both exist by now (each skill's SKILL.md was loaded above and
    // SessionLog::open created the session dir), so canonicalization matches
    // the sandbox allowlist exactly.
    let mut read_roots = Vec::new();
    for dir in &cli.skill {
        read_roots
            .push(dir.canonicalize().with_context(|| {
                format!("failed to canonicalize skill dir `{}`", dir.display())
            })?);
    }
    if let Some(dir) = &cli.session_dir {
        read_roots.push(
            dir.canonicalize().with_context(|| {
                format!("failed to canonicalize session dir `{}`", dir.display())
            })?,
        );
    }

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

    let model = match cli.model.clone() {
        Some(model) => model,
        None if cli.provider == "stub" => "stub".to_string(),
        None => {
            return Err(anyhow!(
                "--provider {} requires --model <model-id>",
                cli.provider
            ))
        }
    };

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
        read_roots,
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
        provider_name => {
            let path = models::default_path()?;
            if path.exists() {
                let registry = models::load(&path)?;
                let resolved = models::resolve(&registry, provider_name, &config.model)?;
                match resolved.api {
                    models::ApiKind::OpenAiCompletions => {
                        let provider = provider::openai_compat::OpenAiCompatProvider::new(
                            resolved.name,
                            resolved.base_url,
                            resolved.api_key,
                        )?;
                        run_real_provider(config, provider, cli.max_retries, &mut sink).await
                    }
                    models::ApiKind::AnthropicMessages => {
                        let provider = provider::anthropic::AnthropicProvider::new(
                            resolved.name,
                            resolved.base_url,
                            resolved.api_key,
                            resolved.anthropic_version,
                            resolved.model.max_output_tokens,
                            resolved.model.thinking_budgets,
                        )?;
                        run_real_provider(config, provider, cli.max_retries, &mut sink).await
                    }
                }
            } else if matches!(provider_name, "gateway" | "openai_compat") {
                // One-release migration bridge for direct invocations. Worker
                // discovery never advertises this implicit provider because it
                // has no bounded model catalog.
                let credentials = config::resolve()?;
                let provider = provider::openai_compat::OpenAiCompatProvider::new(
                    provider_name.to_string(),
                    credentials.base_url,
                    credentials.api_key,
                )?;
                run_real_provider(config, provider, cli.max_retries, &mut sink).await
            } else {
                Err(anyhow!(
                    "models registry {} does not exist; configure provider/model there",
                    path.display()
                ))
            }
        }
    }
}

async fn run_real_provider<P: provider::Provider>(
    config: agent::AgentConfig,
    provider: P,
    max_retries: u32,
    sink: &mut dyn EventSink,
) -> anyhow::Result<u8> {
    let provider_name = config.provider_name.clone();
    let model = config.model.clone();
    let retrying = provider::retry::RetryProvider::new(
        provider,
        max_retries,
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
    agent::run(config, &retrying, sink).await
}

/// Base delay for the internal exponential backoff on transient provider
/// errors (`base * 2^attempt`). Not a CLI flag on purpose: only the retry
/// COUNT is an operator concern (`--max-retries`).
const DEFAULT_RETRY_BASE_DELAY_MS: u64 = 1_000;

/// `velites sandbox wrap` implementation: build the sandbox policy for one
/// command and run it inside, forwarding the exit code. Fail-closed like the
/// harness itself: an unavailable backend (or an unreadable allowlist root)
/// is an error, never an unsandboxed run.
pub fn run_sandbox_wrap(cli: cli::SandboxWrapCli) -> anyhow::Result<u8> {
    let options = sandbox::WrapOptions {
        read_write: cli.allow_write.clone(),
        read_only: cli.allow_read.clone(),
        allow_network: cli.allow_network,
        ..sandbox::WrapOptions::default()
    };
    let sandbox = sandbox::Sandbox::for_wrap(&cli.cwd, &options)
        .context("filesystem sandbox unavailable (fail-closed)")?;
    let (program, argv) = sandbox.wrap(&cli.command);
    let status = std::process::Command::new(&program)
        .args(&argv)
        .current_dir(&cli.cwd)
        .status()
        .with_context(|| format!("failed to spawn sandboxed command `{program}`"))?;
    Ok(u8::try_from(status.code().unwrap_or(1)).unwrap_or(1))
}
