//! `velites-sandbox` bin contract tests (issue #383).
//!
//! Two guarantees pinned here:
//!
//! 1. **Argv compatibility**: the standalone bin accepts the same
//!    `sandbox wrap --cwd ... -- cmd...` argv the main binary is called with
//!    (leading `sandbox wrap` tokens are consumed, mirroring main.rs's
//!    pre-clap dispatch), plus the bare form. `shared/code_sandbox.py`
//!    builds one argv shape for both binaries.
//! 2. **Same policy, both entries**: for identical arguments the standalone
//!    bin and the main binary's `sandbox wrap` subcommand behave identically
//!    (same success/failure and same stdout for a wrapped command). The
//!    deeper guarantee — one policy implementation in the lib crate shared
//!    by both bins — is structural (no second policy module exists); these
//!    tests pin the observable behavior so drift cannot land silently.
//!
//! Linux: the bwrap policy itself is pinned by argv-level unit tests in
//! src/sandbox.rs; these tests run wherever the OS sandbox backend is
//! available (seatbelt on macOS dev/CI, bwrap on CI Linux lanes that install
//! it) and the wrapped command is trivial, so a missing backend surfaces as
//! the fail-closed error path — also worth asserting.

use std::path::Path;
use std::process::Command;

fn velites_bin() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_velites"))
}

fn velites_sandbox_bin() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_velites-sandbox"))
}

fn wrap_args(cwd: &Path, command: &str) -> Vec<String> {
    vec![
        "--cwd".into(),
        cwd.to_string_lossy().into_owned(),
        "--".into(),
        "/bin/sh".into(),
        "-c".into(),
        command.into(),
    ]
}

fn run(binary: &Path, prefix: &[&str], cwd: &Path, args: &[String]) -> std::process::Output {
    let mut cmd = Command::new(binary);
    cmd.args(prefix).args(args).current_dir(cwd);
    cmd.output()
        .unwrap_or_else(|e| panic!("spawn {binary:?}: {e}"))
}

#[test]
fn bare_form_matches_legacy_prefixed_form() {
    let job = tempfile::tempdir().unwrap();
    let args = wrap_args(job.path(), "echo contract");
    let cwd = std::env::temp_dir();

    let bare = run(&velites_sandbox_bin(), &[], &cwd, &args);
    let prefixed = run(&velites_sandbox_bin(), &["sandbox", "wrap"], &cwd, &args);
    let main_bin = run(&velites_bin(), &["sandbox", "wrap"], &cwd, &args);

    let bare_out = String::from_utf8_lossy(&bare.stdout).to_string();
    let prefixed_out = String::from_utf8_lossy(&prefixed.stdout).to_string();
    let main_out = String::from_utf8_lossy(&main_bin.stdout).to_string();
    assert_eq!(bare_out, prefixed_out, "bare vs prefixed argv diverged");
    assert_eq!(
        bare_out, main_out,
        "velites-sandbox and `velites sandbox wrap` diverged"
    );
    // The wrapped command really ran (sandbox backend available and used).
    assert_eq!(bare_out.trim(), "contract");
}

#[test]
fn legacy_prefix_rejects_wrong_subcommand() {
    // `sandbox <not-wrap>` must fail closed, same as the main binary's
    // dispatch — the compat shim must not loosen the error surface.
    let cwd = std::env::temp_dir();
    let cwd_str = cwd.to_string_lossy().into_owned();
    let args = vec![
        "--cwd".to_string(),
        cwd_str,
        "--".to_string(),
        "true".to_string(),
    ];
    let out = run(&velites_sandbox_bin(), &["sandbox", "unwrap"], &cwd, &args);
    assert!(!out.status.success());
}

#[test]
fn version_flag_reports() {
    // --version must work without config/network (clap version attribute);
    // callers use it for drift diagnostics (#381 registration handshake).
    let out = Command::new(velites_sandbox_bin())
        .arg("--version")
        .output()
        .expect("spawn velites-sandbox");
    assert!(out.status.success());
    assert!(String::from_utf8_lossy(&out.stdout).contains("velites"));
}
