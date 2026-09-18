//! Bash tool capture-cap tests (#637): the per-stream 4 MiB in-memory
//! capture cap — head-kept/tail-dropped semantics, per-stream isolation,
//! under-the-cap behavior, and the display-layer edge case where nothing
//! is showable. Split out of `bash_tool.rs` when that file passed 800
//! lines (#689 codex round 3, #563 sibling-file convention).

use velites::tools::{ToolContext, ToolKind};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // No OS sandbox here: these tests exercise output-capture semantics,
        // not confinement; the sandbox has its own integration tests
        // (tests/os_sandbox.rs) and CI's Linux lane has no bwrap.
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
    }
}

// The per-stream capture cap (#637) — 5 MiB of stdout against the 4 MiB
// in-memory limit. Reading from /dev/zero keeps the fixture constant-size
// (no multi-MB string literals in the binary or on disk); `tr` turns it
// into 'a's, `head -c` bounds the total volume. 5 MiB is comfortably past
// the cap while staying CI-friendly.
#[tokio::test]
async fn bash_output_over_capture_cap_keeps_head_drops_tail() {
    let dir = tempfile::tempdir().unwrap();
    let total = 5 * 1024 * 1024usize;
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({
                "command": "echo start; head -c 5242880 /dev/zero | tr '\\0' 'a'; echo; echo end"
            }),
            &ctx(dir.path()),
        )
        .await;
    // The command itself succeeded — capping is not an error.
    assert!(!output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };

    // output_bytes keeps the FULL-stream semantics: stdout "start\n" +
    // 5 MiB of 'a' + "\n" + "end\n" (the [stderr] marker is display-only).
    assert_eq!(
        output.output_bytes,
        (total + b"start\n\nend\n".len()) as u64
    );

    // The HEAD is kept: the marker line and the head of the 'a' run.
    assert!(text.starts_with("start\n"), "head kept: {text}");
    // The TAIL is dropped: nothing past the cap may appear.
    assert!(!text.contains("end"), "tail must be dropped: {text}");
    // The capped notice names the cap, says the tail was dropped, and —
    // unlike the tail-truncation path — offers NO full-output file (the
    // dropped tail no longer exists; a path would be a lie). It points at
    // redirection + chunked reads instead.
    assert!(
        text.contains("[Output capture stopped after "),
        "missing cap notice: {text}"
    );
    assert!(text.contains("4MB"), "missing cap value: {text}");
    // #689 review P2: the remediation must point at CHUNKED BASH reads
    // (`sed`/`tail` windows), never at the read tool's offset/limit — read
    // rejects over-cap whole files BEFORE offset/limit are applied, so that
    // hint would send the model into a redirect → read-error loop.
    assert!(
        text.contains("tail was dropped") && text.contains("Rerun with output redirected"),
        "missing remediation hint: {text}"
    );
    assert!(
        text.contains("read it in chunks with bash")
            && text.contains("sed -n '1,2000p' out.log")
            && text.contains("tail -n +2001 out.log"),
        "remediation must name concrete bash chunk commands: {text}"
    );
    assert!(
        !text.contains("the read tool's offset/limit"),
        "must not point at the read tool (it rejects over-cap files): {text}"
    );
    assert!(
        !text.contains("Full output: "),
        "capped run must not point at a full-output temp file: {text}"
    );
    // Display layer still applies on top: the kept head is cut to the
    // first 2000 lines / 50KB by head truncation (the 'a' run alone is
    // 5 MiB), so the shown content stays bounded.
    assert!(text.len() < 60 * 1024, "shown content must stay small");
    // The notice reports the full 5.0MB volume.
    assert!(
        text.contains("5.0MB"),
        "notice must report full volume: {text}"
    );
}

// Cap semantics with stderr: the per-stream isolation, the runaway stream
// itself, and timing must all survive the cap.
#[tokio::test]
async fn bash_stderr_over_capture_cap_is_counted_and_flagged() {
    let dir = tempfile::tempdir().unwrap();
    let total = 5 * 1024 * 1024usize;
    let output = ToolKind::Bash
        .execute(
            &serde_json::json!({
                "command": "echo out; head -c 5242880 /dev/zero | tr '\\0' 'e' 1>&2; echo done"
            }),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };

    // Full volume: stdout "out\ndone\n" + stderr 5 MiB of 'e'.
    assert_eq!(output.output_bytes, (total + b"out\ndone\n".len()) as u64);
    // The cap is PER-STREAM: stdout ("out\ndone\n") is tiny and survives
    // intact even though stderr blew past its own cap.
    assert!(text.contains("out"), "stdout head must be kept: {text}");
    assert!(
        text.contains("done"),
        "small stdout survives intact: {text}"
    );
    // The stderr head was kept (display-bounded) and the cap notice fired.
    assert!(
        text.contains("[stderr]"),
        "stderr head must be kept: {text}"
    );
    assert!(
        text.contains("[Output capture stopped after 5.0MB at the 4MB per-stream cap"),
        "missing cap notice: {text}"
    );
    // #469 semantics survive the cap: the first byte (stdout "out\n")
    // fired long before any cap, so the phase is still measured.
    let timing = output.timing.expect("bash must always report timing");
    assert!(timing.first_byte_ms.is_some());
}

// Under the cap nothing changes — full output, no cap notice, and the
// regular truncation paths keep working.
#[tokio::test]
async fn bash_output_under_capture_cap_is_unchanged() {
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
    assert!(
        !text.contains("[Output capture stopped"),
        "no cap notice under the cap: {text}"
    );
    assert!(text.contains("3000"), "tail kept: {text}");
}

// Cap + display edge case: the first output line alone exceeds the 50KB
// display limit, so nothing can be shown. The notice must NOT claim "the
// head above is kept" (there is nothing above it) — it names the display
// limit as the reason no content is shown and keeps the remediation hint.
#[tokio::test]
async fn bash_capped_output_with_unshowable_first_line_names_display_limit() {
    let dir = tempfile::tempdir().unwrap();
    let total = 5 * 1024 * 1024usize;
    let output = ToolKind::Bash
        .execute(
            // One giant unterminated line (no newline before the tail), so
            // the kept head is a single line > 50KB → nothing displayable.
            &serde_json::json!({
                "command": "head -c 5242880 /dev/zero | tr '\\0' 'z'"
            }),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error, "capping is not an error");
    assert_eq!(output.output_bytes, total as u64);
    let text = match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    };
    assert!(
        text.contains(
            "the first line alone exceeds the 50KB display limit, so no content is shown"
        ),
        "notice must name the display limit instead of claiming a kept head: {text}"
    );
    assert!(
        !text.contains("the head above is kept"),
        "nothing is shown — the notice must not claim a kept head: {text}"
    );
    // No leaked giant line either: the notice stands alone.
    assert!(
        text.len() < 1024,
        "notice-only output must stay tiny: {text}"
    );
    assert!(
        text.contains("Rerun with output redirected"),
        "remediation hint survives: {text}"
    );
    // Same P2 discipline as the showable-head case above: the chunked-read
    // hint must stay bash-based, never the read tool's offset/limit.
    assert!(
        text.contains("read it in chunks with bash") && text.contains("sed -n '1,2000p' out.log"),
        "remediation must name a concrete bash chunk command: {text}"
    );
    assert!(
        !text.contains("the read tool's offset/limit"),
        "must not point at the read tool (it rejects over-cap files): {text}"
    );
    assert!(
        !text.contains("Full output: "),
        "capped run must not point at a full-output temp file: {text}"
    );
}
