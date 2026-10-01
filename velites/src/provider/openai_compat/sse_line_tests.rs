//! SSE line-cap tests for the OpenAI-compatible provider (#689 codex
//! round 3): one TCP chunk may legitimately deliver many complete lines
//! whose TOTAL exceeds the per-line cap (an HTTP framing detail, not
//! corruption), while a single line longer than the cap is corruption.
//! Split out of `mod.rs` for the file size budget (#689); the tests
//! exercise `SseLineBuffer` directly, so they stay a child module (the
//! `sandbox/tests.rs` sibling-file convention) instead of moving to
//! `velites/tests/`.

use super::aggregate::SseLineBuffer;

#[test]
fn sse_line_buffer_accepts_multi_line_chunk_over_total_cap() {
    // #689 codex round 3: one TCP chunk may legitimately deliver many
    // complete lines whose TOTAL exceeds the per-line cap — that is an
    // HTTP framing detail, not corruption. The same response must parse
    // identically however reqwest happened to chunk it; only a single
    // LINE longer than the cap is rejected.
    let mut buffer = SseLineBuffer::default();
    let line = format!("data: {}\n", "x".repeat(64 * 1024));
    let mut chunk = Vec::with_capacity(40 * line.len());
    for _ in 0..40 {
        chunk.extend_from_slice(line.as_bytes());
    }
    assert!(
        chunk.len() > 2 * 1024 * 1024,
        "fixture must exceed the cap: {}",
        chunk.len()
    );
    let lines = buffer
        .push(&chunk)
        .expect("legal multi-line chunk must parse");
    assert_eq!(lines.len(), 40);
    assert!(lines.iter().all(|l| l.starts_with("data: ")));
    assert!(buffer.finish().is_none());
}

#[test]
fn sse_line_buffer_rejects_overlong_complete_line() {
    // Mirror of the overlong-residual case: a newline-TERMINATED line
    // longer than the cap is still corruption — the cap bounds one
    // line, not the chunk.
    let mut buffer = SseLineBuffer::default();
    let mut junk = vec![b'x'; 2 * 1024 * 1024 + 1];
    junk.push(b'\n');
    let err = buffer
        .push(&junk)
        .expect_err("overlong complete line must be rejected");
    assert!(err.is_retryable(), "overlong line is transient: {err}");
    assert!(err.to_string().contains("SSE line exceeds"), "got: {err}");
    assert!(
        buffer.finish().is_none(),
        "buffer must be cleared on rejection"
    );
}
