//! Read tool tests: pi-aligned head truncation (2000 lines / 50KB) and the
//! continuation notices.

use std::os::fd::FromRawFd;

use velites::tools::{ToolContext, ToolKind};

fn ctx(cwd: &std::path::Path) -> ToolContext {
    ToolContext {
        cwd: cwd.canonicalize().unwrap(),
        cancel: velites::cancel::CancelToken::default(),
        // Same rationale as tests/bash_tool.rs: these tests exercise read
        // semantics, not confinement.
        sandbox: None,
        read_roots: Vec::new(),
        skill_dirs: Vec::new(),
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

#[tokio::test]
async fn read_oversized_file_is_rejected_before_loading() {
    // #637: the whole-file read_to_string is bounded — an over-cap file
    // fails fast at the metadata check (a 5 MiB file against the 4 MiB
    // limit), before any byte is read into memory, and the error tells the
    // model to switch to chunked bash reads. A file just under the cap
    // still loads normally (the limit is inclusive of exactly 4 MiB).
    let dir = tempfile::tempdir().unwrap();
    let mut content = vec![b'x'; 5 * 1024 * 1024];
    content.push(b'\n');
    std::fs::write(dir.path().join("huge.txt"), &content).unwrap();

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "huge.txt"}), &ctx(dir.path()))
        .await;
    assert!(output.is_error, "over-cap file must be rejected");
    let text = result_text(&output);
    assert!(
        text.contains("too large"),
        "missing size-limit error: {text}"
    );
    assert!(
        text.contains("sed -n '1,2000p' huge.txt"),
        "missing chunked-read hint: {text}"
    );
    // #689 review P2: the read tool's own over-cap hint must also stay
    // bash-based — it is the other half of the recovery loop (a >4 MiB
    // redirected log cannot come back through read's offset/limit either).
    assert!(
        text.contains("Read it in chunks via bash"),
        "the over-cap hint must name the bash path: {text}"
    );

    // Exactly at the cap: allowed (the error is strictly greater-than).
    let dir = tempfile::tempdir().unwrap();
    let at_cap = vec![b'y'; 4 * 1024 * 1024];
    std::fs::write(dir.path().join("cap.txt"), &at_cap).unwrap();
    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "cap.txt"}), &ctx(dir.path()))
        .await;
    assert!(!output.is_error, "exactly-at-cap file must load");
}

// #689 review P1: the metadata().len() pre-check is only a fast path for
// regular files — a FIFO reports len() == 0 forever, so the old unbounded
// read_to_string sailed past the cap straight to EOF (the OOM line this
// issue exists to hold). The cap must be enforced by the READ ITSELF
// (take(cap+1)); this test drives the read tool against a named pipe
// carrying more than the 4 MiB cap and pins the rejection.
//
// FIFO plumbing via libc (same dependency the bash tests already use for
// killpg): mkfifo, open the write end NONBLOCK so the read side's open does
// not deadlock, then spawn a thread doing a blocking write of 5 MiB. The
// tool call must fail with the too-large error — not hang, not buffer 5 MiB.
#[cfg(unix)]
#[tokio::test]
async fn read_fifo_over_cap_is_rejected_by_the_bounded_read() {
    let dir = tempfile::tempdir().unwrap();
    let fifo = dir.path().join("pipe.txt");
    let cpath = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);

    // Total volume the writer will push: comfortably past the 4 MiB cap.
    let total = 5 * 1024 * 1024usize;
    let writer_fifo = fifo.clone();
    let writer = std::thread::spawn(move || {
        use std::io::Write;
        // O_WRONLY|O_NONBLOCK against an already-open reader (the tool call
        // below) succeeds immediately; without a reader it would fail ENXIO.
        let flags = libc::O_WRONLY | libc::O_NONBLOCK;
        let cpath = std::ffi::CString::new(writer_fifo.as_os_str().as_encoded_bytes()).unwrap();
        let fd = loop {
            let fd = unsafe { libc::open(cpath.as_ptr(), flags) };
            if fd >= 0 {
                break fd;
            }
            let err = std::io::Error::last_os_error();
            if err.raw_os_error() != Some(libc::ENXIO) {
                panic!("unexpected open error: {err}");
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        };
        let mut file = unsafe { std::fs::File::from_raw_fd(fd) };
        let chunk = vec![b'f'; 64 * 1024];
        let mut written = 0usize;
        let mut saw_epipe = false;
        while written < total {
            match file.write(&chunk[..(total - written).min(chunk.len())]) {
                Ok(n) => written += n,
                // A nonblocking write into a full pipe returns EAGAIN — retry.
                Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(std::time::Duration::from_millis(5));
                }
                // The reader stopped at cap+1 bytes and closed its end: the
                // bounded read is doing its job, the rejection is proven.
                Err(err) if err.kind() == std::io::ErrorKind::BrokenPipe => {
                    saw_epipe = true;
                    break;
                }
                Err(err) => panic!("fifo write failed: {err}"),
            }
        }
        drop(file); // close → any remaining reader sees EOF
        // #689 review P2-2: the EPIPE is the OBSERVABLE proof that the read
        // side was bounded (closed its end at cap+1) rather than draining
        // the whole 5 MiB and rejecting on a length check afterwards. If the
        // take(cap+1) ever regresses to an unbounded read, the reader only
        // closes after EOF — the writer pushes all 5 MiB, never sees EPIPE,
        // and this assertion fails instead of the test quietly passing.
        assert!(
            saw_epipe,
            "writer drained the full {total} bytes without EPIPE — the reader \
             never closed its end at the cap, i.e. the read was NOT bounded"
        );
        assert!(
            written <= 4 * 1024 * 1024 + 128 * 1024,
            "reader closed too late: wrote {written} bytes before EPIPE — the \
             bounded read must stop at cap+1 (plus pipe-buffer slack)"
        );
    });

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "pipe.txt"}), &ctx(dir.path()))
        .await;
    writer.join().expect("writer thread panicked");

    assert!(output.is_error, "over-cap FIFO must be rejected");
    let text = result_text(&output);
    // Anchored on the ToolError::TooLarge prefix + the FIFO branch's own
    // wording (same P3-2 discipline as the json FIFO test).
    assert!(
        text.contains("content too large for in-memory processing"),
        "missing the ToolError::TooLarge prefix: {text}"
    );
    assert!(
        text.contains("exceeds the 4MB whole-file limit"),
        "missing size-limit error: {text}"
    );
    assert!(
        text.contains("growing/FIFO sources report no exact size"),
        "must not fake a precise size for a source whose len() is 0: {text}"
    );
    assert!(
        text.contains("sed -n '1,2000p' pipe.txt"),
        "missing chunked-read hint: {text}"
    );
}

