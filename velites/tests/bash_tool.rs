//! Bash tool tests: timeout terminates the whole process group (no leftover
//! grandchildren), and normal/exit-code paths behave.

use std::time::Duration;

use velites::tools::{ToolContext, ToolKind};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // No OS sandbox here: these tests exercise bash semantics (timeout,
        // exit codes, output capture), not confinement; the sandbox has its
        // own integration tests (tests/os_sandbox.rs) and CI's Linux lane
        // has no bwrap.
        sandbox: None,
    }
}

#[tokio::test]
async fn bash_captures_stdout_and_stderr() {
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "echo out; echo err 1>&2"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(text.contains("out"), "missing stdout: {text}");
    assert!(text.contains("[stderr]"), "missing stderr marker: {text}");
    assert!(text.contains("err"), "missing stderr: {text}");
    assert!(output.output_bytes >= 8); // "out\n" + "err\n"
}

#[tokio::test]
async fn bash_nonzero_exit_is_error() {
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "echo partial; exit 3"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(text.contains("partial"));
    assert!(text.contains("Exit code: 3"), "missing exit code: {text}");
}

#[cfg(unix)]
#[tokio::test]
async fn bash_timeout_kills_whole_process_group() {
    let dir = tempfile::tempdir().unwrap();
    let pgid_file = dir.path().join("pgid");
    let alive_file = dir.path().join("alive");
    // $$ of the spawned bash is its pid; process_group(0) makes it the pgid.
    // The background sleep shares that process group and must die with it.
    let command = format!(
        "echo $$ > '{}'; (sleep 300; touch '{}') & sleep 300",
        pgid_file.display(),
        alive_file.display()
    );
    let started = std::time::Instant::now();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": command, "timeout": 1}),
            &ctx(dir.path()),
        )
        .await;
    let elapsed = started.elapsed();

    assert!(output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(text.contains("timed out"), "missing timeout note: {text}");
    // timeout(1s) + grace(3s) upper bound, generous slack.
    assert!(
        elapsed < Duration::from_secs(15),
        "took too long: {elapsed:?}"
    );

    let pgid: i32 = std::fs::read_to_string(&pgid_file)
        .unwrap()
        .trim()
        .parse()
        .unwrap();

    // The whole group (leader + background sleep) must be gone: signal 0
    // probes for existence; ESRCH means nothing in the group survived.
    let mut gone = false;
    for _ in 0..40 {
        let result = unsafe { libc::killpg(pgid, 0) };
        if result != 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            gone = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(gone, "process group {pgid} survived the timeout kill");
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert!(
        !alive_file.exists(),
        "background child kept running after the group kill"
    );
}
