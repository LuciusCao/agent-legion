//! SSE line-cap tests for the Anthropic provider (#637/#689): the 2 MiB
//! per-line bound in `push_lines_bounded` — overlong-residual rejection,
//! legal multi-line chunks whose TOTAL exceeds the cap, and overlong
//! newline-terminated lines. Split out of `anthropic.rs` when that file
//! passed the 800-line absolute limit (#689 codex round 3); the tests
//! assert on the private helper, so they stay a child module (the
//! `sandbox/tests.rs` sibling-file convention) instead of moving to
//! `velites/tests/`.

use super::*;

#[test]
fn sse_line_buffer_rejects_overlong_line() {
    // #637: an unterminated line longer than any legitimate SSE payload
    // is corruption — the line buffer must reject it instead of growing
    // without bound (a junk flood with no newline never reaches the
    // aggregate caps). Mirrors the openai_compat SseLineBuffer test.
    let mut buffer = Vec::new();
    let junk = vec![b'x'; 2 * 1024 * 1024 + 1];
    let err = push_lines_bounded(&mut buffer, &junk).expect_err("overlong line must be rejected");
    assert!(err.is_retryable(), "overlong line is transient: {err}");
    assert!(err.to_string().contains("SSE line exceeds"), "got: {err}");
    // The rejected buffer is cleared, not retained (defensive: a reused
    // buffer must not immediately re-trip on the stale junk).
    assert!(
        buffer.is_empty(),
        "buffer must be cleared on rejection, {} bytes retained",
        buffer.len()
    );

    // Newline-terminated lines drain on every push, so the same total
    // volume never trips the cap — only an UNTERMINATED line does.
    let mut buffer = Vec::new();
    for _ in 0..5 {
        let lines = push_lines_bounded(&mut buffer, b"data: x\n").unwrap();
        assert_eq!(lines, vec!["data: x".to_string()]);
    }
    assert!(buffer.is_empty());
}

#[test]
fn sse_lines_multi_line_chunk_over_total_cap_parses() {
    // #689 codex round 3: one chunk may legitimately deliver many
    // complete lines whose TOTAL exceeds MAX_SSE_LINE_BYTES — an HTTP
    // framing detail, not corruption. The same response must parse
    // identically however reqwest happened to chunk it; only a single
    // LINE longer than the cap is rejected. Mirrors the openai_compat
    // SseLineBuffer test.
    let mut buffer = Vec::new();
    let line = format!("data: {}\n", "x".repeat(64 * 1024));
    let mut chunk = Vec::with_capacity(40 * line.len());
    for _ in 0..40 {
        chunk.extend_from_slice(line.as_bytes());
    }
    assert!(
        chunk.len() > MAX_SSE_LINE_BYTES,
        "fixture must exceed the cap: {}",
        chunk.len()
    );
    let lines = push_lines_bounded(&mut buffer, &chunk).expect("legal multi-line chunk must parse");
    assert_eq!(lines.len(), 40);
    assert!(lines.iter().all(|l| l.starts_with("data: ")));
    assert!(buffer.is_empty());
}

#[test]
fn sse_lines_overlong_complete_line_is_rejected() {
    // Mirror of the overlong-residual case: a newline-TERMINATED line
    // longer than the cap is still corruption — the cap bounds one
    // line, not the chunk.
    let mut buffer = Vec::new();
    let mut junk = vec![b'x'; MAX_SSE_LINE_BYTES + 1];
    junk.push(b'\n');
    let err = push_lines_bounded(&mut buffer, &junk)
        .expect_err("overlong complete line must be rejected");
    assert!(err.is_retryable(), "overlong line is transient: {err}");
    assert!(err.to_string().contains("SSE line exceeds"), "got: {err}");
    assert!(
        buffer.is_empty(),
        "buffer must be cleared on rejection, {} bytes retained",
        buffer.len()
    );
}
