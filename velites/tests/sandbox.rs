//! Sandbox tests: paths escaping the working directory (`../`, absolute,
//! symlink) are rejected before any filesystem effect.

use velites::tools::{resolve_in_cwd, ToolContext, ToolKind};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // These tests cover the in-process canonicalize sandbox of the
        // read/write tools, which is independent of the OS-level bash sandbox.
        sandbox: None,
    }
}

fn result_text(output: &velites::tools::ToolOutput) -> String {
    match &output.content[0] {
        velites::events::ContentBlock::Text { text } => text.clone(),
        other => panic!("expected text content, got {other:?}"),
    }
}

#[tokio::test]
async fn read_rejects_parent_escape() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();
    std::fs::write(dir.path().join("outside.txt"), "secret").unwrap();

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "../outside.txt"}), &ctx(&job))
        .await;
    assert!(output.is_error);
    assert!(
        result_text(&output).contains("sandbox"),
        "expected sandbox error: {}",
        result_text(&output)
    );
}

#[tokio::test]
async fn read_rejects_absolute_escape() {
    let dir = tempfile::tempdir().unwrap();
    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "/etc/hosts"}), &ctx(dir.path()))
        .await;
    assert!(output.is_error);
    assert!(result_text(&output).contains("sandbox"));
}

#[tokio::test]
async fn write_rejects_parent_escape_without_side_effects() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    std::fs::create_dir(&job).unwrap();

    let output = ToolKind::Write
        .execute(
            &serde_json::json!({"path": "../evil.txt", "content": "pwned"}),
            &ctx(&job),
        )
        .await;
    assert!(output.is_error);
    assert!(result_text(&output).contains("sandbox"));
    assert!(!dir.path().join("evil.txt").exists());
}

#[tokio::test]
async fn write_rejects_symlink_escape() {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    let outside = dir.path().join("outside");
    std::fs::create_dir(&job).unwrap();
    std::fs::create_dir(&outside).unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, job.join("link")).unwrap();

    let output = ToolKind::Write
        .execute(
            &serde_json::json!({"path": "link/evil.txt", "content": "pwned"}),
            &ctx(&job),
        )
        .await;
    assert!(output.is_error, "symlink escape must be rejected");
    assert!(!outside.join("evil.txt").exists());
}

#[tokio::test]
async fn read_and_write_inside_cwd_work() {
    let dir = tempfile::tempdir().unwrap();

    let write_out = ToolKind::Write
        .execute(
            &serde_json::json!({"path": "sub/dir/file.txt", "content": "line1\nline2\nline3"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(
        !write_out.is_error,
        "write failed: {}",
        result_text(&write_out)
    );
    assert_eq!(write_out.output_bytes, "line1\nline2\nline3".len() as u64);
    // Atomic write: no tmp file left behind.
    assert!(!dir.path().join("sub/dir/file.velites-tmp").exists());

    let read_out = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "sub/dir/file.txt"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!read_out.is_error);
    assert_eq!(result_text(&read_out), "line1\nline2\nline3");

    // Line range: 1-based offset + limit.
    let ranged = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "sub/dir/file.txt", "offset": 2, "limit": 1}),
            &ctx(dir.path()),
        )
        .await;
    assert_eq!(result_text(&ranged), "line2");
}

#[tokio::test]
async fn resolve_in_cwd_accepts_dot_segments_inside() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir(dir.path().join("sub")).unwrap();
    let canonical = dir.path().canonicalize().unwrap();
    let resolved = resolve_in_cwd(&canonical, "sub/../ok.txt").unwrap();
    assert_eq!(resolved, canonical.join("ok.txt"));
}
