use std::process::{Command, Output};

fn run(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(args)
        .output()
        .expect("failed to spawn velites")
}

#[test]
fn agent_run_fallback_preserves_help_and_version() {
    for flag in ["--help", "-h"] {
        let output = run(&[flag]);
        assert_eq!(output.status.code(), Some(0), "{flag}: {output:?}");
        assert!(String::from_utf8_lossy(&output.stdout).contains("Usage: velites"));
        assert!(output.stderr.is_empty(), "{flag}: {output:?}");
    }
    for flag in ["--version", "-V"] {
        let output = run(&[flag]);
        assert_eq!(output.status.code(), Some(0), "{flag}: {output:?}");
        assert!(String::from_utf8_lossy(&output.stdout).starts_with("velites "));
        assert!(output.stderr.is_empty(), "{flag}: {output:?}");
    }
}

#[test]
fn preparse_help_uses_the_existing_subcommand_argv_offsets() {
    for args in [
        &["models", "list", "--help"][..],
        &["tools", "list", "--help"][..],
        &["sandbox", "wrap", "--help"][..],
        &["validate", "--help"][..],
    ] {
        let output = run(args);
        assert_eq!(output.status.code(), Some(0), "{args:?}: {output:?}");
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("Usage: velites"),
            "{args:?}: {output:?}"
        );
        assert!(output.stderr.is_empty(), "{args:?}: {output:?}");
    }
}

#[test]
fn preparse_version_flags_keep_their_existing_surface() {
    for args in [
        &["sandbox", "wrap", "--version"][..],
        &["validate", "--version"][..],
    ] {
        let output = run(args);
        assert_eq!(output.status.code(), Some(0), "{args:?}: {output:?}");
        assert!(
            String::from_utf8_lossy(&output.stdout).contains(env!("CARGO_PKG_VERSION")),
            "{args:?}: {output:?}"
        );
        assert!(output.stderr.is_empty(), "{args:?}: {output:?}");
    }
    for args in [
        &["models", "list", "--version"][..],
        &["tools", "list", "--version"][..],
    ] {
        let output = run(args);
        assert_eq!(output.status.code(), Some(2), "{args:?}: {output:?}");
        assert!(
            String::from_utf8_lossy(&output.stderr).contains("unexpected argument '--version'"),
            "{args:?}: {output:?}"
        );
    }
}

#[test]
fn preparse_errors_keep_exit_codes_and_messages() {
    for (args, message) in [
        (
            &["models", "describe"][..],
            "error: expected `velites models list --json`\n",
        ),
        (
            &["models", "list"][..],
            "error: `velites models list` requires --json\n",
        ),
        (
            &["tools", "describe"][..],
            "error: expected `velites tools list --json`\n",
        ),
        (
            &["tools", "list"][..],
            "error: `velites tools list` requires --json\n",
        ),
        (
            &["sandbox", "unwrap"][..],
            "error: expected `velites sandbox wrap --cwd <dir> -- <cmd...>`\n",
        ),
    ] {
        let output = run(args);
        assert_eq!(output.status.code(), Some(2), "{args:?}: {output:?}");
        assert_eq!(String::from_utf8_lossy(&output.stderr), message, "{args:?}");
        assert!(output.stdout.is_empty(), "{args:?}: {output:?}");
    }

    let output = run(&["validate"]);
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--job-dir <JOB_DIR>"), "{stderr}");
    assert!(stderr.contains("Usage: velites"), "{stderr}");
}

#[test]
fn unmatched_argv_still_runs_the_agent_cli() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(
        dir.path().join("fixture.json"),
        r#"{"responses":[{"content":[{"type":"text","text":"done"}],"stopReason":"stop"}]}"#,
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args([
            "--provider",
            "stub",
            "--stub-fixture",
            "fixture.json",
            "--no-sandbox",
            "do",
            "work",
        ])
        .current_dir(dir.path())
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(0), "{output:?}");
    assert!(output.stderr.is_empty(), "{output:?}");
    assert!(String::from_utf8_lossy(&output.stdout).contains("\"type\":\"agent_end\""));
}
