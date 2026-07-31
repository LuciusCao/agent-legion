//! velites — Agent Legion lightweight agent harness.
//!
//! See docs/architecture/velites-harness.md. The library surface exists so
//! integration tests (and future embedding) can drive the agent loop without
//! spawning the binary.

pub mod agent;
pub mod cli;
pub mod events;
pub mod provider;
pub mod session;
pub mod skill;
pub mod tools;

use anyhow::{anyhow, Context};

use crate::cli::Cli;
use crate::events::StdoutJsonlSink;
use crate::tools::ToolKind;

/// Run one headless session from parsed CLI args. Returns the process exit
/// code: 0 even for unrecovered model errors (Pi semantics); non-zero only
/// for harness failures (bad args, fixture errors, I/O).
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
        max_turns: cli.max_turns,
        max_tokens: cli.max_tokens,
        require_output: cli.require_output.clone(),
        session,
        cwd,
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
        other => Err(anyhow!(
            "provider `{other}` is not implemented yet (M2); available: stub"
        )),
    }
}
