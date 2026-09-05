//! Bash tool tests: timeout terminates the whole process group (no leftover
//! grandchildren), normal/exit-code paths behave, and the #469 phase timing
//! (spawn / first output byte / steady run / reap) is measured on every
//! path.

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
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
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

#[tokio::test]
async fn bash_guard_blocks_full_disk_scan() {
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "find / -name python3 -type f 2>/dev/null | head"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(output.is_error, "full-disk scan must be rejected");
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(text.contains("blocked by velites guard"), "got: {text}");
    assert!(
        text.contains("command -v"),
        "missing remediation hint: {text}"
    );
}

#[tokio::test]
async fn bash_guard_allows_scoped_find() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("marker.txt"), "x").unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "find . -name marker.txt"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error, "scoped find must run");
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(text.contains("marker.txt"), "missing find output: {text}");
}

#[tokio::test]
async fn bash_timing_covers_spawn_first_byte_rest_on_success() {
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "echo phase-marker"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);

    // #469: the happy path decomposes totalMs ≈ spawnMs + firstByteMs +
    // restMs (reapMs absent on a natural exit). Every phase has a value and
    // each fits inside the total; the phases partition the output window
    // without overlap (restMs starts where firstByteMs ended).
    let timing = output.timing.expect("bash must always report timing");
    let total = timing.total_ms.expect("totalMs");
    let spawn = timing.spawn_ms.expect("spawnMs");
    let first_byte = timing.first_byte_ms.expect("firstByteMs");
    let rest = timing.rest_ms.expect("restMs");
    assert!(timing.reap_ms.is_none(), "no reap on a natural exit");
    // No explicit `timeout` argument → the 120s default ceiling is reported,
    // so the analysis side can join actual vs requested durations.
    assert_eq!(
        timing.requested_timeout_ms,
        Some(120_000),
        "default timeout must be reported as requestedTimeoutMs"
    );
    assert!(
        spawn <= total && first_byte <= total && rest <= total,
        "phases ({spawn}+{first_byte}+{rest}) must fit inside totalMs ({total})"
    );
    assert!(
        spawn + first_byte + rest <= total,
        "phases ({spawn}+{first_byte}+{rest}) must not overlap within totalMs ({total})"
    );
    assert_eq!(
        output.output_bytes,
        "phase-marker\n".len() as u64,
        "incremental read must collect every output byte"
    );
}

#[tokio::test]
async fn bash_timing_reports_the_requested_timeout_ceiling() {
    // #469: the enforced (clamped) `timeout` argument surfaces as
    // requestedTimeoutMs — the analysis side's explanation variable for
    // long-tail variance (models raise `timeout` after consecutive
    // failures). Explicit values pass through; the clamp bounds absurd
    // ones at the 1h ceiling.
    let dir = tempfile::tempdir().unwrap();
    let explicit = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "true", "timeout": 30}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!explicit.is_error);
    assert_eq!(
        explicit.timing.as_ref().unwrap().requested_timeout_ms,
        Some(30_000),
        "explicit timeout must be reported as requestedTimeoutMs"
    );

    let clamped = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "true", "timeout": 1_000_000_000}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!clamped.is_error);
    assert_eq!(
        clamped.timing.as_ref().unwrap().requested_timeout_ms,
        Some(3_600_000),
        "absurd timeout must report the enforced 1h clamp, not the raw ask"
    );
}

