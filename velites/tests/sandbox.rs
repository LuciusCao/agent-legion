//! Sandbox tests: paths escaping the working directory (`../`, absolute,
//! symlink) are rejected before any filesystem effect.

use velites::tools::{resolve_in_cwd, ToolContext, ToolKind};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ctx_with_read_roots(cwd, &[])
}

fn ctx_with_read_roots(cwd: &std::path::Path, read_roots: &[std::path::PathBuf]) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // These tests cover the in-process canonicalize sandbox of the
        // read/write tools, which is independent of the OS-level bash sandbox.
        sandbox: None,
        read_roots: read_roots.to_vec(),
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

    // Line range: 1-based offset + limit. The file has more lines after the
    // selected range, so a continuation notice follows (design §8, pi-aligned).
    let ranged = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "sub/dir/file.txt", "offset": 2, "limit": 1}),
            &ctx(dir.path()),
        )
        .await;
    assert_eq!(
        result_text(&ranged),
        "line2\n\n[1 more lines in file. Use offset=3 to continue.]"
    );
}

#[tokio::test]
async fn resolve_in_cwd_accepts_dot_segments_inside() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir(dir.path().join("sub")).unwrap();
    let canonical = dir.path().canonicalize().unwrap();
    let resolved = resolve_in_cwd(&canonical, "sub/../ok.txt").unwrap();
    assert_eq!(resolved, canonical.join("ok.txt"));
}

// --- Read-only roots (--skill dirs, session dir; design §5): the read tool
// may resolve into them, escapes out of them are rejected, and the write
// tool never honors them. ---

/// A job dir, a sibling skill dir, and a sibling session dir, with
/// `read_roots` covering the latter two (canonicalized, like lib.rs does).
fn job_skill_session() -> (
    tempfile::TempDir,
    std::path::PathBuf,
    std::path::PathBuf,
    std::path::PathBuf,
) {
    let dir = tempfile::tempdir().unwrap();
    let job = dir.path().join("job");
    let skill = dir.path().join("skill");
    let session = dir.path().join("session");
    std::fs::create_dir(&job).unwrap();
    std::fs::create_dir(&skill).unwrap();
    std::fs::create_dir(&session).unwrap();
    (dir, job, skill, session)
}

#[tokio::test]
async fn read_allows_absolute_paths_inside_skill_and_session_dirs() {
    let (_dir, job, skill, session) = job_skill_session();
    std::fs::write(skill.join("SKILL.md"), "skill doc").unwrap();
    std::fs::create_dir(skill.join("references")).unwrap();
    std::fs::write(skill.join("references/data.json"), "{\"k\": 1}").unwrap();
    std::fs::write(session.join("notes.txt"), "session notes").unwrap();

    let roots = vec![
        skill.canonicalize().unwrap(),
        session.canonicalize().unwrap(),
    ];
    let ctx = ctx_with_read_roots(&job, &roots);

    for (path, expected) in [
        (skill.join("SKILL.md"), "skill doc"),
        (skill.join("references/data.json"), "{\"k\": 1}"),
        (session.join("notes.txt"), "session notes"),
    ] {
        let output = ToolKind::Read
            .execute(&serde_json::json!({"path": path.to_string_lossy()}), &ctx)
            .await;
        assert!(
            !output.is_error,
            "read of {} failed: {}",
            path.display(),
            result_text(&output)
        );
        assert_eq!(result_text(&output), expected);
    }
}

#[tokio::test]
async fn read_rejects_parent_escape_out_of_skill_dir() {
    let (dir, job, skill, _session) = job_skill_session();
    std::fs::write(dir.path().join("secret.txt"), "secret").unwrap();

    let ctx = ctx_with_read_roots(&job, &[skill.canonicalize().unwrap()]);
    let escaping = format!("{}/../secret.txt", skill.display());
    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": escaping}), &ctx)
        .await;
    assert!(output.is_error, "`..` out of a skill dir must be rejected");
    assert!(result_text(&output).contains("sandbox"));
}

#[cfg(unix)]
#[tokio::test]
async fn read_rejects_symlink_escape_out_of_skill_dir() {
    let (dir, job, skill, _session) = job_skill_session();
    let outside = dir.path().join("outside");
    std::fs::create_dir(&outside).unwrap();
    std::fs::write(outside.join("secret.txt"), "secret").unwrap();
    std::os::unix::fs::symlink(&outside, skill.join("link")).unwrap();

    let ctx = ctx_with_read_roots(&job, &[skill.canonicalize().unwrap()]);
    let via_symlink = format!("{}/link/secret.txt", skill.display());
    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": via_symlink}), &ctx)
        .await;
    assert!(output.is_error, "symlink escape must be rejected");
    assert!(result_text(&output).contains("sandbox"));
}

#[tokio::test]
async fn read_still_rejects_paths_outside_every_root() {
    let (_dir, job, skill, _session) = job_skill_session();
    let ctx = ctx_with_read_roots(&job, &[skill.canonicalize().unwrap()]);

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "/etc/hosts"}), &ctx)
        .await;
    assert!(output.is_error);
    assert!(result_text(&output).contains("sandbox"));
}

#[tokio::test]
async fn write_rejects_skill_dir_even_when_it_is_a_read_root() {
    let (_dir, job, skill, _session) = job_skill_session();
    let ctx = ctx_with_read_roots(&job, &[skill.canonicalize().unwrap()]);
    let target = skill.join("evil.txt");

    let output = ToolKind::Write
        .execute(
            &serde_json::json!({"path": target.to_string_lossy(), "content": "pwned"}),
            &ctx,
        )
        .await;
    assert!(
        output.is_error,
        "write to a read-only root must be rejected"
    );
    assert!(result_text(&output).contains("sandbox"));
    assert!(!target.exists());
}
