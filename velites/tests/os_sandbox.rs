//! OS-level filesystem sandbox tests (design §5, M4.5, EXEC-HARNESS-SANDBOX-001).
//!
//! - fail-closed: an unavailable backend (probed via PATH) aborts startup
//!   with a non-zero exit before the agent loop; `--no-sandbox` is the only
//!   escape hatch.
//! - macOS seatbelt integration: a stub-provider session drives the bash tool
//!   against $HOME (denied), the job dir / session dir (allowed) and a
//!   `--skill` dir (read-only).
//! - Linux bubblewrap integration: gated on `bwrap` availability (the CI rust
//!   lane installs bubblewrap, see .github/workflows/quality-gate.yml; local
//!   runs without bwrap skip).

use std::path::{Path, PathBuf};
use std::process::Command;

fn write(path: &Path, content: &str) {
    std::fs::write(path, content).expect("failed to write test file");
}

fn run_velites(cwd: &Path, args: &[String]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_velites"))
        .args(args)
        .current_dir(cwd)
        .output()
        .expect("failed to spawn velites")
}

fn parse_events(stdout: &[u8]) -> Vec<serde_json::Value> {
    String::from_utf8_lossy(stdout)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("each stdout line must be valid JSON"))
        .collect()
}

fn tool_end_errors(events: &[serde_json::Value]) -> Vec<bool> {
    events
        .iter()
        .filter(|event| event["type"] == "tool_execution_end")
        .map(|event| event["isError"].as_bool().expect("isError must be a bool"))
        .collect()
}

/// Base args for a stub-provider session (sandbox ON unless --no-sandbox is
/// appended by the caller).
fn base_args(job: &Path, session: &Path, skill: &Path) -> Vec<String> {
    vec![
        "--mode".into(),
        "json".into(),
        "--provider".into(),
        "stub".into(),
        "--stub-fixture".into(),
        job.join("fixture.json").to_string_lossy().into_owned(),
        "--session-dir".into(),
        session.to_string_lossy().into_owned(),
        "--skill".into(),
        skill.to_string_lossy().into_owned(),
        "@prompt.md".into(),
    ]
}

/// Fixture: one assistant message with the given bash commands as tool calls,
/// then a final text response.
fn write_bash_fixture(job: &Path, commands: &[String]) {
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
    write(
        &job.join("fixture.json"),
        &serde_json::to_string_pretty(&fixture).unwrap(),
    );
}

fn home_probe() -> PathBuf {
    PathBuf::from(std::env::var("HOME").expect("HOME must be set")).join(".velites_sandbox_probe")
}

/// The read-only fixture uses only the read tool, so a successful run never
/// spawns bash (which the emptied PATH would break).
fn write_read_fixture(job: &Path) {
    write(
        &job.join("fixture.json"),
        r#"{
  "responses": [
    {"content": [{"type": "toolCall", "name": "read", "arguments": {"path": "prompt.md"}}]},
    {"content": [{"type": "text", "text": "done"}]}
  ]
}"#,
    );
}

#[cfg(unix)]
#[test]
fn sandbox_unavailable_fails_closed_before_agent_loop() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    write(&job.join("prompt.md"), "Hi.");
    write_read_fixture(&job);
    // An empty PATH makes the backend probe (sandbox-exec / bwrap) fail.
    let empty_bin = dir.path().join("empty-bin");
    std::fs::create_dir(&empty_bin).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args([
            "--mode",
            "json",
            "--provider",
            "stub",
            "--stub-fixture",
            "fixture.json",
            "@prompt.md",
        ])
        .current_dir(&job)
        .env("PATH", &empty_bin)
        .output()
        .expect("failed to spawn velites");

    assert!(
        !output.status.success(),
        "sandbox-unavailable run must fail closed"
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("sandbox"),
        "stderr must name the sandbox: {stderr}"
    );
    assert!(
        stderr.contains("--no-sandbox"),
        "stderr must name the escape hatch: {stderr}"
    );
    // Fail-closed means BEFORE the agent loop: no events were emitted.
    assert!(
        !String::from_utf8_lossy(&output.stdout).contains("agent_start"),
        "agent loop must not start without the sandbox"
    );
}

#[cfg(unix)]
#[test]
fn no_sandbox_flag_bypasses_unavailable_sandbox() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    write(&job.join("prompt.md"), "Hi.");
    write_read_fixture(&job);
    let empty_bin = dir.path().join("empty-bin");
    std::fs::create_dir(&empty_bin).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_velites"))
        .args([
            "--mode",
            "json",
            "--provider",
            "stub",
            "--stub-fixture",
            "fixture.json",
            "@prompt.md",
            "--no-sandbox",
        ])
        .current_dir(&job)
        .env("PATH", &empty_bin)
        .output()
        .expect("failed to spawn velites");

    assert!(
        output.status.success(),
        "--no-sandbox must bypass the sandbox probe: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = parse_events(&output.stdout);
    assert_eq!(events.last().unwrap()["type"], "agent_end");
}

