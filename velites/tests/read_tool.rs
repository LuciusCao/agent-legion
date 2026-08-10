//! Read tool tests: pi-aligned head truncation (2000 lines / 50KB) and the
//! continuation notices.

use velites::tools::{ToolContext, ToolKind};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // Same rationale as tests/bash_tool.rs: these tests exercise read
        // semantics, not confinement.
        sandbox: None,
        read_roots: Vec::new(),
    }
}

fn result_text(output: &velites::tools::ToolOutput) -> String {
    match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    }
}

#[tokio::test]
async fn read_truncates_by_lines_and_offers_offset() {
    let dir = tempfile::tempdir().unwrap();
    let content: String = (1..=2500).map(|i| format!("line {i}\n")).collect();
    std::fs::write(dir.path().join("big.txt"), &content).unwrap();

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "big.txt"}), &ctx(dir.path()))
        .await;
    assert!(!output.is_error);
    let text = result_text(&output);
    assert!(text.contains("line 1\n"), "head kept: {text}");
    assert!(!text.contains("line 2001"), "tail dropped: {text}");
    assert!(
        text.contains("[Showing lines 1-2000 of 2500. Use offset=2001 to continue.]"),
        "missing notice: {text}"
    );
    // output_bytes measures the pre-truncation selection (the joined lines;
    // the file's trailing newline is not a line).
    assert_eq!(output.output_bytes, content.trim_end().len() as u64);
}

#[tokio::test]
async fn read_truncates_by_bytes_with_limit_note() {
    let dir = tempfile::tempdir().unwrap();
    // 600 lines × 100 bytes ≈ 60KB > 50KB, under the 2000-line limit.
    let line = "a".repeat(100);
    let content = (0..600)
        .map(|_| line.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    std::fs::write(dir.path().join("wide.txt"), &content).unwrap();

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "wide.txt"}), &ctx(dir.path()))
        .await;
    assert!(!output.is_error);
    let text = result_text(&output);
    assert!(text.contains("(50KB limit)"), "missing byte note: {text}");
    assert!(
        text.contains("Use offset=") && text.contains("to continue."),
        "missing continuation: {text}"
    );
    // No partial lines: every shown line is complete.
    let shown = text.split("\n\n[Showing lines").next().unwrap();
    assert!(shown.lines().all(|l| l.len() == 100), "split line: {shown}");
}

#[tokio::test]
async fn read_first_line_over_50kb_points_at_sed_fallback() {
    let dir = tempfile::tempdir().unwrap();
    let content = format!("{}\nshort\n", "x".repeat(60 * 1024));
    std::fs::write(dir.path().join("huge-line.txt"), &content).unwrap();

    let output = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "huge-line.txt"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = result_text(&output);
    assert!(
        text.contains("exceeds 50KB limit")
            && text.contains("sed -n '1p' huge-line.txt | head -c 51200"),
        "missing fallback hint: {text}"
    );
}

#[tokio::test]
async fn read_user_limit_with_remaining_file_offers_offset() {
    let dir = tempfile::tempdir().unwrap();
    let content: String = (1..=10).map(|i| format!("line {i}\n")).collect();
    std::fs::write(dir.path().join("small.txt"), &content).unwrap();

    let output = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "small.txt", "limit": 4}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    let text = result_text(&output);
    assert!(text.contains("line 4"), "limit applied: {text}");
    assert!(!text.contains("line 5"), "limit applied: {text}");
    assert!(
        text.contains("[6 more lines in file. Use offset=5 to continue.]"),
        "missing remaining notice: {text}"
    );
}

#[tokio::test]
async fn read_small_file_is_not_truncated() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("tiny.txt"), "alpha\nbeta\n").unwrap();

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "tiny.txt"}), &ctx(dir.path()))
        .await;
    assert!(!output.is_error);
    assert_eq!(result_text(&output), "alpha\nbeta");
}

#[tokio::test]
async fn read_huge_limit_does_not_overflow() {
    // Regression: a model-supplied u64::MAX limit must not overflow
    // `start + limit` (debug panic / release wrap → slice panic, exit 101
    // without an `agent_end` event).
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("small.txt"), "alpha\nbeta\n").unwrap();

    let output = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "small.txt", "limit": u64::MAX}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    assert_eq!(result_text(&output), "alpha\nbeta");
}

#[tokio::test]
async fn read_offset_past_eof_with_huge_limit_selects_nothing() {
    // Empty selection (start == total_file_lines) must not index past the
    // line vector, even combined with a saturating limit.
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("small.txt"), "alpha\nbeta\n").unwrap();

    let output = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "small.txt", "offset": 100, "limit": u64::MAX}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!output.is_error);
    assert_eq!(result_text(&output), "");
}