#[tokio::test]
async fn bash_timing_first_byte_separates_prelude_from_steady_run() {
    // A command that sleeps BEFORE printing stretches the prelude phase
    // (spawn → first output byte); a command that prints first and sleeps
    // after stretches the steady-run phase instead. This pins the #469
    // observation axis: a stall in the child's prelude (bash parsing,
    // heredoc write, interpreter startup) lands in firstByteMs, not restMs.
    let dir = tempfile::tempdir().unwrap();
    let late_output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "sleep 1; echo late", "timeout": 30}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!late_output.is_error);
    let late = late_output.timing.expect("bash must always report timing");
    let late_first = late.first_byte_ms.expect("firstByteMs (late output)");
    let late_rest = late.rest_ms.expect("restMs (late output)");

    let early_output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "echo early; sleep 1", "timeout": 30}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!early_output.is_error);
    let early = early_output.timing.expect("bash must always report timing");
    let early_first = early.first_byte_ms.expect("firstByteMs (early output)");
    let early_rest = early.rest_ms.expect("restMs (early output)");

    assert!(
        late_first >= 900,
        "prelude of sleep-then-print must dominate firstByteMs: {late_first}ms"
    );
    assert!(
        late_rest < 900,
        "sleep-then-print must have a short restMs (first byte → exit): {late_rest}ms"
    );
    assert!(
        early_first < 900,
        "print-then-sleep must have a short firstByteMs: {early_first}ms"
    );
    assert!(
        early_rest >= 900,
        "print-then-sleep must stretch restMs (first byte → exit): {early_rest}ms"
    );
}

#[tokio::test]
async fn bash_timing_first_byte_takes_the_earlier_stream() {
    // P1 fix: firstByteMs is the EARLIER of the two streams' first bytes, not
    // stdout's whenever stdout has one. A stderr-first child (echo to stderr,
    // sleep, then stdout) must attribute the fast prelude to firstByteMs and
    // the stall to restMs — a stdout-first `.or()` would mis-bucket the
    // steady-run stall into the prelude and invert the #469 diagnosis.
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({
                "command": "echo warn >&2; sleep 1; echo out",
                "timeout": 30
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
        text.contains("[stderr]") && text.contains("warn"),
        "stderr missing: {text}"
    );
    assert!(text.contains("out"), "stdout missing: {text}");

    let timing = output.timing.expect("bash must always report timing");
    let first_byte = timing
        .first_byte_ms
        .expect("firstByteMs (stderr-first child)");
    let rest = timing.rest_ms.expect("restMs (stderr-first child)");
    // stderr fires almost immediately: the 1s sleep is the steady run.
    assert!(
        first_byte < 900,
        "stderr-first child must have a short firstByteMs: {first_byte}ms"
    );
    assert!(
        rest >= 900,
        "the post-first-byte sleep must land in restMs, not firstByteMs: {rest}ms"
    );
}

#[tokio::test]
async fn bash_timing_no_output_child_skips_first_byte() {
    // A child that never writes to stdout/stderr has no first byte: the
    // phase is skipped on the wire (None), restMs covers the whole wait.
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "sleep 0.2"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let timing = output.timing.expect("bash must always report timing");
    assert!(timing.total_ms.is_some());
    assert!(timing.spawn_ms.is_some());
    assert!(
        timing.first_byte_ms.is_none(),
        "no-output child must skip firstByteMs"
    );
    // No first byte: restMs covers the whole output window (the sleep).
    assert!(
        timing.rest_ms.unwrap() >= 150,
        "restMs must cover the whole wait"
    );
    assert!(timing.reap_ms.is_none());
}

#[tokio::test]
async fn bash_timeout_path_reports_reap_phase() {
    // The timeout path must still emit timing, with reapMs present (TERM →
    // grace → KILL → reaped) and restMs ending at the timeout: the kill and
    // reap are accounted as reapMs, never inside restMs.
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "sleep 300", "timeout": 1}),
            &ctx(dir.path()),
        )
        .await;
    assert!(output.is_error);
    let timing = output.timing.expect("timing on the timeout path");
    assert!(timing.total_ms.is_some());
    assert!(timing.spawn_ms.is_some());
    assert!(
        timing.first_byte_ms.is_none(),
        "silent sleeper has no first byte"
    );
    let rest = timing.rest_ms.expect("restMs on the timeout path");
    let reap = timing
        .reap_ms
        .expect("timeout path must report the reap phase");
    // restMs ends at the 1s timeout; the TERM → grace(3s) → KILL → reap
    // sequence lives in reapMs, so each phase stays in its own bucket. The
    // reap can be sub-millisecond when sleep dies to SIGTERM instantly —
    // the point is that the grace window stays bounded, not that it hits
    // an exact duration.
    assert!(
        rest < 5_000,
        "restMs must stay bounded by the timeout: {rest}ms"
    );
    assert!(
        rest + reap < 10_000,
        "rest ({rest}) + reap ({reap}) must stay bounded by the timeout + grace window"
    );
}

