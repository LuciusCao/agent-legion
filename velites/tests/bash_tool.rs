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

#[tokio::test]
async fn bash_truncates_long_output_keeping_tail_and_temp_file() {
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "seq 1 3000"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    // Tail is kept: the run's last lines survive, the head is dropped.
    assert!(text.contains("3000"), "tail kept: {text}");
    assert!(!text.contains("\n500\n"), "head dropped: {text}");
    assert!(
        text.contains("[Showing lines 1001-3000 of 3000. Full output: "),
        "missing notice: {text}"
    );
    // The notice points at a velites-bash temp file holding the full output.
    let marker = "Full output: ";
    let path = text[text.rfind(marker).unwrap() + marker.len()..]
        .trim_end_matches(']')
        .trim()
        .to_string();
    assert!(
        std::path::Path::new(&path)
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("velites-bash"),
        "unexpected temp file name: {path}"
    );
    let full = std::fs::read_to_string(&path).unwrap();
    assert!(full.contains("1\n2\n3"), "full output incomplete: {path}");
    assert!(full.contains("3000"), "full output incomplete: {path}");
    std::fs::remove_file(&path).ok();
    // output_bytes measures the pre-truncation stdout ("1\n".."3000\n").
    assert!(output.output_bytes > text.len() as u64);
}

#[tokio::test]
async fn bash_truncates_by_bytes_with_limit_note() {
    let dir = tempfile::tempdir().unwrap();
    // 600 lines × 100 bytes ≈ 60KB > 50KB, under the 2000-line limit.
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({
                "command": "for i in $(seq 1 600); do printf 'a%.0s' $(seq 1 100); echo; done"
            }),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(text.contains("(50KB limit)"), "missing byte note: {text}");
    assert!(text.contains("Full output: "), "missing temp path: {text}");
    // No partial lines in the normal byte-truncation path.
    let shown = text.split("\n\n[Showing lines").next().unwrap();
    assert!(shown.lines().all(|l| l.len() == 100), "split line: {shown}");
}

#[tokio::test]
async fn bash_single_huge_line_keeps_partial_tail() {
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({
                "command": "echo start; head -c 60000 /dev/zero | tr '\\0' 'y'; echo"
            }),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(
        text.contains("[Showing last 50.0KB of line 2 (line is 58.6KB)."),
        "missing partial-line note: {text}"
    );
    assert!(!text.contains("start"), "head should be dropped: {text}");
}
