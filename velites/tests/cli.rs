use clap::Parser;
use std::path::{Path, PathBuf};
use velites::cli::{expand_instruction, Cli, ModelsListCli, SandboxWrapCli};

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
        "--allow-network",
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
    assert!(cli.allow_network);
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
    assert_eq!(cli.max_output_tokens, None);
    assert!(cli.require_output.is_empty());
    assert!(cli.skill.is_empty());
    assert!(!cli.no_sandbox);
    assert!(!cli.allow_network);
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
    let err =
        Cli::try_parse_from(["velites", "--provider", "stub", "--verbose", "work"]).unwrap_err();
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