#[tokio::test]
async fn bash_unmeasured_error_paths_carry_no_timing() {
    // The RequestTiming convention: failures raised BEFORE any measurement
    // surface as error content with `timing: None` — the dispatch boundary
    // (ToolKind::execute) must not stamp a totalMs onto them. Both guards
    // here fail before spawn, so nothing was measured.
    let dir = tempfile::tempdir().unwrap();

    let missing_command = ToolKind::Bash
        .execute(&serde_json::json!({}), &ctx(dir.path()))
        .await;
    assert!(missing_command.is_error);
    assert!(
        missing_command.timing.is_none(),
        "argument-validation failure must not carry timing"
    );

    let guard_rejected = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "find / -name x 2>/dev/null | head"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(guard_rejected.is_error, "full-disk scan must be rejected");
    assert!(
        guard_rejected.timing.is_none(),
        "guard rejection must not carry timing"
    );
}

#[tokio::test]
async fn bash_measured_error_paths_keep_their_timing() {
    // Contrast with the unmeasured guards above: the timeout path IS an
    // error, but it carries a full phase set (spawn/firstByte/rest/reap +
    // requestedTimeoutMs) and still receives its totalMs from the dispatch
    // boundary — the `is_error && timing.is_none()` exemption must not
    // swallow measured errors.
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "sleep 300", "timeout": 1}),
            &ctx(dir.path()),
        )
        .await;
    assert!(output.is_error);
    let timing = output.timing.expect("measured error keeps its timing");
    assert!(
        timing.total_ms.is_some(),
        "dispatch boundary still fills totalMs on measured errors"
    );
    assert!(timing.spawn_ms.is_some());
    assert!(timing.rest_ms.is_some());
    assert!(timing.reap_ms.is_some());
    assert_eq!(timing.requested_timeout_ms, Some(1_000));
}

#[tokio::test]
async fn bash_timing_heredoc_prelude_is_measured() {
    // The blocked shape from #469: `python3 - <<'EOF' ... EOF`. The heredoc
    // write happens inside the child bash BEFORE python3 starts producing
    // output, so the whole script (heredoc write + interpreter startup +
    // first print) lands inside firstByteMs. This is the exact observation
    // point the instrumentation exists for.
    let dir = tempfile::tempdir().unwrap();
    let script = "for i in range(1, 6):\n    print(f\"line-{i}\")";
    let command = format!("python3 - <<'EOF'\n{script}\nEOF");
    // python3 may not exist on minimal CI hosts; fall back to a pure-bash
    // heredoc with the same shape.
    let use_python = std::process::Command::new("bash")
        .arg("-c")
        .arg("command -v python3 >/dev/null 2>&1")
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    let command = if use_python {
        command
    } else {
        let bash_script = "for i in $(seq 1 5); do echo line-$i; done";
        format!("bash -s <<'EOF'\n{bash_script}\nEOF")
    };
    let output = ToolKind::Bash
        .execute(&serde_json::json!({"command": command}), &ctx(dir.path()))
        .await;
    assert!(!output.is_error, "command failed: {:?}", output.content);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    for i in 1..=5 {
        assert!(text.contains(&format!("line-{i}")), "output: {text}");
    }
    // Output collection is unchanged: every line survived the incremental
    // read (no dropped head/tail chunks).
    let timing = output.timing.expect("bash must always report timing");
    assert!(
        timing.first_byte_ms.is_some(),
        "heredoc shape must report the prelude phase"
    );
    assert!(timing.spawn_ms.is_some());
}