// The FIFO-under-cap complement: the bounded reader must still read small
// FIFO payloads to EOF — the cap is a ceiling, not a reason to refuse the
// whole class of non-regular files.
#[cfg(unix)]
#[tokio::test]
async fn read_fifo_under_cap_reads_to_eof() {
    let dir = tempfile::tempdir().unwrap();
    let fifo = dir.path().join("small-pipe.txt");
    let cpath = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);

    let writer_fifo = fifo.clone();
    let writer = std::thread::spawn(move || {
        use std::io::Write;
        let flags = libc::O_WRONLY | libc::O_NONBLOCK;
        let cpath = std::ffi::CString::new(writer_fifo.as_os_str().as_encoded_bytes()).unwrap();
        let fd = loop {
            let fd = unsafe { libc::open(cpath.as_ptr(), flags) };
            if fd >= 0 {
                break fd;
            }
            let err = std::io::Error::last_os_error();
            if err.raw_os_error() != Some(libc::ENXIO) {
                panic!("unexpected open error: {err}");
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        };
        let mut file = unsafe { std::fs::File::from_raw_fd(fd) };
        file.write_all(b"alpha\nbeta\n").expect("fifo write failed");
        drop(file);
    });

    let output = ToolKind::Read
        .execute(&serde_json::json!({"path": "small-pipe.txt"}), &ctx(dir.path()))
        .await;
    writer.join().expect("writer thread panicked");

    assert!(!output.is_error, "under-cap FIFO must be readable");
    assert_eq!(result_text(&output), "alpha\nbeta");
}

// #689 review P2: the full recovery loop the capped-bash notice prescribes —
// redirect a >4 MiB output to a file, then walk it with the notice's own
// bash commands (`sed` head window, `tail | head` continuation windows).
// Every step must succeed against the tool semantics as shipped; this test
// is the executable proof that the remediation is walkable end-to-end (and
// doubles as the read-rejects-over-cap-with-offset/limit half of it).
#[tokio::test]
async fn capped_bash_recovery_flow_is_walkable() {
    let dir = tempfile::tempdir().unwrap();
    // Aim comfortably past the 4 MiB read cap: 20 bytes/line × 250k lines.
    let total_lines = 250_000usize;
    let content: String = (0..total_lines)
        .map(|i| format!("row-{i:07}-payload\n"))
        .collect();
    std::fs::write(dir.path().join("out.log"), &content).unwrap();
    assert!(
        content.len() > 4 * 1024 * 1024,
        "fixture must exceed the read cap"
    );

    // Step 1 — the read tool must REJECT the over-cap file even with an
    // explicit bounded offset/limit window (the old notice's dead end).
    let output = ToolKind::Read
        .execute(
            &serde_json::json!({"path": "out.log", "offset": 1, "limit": 50}),
            &ctx(dir.path()),
        )
        .await;
    assert!(output.is_error, "read must reject the over-cap whole file");
    assert!(
        result_text(&output).contains("too large"),
        "missing size-limit error"
    );

    // Step 2 — the notice's first command: the sed head window.
    let sed = ToolKind::Bash
        .execute(
            &serde_json::json!({"command": "sed -n '1,2000p' out.log"}),
            &ctx(dir.path()),
        )
        .await;
    assert!(!sed.is_error);
    let sed_text = result_text(&sed);
    assert!(sed_text.contains("row-0000000"), "sed window lost the head");
    assert!(!sed_text.contains("row-0020000"), "sed window too wide");

    // Step 3 — the continuation command: tail + head paging deeper in.
    let page = ToolKind::Bash
        .execute(
            &serde_json::json!({
                "command": "tail -n +2001 out.log | head -n 2000"
            }),
            &ctx(dir.path()),
        )
        .await;
    assert!(!page.is_error);
    let page_text = result_text(&page);
    assert!(
        page_text.contains("row-0002000") && !page_text.contains("row-0000000"),
        "continuation window is wrong: {page_text}"
    );
}
