//! Regression tests for the #922 / #942 / #715 tool-sandbox hardening:
//!
//! - the bash child sees only an allowlisted environment (#922 R-4);
//! - the bash tool returns promptly when background processes outlive the
//!   command, and leaves no leftovers in its process group (#942);
//! - the write/json tools' temp files cannot be redirected (#922 R-2);
//! - Linux bubblewrap: the bash tool runs in a private pid namespace
//!   (#922 R-1) and, unless `--allow-network`, a private network namespace
//!   (#715). Gated on `bwrap` availability like `os_sandbox.rs` (the CI
//!   rust lane installs bubblewrap).

use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use velites::tools::{ToolContext, ToolKind};

const CANARY_VAR: &str = "VELITES_TEST_HARDENING_CANARY";
const CANARY_VALUE: &str = "hardening-canary-7f3c";

fn ctx(cwd: &Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
    }
}

fn text_of(output: &velites::tools::ToolOutput) -> String {
    match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    }
}

/// Stub-provider fixture: one assistant message running `commands` as bash
/// tool calls, then a final text response.
fn write_bash_fixture(job: &Path, commands: &[&str]) {
    let calls: Vec<serde_json::Value> = commands
        .iter()
        .map(|command| {
            serde_json::json!({"type": "toolCall", "name": "bash", "arguments": {"command": command}})
        })
        .collect();
    let fixture = serde_json::json!({
        "responses": [
            {"content": calls},
            {"content": [{"type": "text", "text": "done"}]}
        ]
    });
    std::fs::write(job.join("fixture.json"), fixture.to_string()).unwrap();
    std::fs::write(job.join("prompt.md"), "Run the commands.").unwrap();
}