#[cfg(target_os = "macos")]
#[test]
fn macos_seatbelt_blocks_escape_and_allows_job_session_skill() {
    // Fixture dirs must live OUTSIDE $TMPDIR and /tmp (both read-write roots
    // with subpath grants): the parent-chain list-only assertions below are
    // only meaningful when no whitelisted subtree covers the fixture dirs.
    let dir = tempfile::tempdir_in("/var/tmp").unwrap();
    let job = dir.path().join("job");
    let session = dir.path().join("session");
    let skill = dir.path().join("skill");
    std::fs::create_dir(&job).unwrap();
    std::fs::create_dir(&skill).unwrap();
    write(&job.join("prompt.md"), "Run the commands.");
    write(&skill.join("SKILL.md"), "You are a test skill.");
    // A file NEXT TO the whitelist roots: visible in a parent listing, but
    // its contents must stay unreadable (list-only, not subpath).
    let secret = dir.path().join("secret.txt");
    write(&secret, "top secret");
    let probe = home_probe();

    let commands = vec![
        // 1. Reading outside the allowed roots ($HOME) is denied.
        "ls \"$HOME\" >/dev/null 2>&1".to_string(),
        // 2. Writing outside the allowed roots is denied.
        format!("echo pwned > '{}'", probe.display()),
        // 3. Read/write inside the job dir works.
        "echo hi > ok.txt && cat ok.txt".to_string(),
        // 4. The --skill dir stays readable.
        format!("cat '{}'", skill.join("SKILL.md").display()),
        // 5. The session dir is writable.
        format!(
            "echo session-ok > '{}'",
            session.join("extra.txt").display()
        ),
        // 6. The parent chain of the whitelist roots is listable: the agent
        //    sees the roots exist instead of a misleading EPERM/empty dir.
        format!(
            "ls '{0}' | grep -q '^job$' && ls '{0}' | grep -q '^skill$'",
            dir.path().display()
        ),
        // 7. …but list-only is not read: file contents next to the roots
        //    stay denied.
        format!("cat '{}'", secret.display()),
    ];
    write_bash_fixture(&job, &commands);

    let output = run_velites(&job, &base_args(&job, &session, &skill));
    // Clean up immediately if a regression let the escape write through.
    let leaked = probe.exists();
    if leaked {
        let _ = std::fs::remove_file(&probe);
    }

    assert!(
        output.status.success(),
        "run failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = parse_events(&output.stdout);
    assert_eq!(
        tool_end_errors(&events),
        vec![true, true, false, false, false, false, true],
        "escape attempts must fail, allowed operations must succeed: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(!leaked, "sandbox let a write escape into $HOME");
    assert_eq!(
        std::fs::read_to_string(job.join("ok.txt")).unwrap().trim(),
        "hi"
    );
    assert!(session.join("extra.txt").exists());
}

#[cfg(target_os = "linux")]
#[test]
fn linux_bwrap_blocks_reads_and_writes_outside_allowed_roots() {
    let bwrap_available = Command::new("bwrap")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    if !bwrap_available {
        eprintln!("bwrap unavailable; skipping bubblewrap integration test");
        return;
    }

    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    let session = dir.path().join("session");
    let skill = dir.path().join("skill");
    std::fs::create_dir(&job).unwrap();
    std::fs::create_dir(&skill).unwrap();
    write(&job.join("prompt.md"), "Run the commands.");
    write(&skill.join("SKILL.md"), "You are a test skill.");
    let probe = home_probe();

    let commands = vec![
        // 1. Writes outside the allowed roots fail.
        format!("echo pwned > '{}'", probe.display()),
        // 2. Reads outside the allowed roots ($HOME) are denied: with
        //    selective binds $HOME simply does not exist in the namespace.
        "ls \"$HOME\" >/dev/null 2>&1".to_string(),
        // 3. Reads off the whitelisted system roots still work.
        "cat /etc/os-release >/dev/null".to_string(),
        // 4. The job dir stays writable.
        "echo hi > ok.txt".to_string(),
        // 5. The session dir stays writable.
        format!(
            "echo session-ok > '{}'",
            session.join("extra.txt").display()
        ),
    ];
    write_bash_fixture(&job, &commands);

    let output = run_velites(&job, &base_args(&job, &session, &skill));
    let leaked = probe.exists();
    if leaked {
        let _ = std::fs::remove_file(&probe);
    }

    assert!(
        output.status.success(),
        "run failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = parse_events(&output.stdout);
    assert_eq!(
        tool_end_errors(&events),
        vec![true, true, false, false, false],
        "escape attempts must fail, allowed operations must succeed: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(!leaked, "sandbox let a write escape into $HOME");
    assert!(session.join("extra.txt").exists());
}