/// Run the real binary on the bash fixture with the canary variable set in
/// the harness environment; returns `(isError, text)` per bash call.
fn run_bash_session(job: &Path, extra_args: &[&str]) -> Vec<(bool, String)> {
    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(["--mode", "json", "--provider", "stub"])
        .args(["--stub-fixture", "fixture.json"])
        .args(extra_args)
        .args(["@prompt.md", "Execute the attached node instructions."])
        .env(CANARY_VAR, CANARY_VALUE)
        .current_dir(job)
        .output()
        .expect("failed to spawn velites");
    assert!(
        output.status.success(),
        "run failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
        .filter(|event| event["type"] == "tool_execution_end")
        .map(|event| {
            let text = event["result"]["content"][0]["text"].as_str().unwrap_or("");
            (event["isError"].as_bool().unwrap(), text.to_string())
        })
        .collect()
}

#[test]
fn bash_child_environment_is_allowlisted() {
    let dir = tempfile::tempdir().unwrap();
    write_bash_fixture(
        dir.path(),
        &[
            // Positive control: the allowlisted basics are still there.
            "test -n \"$PATH\" && test -n \"$HOME\"",
            // The harness-only variable never reaches the shell.
            "test -z \"${VELITES_TEST_HARDENING_CANARY:-}\"",
            "! env | grep -q hardening-canary",
        ],
    );
    let results = run_bash_session(dir.path(), &["--no-sandbox"]);
    assert_eq!(results.len(), 3, "{results:?}");
    for (is_error, text) in &results {
        assert!(!is_error, "allowlist check failed: {text}");
    }
}

#[cfg(unix)]
#[tokio::test]
async fn bash_returns_promptly_and_reaps_background_leftovers() {
    let dir = tempfile::tempdir().unwrap();
    let pf = dir.path().join("pgid");
    let command = format!("echo $$ > '{}'; sleep 300 & echo done", pf.display());
    let started = Instant::now();
    let output = ToolKind::Bash
        .execute(&serde_json::json!({"command": command}), &ctx(dir.path()))
        .await;
    let elapsed = started.elapsed();
    let text = text_of(&output);
    assert!(!output.is_error, "unexpected error: {text}");
    assert!(text.contains("done"), "missing output: {text}");
    assert!(elapsed < Duration::from_secs(10), "took {elapsed:?}");

    let pgid: i32 = std::fs::read_to_string(&pf)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    let mut gone = false;
    for _ in 0..40 {
        let result = unsafe { libc::killpg(pgid, 0) };
        if result != 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            gone = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(gone, "background leftovers in group {pgid} survived");
}

/// Poll until `pid` no longer exists (ESRCH); false if it survives ~4s.
#[cfg(unix)]
async fn process_gone(pid: i32) -> bool {
    for _ in 0..40 {
        let result = unsafe { libc::kill(pid, 0) };
        if result != 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    false
}

#[cfg(unix)]
#[tokio::test]
async fn bash_kills_leftovers_that_ignore_term_and_closed_the_pipes() {
    // A leftover in the group that ignores SIGTERM and holds no pipe must
    // still be gone when the tool call returns (final group KILL).
    let dir = tempfile::tempdir().unwrap();
    let pf = dir.path().join("leftover");
    let command = format!(
        "(trap '' TERM; exec sleep 300) >/dev/null 2>&1 & echo $! > '{}'; echo done",
        pf.display()
    );
    let output = ToolKind::Bash
        .execute(&serde_json::json!({"command": command}), &ctx(dir.path()))
        .await;
    let text = text_of(&output);
    assert!(!output.is_error, "unexpected error: {text}");
    let pid: i32 = std::fs::read_to_string(&pf)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert!(process_gone(pid).await, "TERM-ignoring leftover {pid} survived");
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn bash_drain_is_bounded_when_a_process_leaves_the_group() {
    // A process in its own session still holds the output pipes: the drain
    // must stop at the grace bound, and the process — no longer part of the
    // command's group — is not signalled.
    let dir = tempfile::tempdir().unwrap();
    let pf = dir.path().join("escaped");
    let command = format!(
        "setsid sh -c 'echo $$ > {}; exec sleep 20' & echo done",
        pf.display()
    );
    let started = Instant::now();
    let output = ToolKind::Bash
        .execute(&serde_json::json!({"command": command}), &ctx(dir.path()))
        .await;
    let elapsed = started.elapsed();
    let text = text_of(&output);
    assert!(text.contains("done"), "missing output: {text}");
    assert!(
        text.contains("Background processes kept the output open"),
        "missing drain note: {text}"
    );
    assert!(elapsed < Duration::from_secs(10), "took {elapsed:?}");
    let pid: i32 = std::fs::read_to_string(&pf)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    let alive = unsafe { libc::kill(pid, 0) } == 0;
    unsafe { libc::kill(pid, libc::SIGKILL) };
    assert!(alive, "a process outside the group must not be signalled");
}

#[cfg(unix)]
#[tokio::test]
async fn write_and_json_tools_ignore_a_planted_tmp_symlink() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    let outside = dir.path().join("outside.txt");
    std::fs::write(&outside, "untouched").unwrap();
    std::fs::write(job.join("data.json"), r#"{"a": 1}"#).unwrap();
    for name in ["out.md.velites-tmp", "data.json.velites-tmp"] {
        std::os::unix::fs::symlink(&outside, job.join(name)).unwrap();
    }

    let write = ToolKind::Write
        .execute(
            &serde_json::json!({"path": "out.md", "content": "payload"}),
            &ctx(&job),
        )
        .await;
    assert!(!write.is_error, "write failed: {}", text_of(&write));
    let set = ToolKind::Json
        .execute(
            &serde_json::json!({"op": "set", "path": "data.json", "query": "a", "value": 2}),
            &ctx(&job),
        )
        .await;
    assert!(!set.is_error, "json set failed: {}", text_of(&set));

    assert_eq!(std::fs::read_to_string(&outside).unwrap(), "untouched");
    assert_eq!(
        std::fs::read_to_string(job.join("out.md")).unwrap(),
        "payload"
    );
    let raw = std::fs::read_to_string(job.join("data.json")).unwrap();
    let data: serde_json::Value = serde_json::from_str(&raw).unwrap();
    assert_eq!(data["a"], 2);
}

#[cfg(target_os = "linux")]
fn bwrap_available() -> bool {
    Command::new("bwrap")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(target_os = "linux")]
#[test]
fn linux_bash_sandbox_has_private_pid_and_network_namespaces() {
    if !bwrap_available() {
        eprintln!("bwrap unavailable; skipping bubblewrap isolation test");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    write_bash_fixture(
        dir.path(),
        &[
            // Private pid namespace: pid 1 is the sandbox's own init.
            "grep -q bwrap /proc/1/cmdline",
            // No process outside the sandbox is visible through /proc.
            "! cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n' | grep -q hardening-canary",
            // Private network namespace: loopback only.
            "test \"$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ')\" = lo",
        ],
    );
    let results = run_bash_session(dir.path(), &[]);
    assert_eq!(results.len(), 3, "{results:?}");
    for (is_error, text) in &results {
        assert!(!is_error, "isolation check failed: {text}");
    }
}

#[cfg(target_os = "linux")]
#[test]
fn linux_bash_sandbox_allow_network_shares_only_the_network() {
    if !bwrap_available() {
        eprintln!("bwrap unavailable; skipping bubblewrap isolation test");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    write_bash_fixture(
        dir.path(),
        &[
            // The pid namespace stays private with network allowed.
            "grep -q bwrap /proc/1/cmdline",
            // Host interfaces beyond loopback are visible.
            "tail -n +3 /proc/net/dev | grep -qv '^ *lo:'",
        ],
    );
    let results = run_bash_session(dir.path(), &["--allow-network"]);
    assert_eq!(results.len(), 2, "{results:?}");
    for (is_error, text) in &results {
        assert!(!is_error, "network opt-in check failed: {text}");
    }
}
