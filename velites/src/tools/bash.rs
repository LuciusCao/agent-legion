//! `bash` tool: run a command in the working directory with inherited env.
//!
//! The child is put in its own process group; on timeout OR cancellation the
//! whole group receives SIGTERM, then SIGKILL after a grace period (Pi
//! semantics, design §8). The model-supplied `timeout` is clamped to
//! [1s, 1h] (default 120s) so one call cannot outrun the run's wall-clock
//! budget by orders of magnitude. stdout+stderr volume is reported as
//! `output_bytes` (full stream measurement — kept head PLUS dropped tail).
//! Output is truncated from the tail to 2000 lines or 50KB, whichever is
//! hit first (pi-aligned, design §8); when truncated, the full output is
//! written to a temp file and the notice points at it.
//!
//! #637 capture cap: each pipe's in-memory buffer is bounded at
//! [`truncate::MAX_CAPTURE_BYTES`] (4 MiB, per stream). Past the cap the
//! head is kept, every further byte is COUNTED but dropped, and the reader
//! keeps draining to EOF (stopping the reads would backpressure-block the
//! child's writes) — a runaway `cat hugefile` can no longer grow the
//! buffer without bound. `output_bytes` still reports the FULL volume
//! (kept + dropped), and the capped run's notice says the tail was dropped
//! and NO full-output file exists (there is nothing complete to write),
//! pointing the model at file redirection + chunked reads instead.
//!
//! #469 phase instrumentation: the tool result carries `timing` with the
//! phase decomposition `totalMs ≈ spawnMs + firstByteMs + restMs + reapMs`
//! (see `events::ToolTiming` for the residual and the signature table;
//! `totalMs` itself is filled by the `ToolKind::execute` dispatch
//! boundary, this module owns the subprocess phases). The output pipes
//! are read incrementally (first chunk observed, then read to end);
//! collection semantics (bytes, truncation, timeout) are unchanged.
//! firstByteMs only counts bytes observed BEFORE the exit/timeout
//! boundary — the reader tasks keep draining the pipes after the child
//! is gone, and a TERM handler printing late must not retroactively own
//! the prelude.
//!
//! Observation caveat: `firstByteMs` measures when the HARNESS read the
//! first byte, not when the child wrote it — and the two #469 stall
//! shapes split along exactly that seam: a WRITE-side stall (child stuck
//! before producing output — bash parsing, an internal `<<EOF` heredoc
//! write; the spindump-confirmed main shape never wrote to the
//! velites-held pipes at all) surfaces as firstByteMs ABSENT (the whole
//! window lands in restMs, then the timeout kill); a READ-side stall
//! (child wrote, harness read parked in a kernel pipe lock — #469
//! spindump: `lck_mtx_sleep`) surfaces as an ELEVATED firstByteMs. Under
//! normal load the agent loop's sequential tool awaits keep the reader
//! tasks pumping while the child runs, bounding benign drift; if tool
//! dispatch ever becomes concurrent, re-evaluate before trusting the
//! attribution. A silent command (`sleep 60; echo done`) mimics the
//! write-side signature — disambiguate via tool_execution_start.args.
//!
//! Reading the phases (the #469 signature table):
//!
//! 1. firstByteMs absent + restMs ≈ requestedTimeoutMs + reapMs present
//!    → write-side stall (child stuck in its prelude, killed at the
//!    ceiling) — the #469 main shape; cross-check args for heredoc;
//! 2. firstByteMs elevated + restMs normal → read-side stall (harness
//!    read parked in the pipe lock after the child wrote);
//! 3. spawnMs elevated → process-creation queuing;
//! 4. firstByteMs normal + restMs elevated → steady-run / output stream
//!    (long script, slow output, or a hung command).

use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::io::AsyncReadExt;

use super::command_guard;
use super::truncate::{self, TruncatedBy};
use super::{elapsed_ms, ToolContext, ToolError, ToolOutput};
use crate::events::ToolTiming;

const DEFAULT_TIMEOUT_SECS: u64 = 120;
/// Hard ceiling on one bash call's timeout. The model controls the
/// `timeout` argument; without a cap, `timeout=10^9` would let a single
/// tool call run for decades, far past the run's wall-clock budget
/// (`--timeout-seconds`). Not the min with the loop's remaining budget on
/// purpose: the tool layer does not see the agent loop's deadline, and one
/// hour already dwarfs any sane command lifetime.
const MAX_TIMEOUT_SECS: u64 = 3600;
const TERM_GRACE: Duration = Duration::from_secs(3);

pub async fn run(args: &Value, ctx: &ToolContext) -> ToolOutput {
    match run_inner(args, ctx).await {
        Ok(output) => output,
        Err(err) => ToolOutput::error(err.to_string()),
    }
}

#[cfg(unix)]
fn kill_process_group(pid: u32, signal: libc::c_int) {
    // The child was spawned with process_group(0), so pgid == pid.
    unsafe {
        libc::killpg(pid as libc::pid_t, signal);
    }
}

/// TERM → grace → KILL the child's process group, then reap it.
async fn terminate(child: &mut tokio::process::Child, pid: Option<u32>) {
    #[cfg(unix)]
    {
        if let Some(pid) = pid {
            kill_process_group(pid, libc::SIGTERM);
        }
        if tokio::time::timeout(TERM_GRACE, child.wait())
            .await
            .is_err()
        {
            if let Some(pid) = pid {
                kill_process_group(pid, libc::SIGKILL);
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = child.start_kill();
    }
    let _ = child.wait().await;
}

async fn run_inner(args: &Value, ctx: &ToolContext) -> Result<ToolOutput, ToolError> {
    let command = args
        .get("command")
        .and_then(Value::as_str)
        .ok_or_else(|| ToolError::InvalidArgs("missing string field `command`".into()))?;
    let timeout_secs = requested_timeout_secs(args);

    // Footgun guard: reject full-disk scan commands (`find /` …) before
    // spawn; one scan per parallel job floods host fs indexing. Applies with
    // or without the OS sandbox.
    command_guard::check(command)?;

    let mut cmd = {
        let argv = vec!["bash".to_string(), "-c".to_string(), command.to_string()];
        // OS-level filesystem sandbox (design §5, M4.5): the whole child
        // tree inherits the seatbelt/bwrap confinement. `None` = --no-sandbox.
        let (program, wrapped_argv) = match &ctx.sandbox {
            Some(sandbox) => sandbox.wrap(&argv),
            None => (argv[0].clone(), argv[1..].to_vec()),
        };
        let mut cmd = tokio::process::Command::new(program);
        cmd.args(wrapped_argv);
        cmd
    };
    // #469: the child's stdin stays inherited (the command travels as
    // `bash -c <command>` argv; nothing is written by the harness).
    cmd.current_dir(&ctx.cwd)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        // Env is inherited by default; kill_on_drop is a safety net for
        // harness shutdown, the terminate path below handles timeout and
        // cancellation.
        .kill_on_drop(true);
    #[cfg(unix)]
    cmd.process_group(0);

    // Phase 1 (#469): process creation — spawn start → child pid returned.
    let spawn_started = Instant::now();
    let mut child = cmd.spawn()?;
    let spawn_ms = elapsed_ms(spawn_started);
    let pid = child.id();

    let mut stdout_pipe = child.stdout.take().expect("stdout was piped");
    let mut stderr_pipe = child.stderr.take().expect("stderr was piped");
    // Phase 2 (#469): first output byte — the child's prelude (bash parsing,
    // an internal `<<EOF` heredoc write, interpreter startup) all happens
    // before the first byte lands in a pipe. Each reader records its own
    // first-byte offset; whichever fires first is the phase measurement.
    // Reading is otherwise identical to the previous read_to_end: bytes,
    // ordering per stream, and error semantics are unchanged. The boundary
    // flag is shared with the readers and fires only on the kill paths:
    // bytes that land after it are still collected for output purposes but
    // cannot claim firstByteMs (a TERM handler printing during the kill
    // does not retroactively own the prelude).
    let output_started = Instant::now();
    let boundary_fired = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    // #637: 每条 pipe 各自带上限读取（上限按流计，stdout/stderr 互不
    // 占用对方的额度）。
    let capture_cap = usize::try_from(truncate::MAX_CAPTURE_BYTES).unwrap_or(usize::MAX);
    let stdout_task = tokio::spawn({
        let boundary = boundary_fired.clone();
        async move {
            read_with_first_byte(&mut stdout_pipe, output_started, boundary, capture_cap).await
        }
    });
    let stderr_task = tokio::spawn({
        let boundary = boundary_fired.clone();
        async move {
            read_with_first_byte(&mut stderr_pipe, output_started, boundary, capture_cap).await
        }
    });

    let timeout = Duration::from_secs(timeout_secs);
    let mut timed_out = false;
    let mut cancelled = false;
    // `Child::wait` is cancel-safe, so racing it against the timeout and the
    // cancellation token loses nothing on the dropped branch.
    let status = tokio::select! {
        status = child.wait() => Some(status?),
        _ = tokio::time::sleep(timeout) => {
            timed_out = true;
            None
        }
        _ = ctx.cancel.wait() => {
            cancelled = true;
            None
        }
    };
    // Phase 3 (#469): steady run — first output byte → exit observed. On a
    // child with no pre-boundary output there is no first byte, so restMs
    // covers the whole output window instead. On the timeout/cancel path
    // restMs ends where the timeout fired; killing and reaping the group is
    // accounted separately as reapMs. The boundary flag fires ONLY on the
    // kill paths: a natural exit means every collected byte was produced
    // while the child ran (the pipes drain after wait() returns, so a fast
    // `echo` child's output would otherwise be mis-dropped); on a kill, the
    // flag tells the readers that later bytes (e.g. a TERM handler's
    // parting print) are output-only and cannot claim firstByteMs.
    let exit_offset_ms = elapsed_ms(output_started);
    let mut reap_ms = None;
    if timed_out || cancelled {
        boundary_fired.store(true, std::sync::atomic::Ordering::Release);
        // Phase 4 (#469): termination — TERM → grace → KILL → reaped.
        let reap_started = Instant::now();
        terminate(&mut child, pid).await;
        reap_ms = Some(elapsed_ms(reap_started));
    }

    let stdout = stdout_task
        .await
        .map_err(|err| ToolError::Io(std::io::Error::other(err)))??;
    let stderr = stderr_task
        .await
        .map_err(|err| ToolError::Io(std::io::Error::other(err)))??;

    // #637: output_bytes 统计口径不变——完整 stdout+stderr 字节数
    // （保留的头部 + 触顶后丢弃的尾部）。
    let output_bytes = stdout.total_bytes + stderr.total_bytes;
    let stdout_text = String::from_utf8_lossy(&stdout.head);
    let stderr_text = String::from_utf8_lossy(&stderr.head);

    let mut text = stdout_text.into_owned();
    if !stderr_text.is_empty() {
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str("[stderr]\n");
        text.push_str(&stderr_text);
    }

    let capture_capped = stdout.hit_cap || stderr.hit_cap;
    if capture_capped {
        // #637 触顶分支：保留的是流头部、尾部已被丢弃——完整输出在内存
        // 中已不存在，绝不能走 write_full_output（那会假装有完整输出可
        // 指认）。展示层保留头部的前 2000 行 / 50KB，通知说清「尾部已
        // 丢弃、无完整输出文件」，并指路：重定向到文件再用 read 工具
        // 分段读取。与 tail 截断方向相反（那边错误/结果在末尾、保尾；
        // 触顶后尾部已丢，只能保头）。
        let truncation = truncate::truncate_head(&text);
        let notice = format!(
            "[Output capture stopped after {} at the {} per-stream cap: the head above is kept, the tail was dropped. No full-output file was saved — the dropped tail no longer exists. Rerun with output redirected to a file (e.g. `cmd > out.log 2>&1`) and read it in chunks with the read tool's offset/limit.]",
            truncate::format_size(usize::try_from(output_bytes).unwrap_or(usize::MAX)),
            truncate::MAX_CAPTURE_BYTES_DISPLAY,
        );
        if truncation.content.is_empty() {
            // 首行就超过展示上限（如单个超长行）：无内容可展示，通知
            // 独立成文，不加前导空行——且不说「head above is kept」，
            // 上面没有任何内容。
            text = format!(
                "[Output capture stopped after {} at the {} per-stream cap (the first line alone exceeds the {} display limit, so no content is shown; the tail was dropped). No full-output file was saved — the dropped tail no longer exists. Rerun with output redirected to a file (e.g. `cmd > out.log 2>&1`) and read it in chunks with the read tool's offset/limit.]",
                truncate::format_size(usize::try_from(output_bytes).unwrap_or(usize::MAX)),
                truncate::MAX_CAPTURE_BYTES_DISPLAY,
                truncate::MAX_BYTES_DISPLAY,
            );
        } else {
            text = truncation.content;
            text.push_str("\n\n");
            text.push_str(&notice);
        }
    } else {
        // Tail truncation keeps the end of the output (errors/results live
        // there); the full output goes to a temp file the notice points at.
        let truncation = truncate::truncate_tail(&text);
        if truncation.truncated {
            // Size of the original last line (a trailing newline is not a line).
            let trimmed = text.strip_suffix('\n').unwrap_or(&text);
            let last_line_size = trimmed.rsplit('\n').next().unwrap_or("").len();
            let full_output = write_full_output(&text);
            let path_note = match &full_output {
                Some(path) => format!(" Full output: {}", path.display()),
                None => String::new(),
            };
            text = truncation.content;
            if truncation.last_line_partial {
                text.push_str(&format!(
                    "\n\n[Showing last {} of line {} (line is {}).{}]",
                    truncate::format_size(truncation.output_bytes),
                    truncation.total_lines,
                    truncate::format_size(last_line_size),
                    path_note,
                ));
            } else {
                let start_line = truncation.total_lines - truncation.output_lines + 1;
                let limit_note = match truncation.truncated_by {
                    Some(TruncatedBy::Bytes) => {
                        format!(" ({} limit)", truncate::MAX_BYTES_DISPLAY)
                    }
                    _ => String::new(),
                };
                text.push_str(&format!(
                    "\n\n[Showing lines {}-{} of {}{}.{}]",
                    start_line,
                    truncation.total_lines,
                    truncation.total_lines,
                    limit_note,
                    path_note,
                ));
            }
        }
    }

    let mut is_error = false;
    if timed_out {
        is_error = true;
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str(&format!(
            "Command timed out after {timeout_secs}s (process group terminated)."
        ));
    } else if cancelled {
        is_error = true;
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str("Command cancelled (process group terminated).");
    } else if let Some(status) = status {
        if !status.success() {
            is_error = true;
            if !text.is_empty() {
                text.push('\n');
            }
            text.push_str(&format!("Exit code: {}", exit_code_display(status)));
        }
    }

    // #469 phase combination: firstByteMs is the EARLIER of the two streams'
    // first-byte offsets (both readers share the same output_started, so the
    // offsets are directly comparable — a stdout-first `.or()` would mis-bucket
    // a stderr-first child's steady-run stall into the prelude). The readers
    // already dropped post-boundary bytes (see read_with_first_byte), so a
    // first byte that arrived only during termination belongs to the kill
    // path and never reaches here — restMs cannot collapse to 0 and the
    // phases never double-count the termination window. restMs then spans
    // first byte → exit, so the phases partition the output window without
    // overlap and total ≈ their sum (the residual is harness-side work
    // outside the phases — see events::ToolTiming); a child with no
    // pre-boundary output reports only restMs (the whole output window).
    // totalMs is NOT set here — the ToolKind::execute dispatch boundary owns
    // it, keeping one source for the decomposition base.
    let first_byte_ms = match (stdout.first_byte_ms, stderr.first_byte_ms) {
        (Some(a), Some(b)) => Some(a.min(b)),
        (a, b) => a.or(b),
    };
    let rest_ms = Some(match first_byte_ms {
        Some(first_byte) => exit_offset_ms.saturating_sub(first_byte),
        None => exit_offset_ms,
    });

    Ok(ToolOutput {
        content: vec![crate::events::ContentBlock::Text { text }],
        is_error,
        output_bytes,
        timing: Some(ToolTiming {
            total_ms: None,
            spawn_ms: Some(spawn_ms),
            first_byte_ms,
            rest_ms,
            reap_ms,
            // The enforced ceiling (clamped model-supplied `timeout`), so
            // the analysis side can join "actual duration vs requested
            // ceiling" — #469: models raise `timeout` after consecutive
            // failures, turning the 120s default into long self-inflicted
            // stalls.
            requested_timeout_ms: Some(timeout_secs.saturating_mul(1000)),
        }),
    })
}

/// 一条输出 pipe 的采集结果（#637 带上限读取）。
struct PipeCapture {
    /// 流头部，至多 `max_bytes` 字节；触顶后的字节只计数、不保留。
    head: Vec<u8>,
    /// 完整流字节数（保留的头部 + 丢弃的尾部）——`output_bytes` 的统计
    /// 口径，触顶前后一致。
    total_bytes: u64,
    /// 是否触顶（`total_bytes > max_bytes`，即确有字节被丢弃）。
    hit_cap: bool,
    /// 首个非空 PRE-BOUNDARY 读的毫秒偏移（#469），语义与上限无关。
    first_byte_ms: Option<u64>,
}

/// Read one output pipe to EOF, keeping at most `max_bytes` HEAD bytes in
/// memory (#637) and recording the elapsed offset of the first non-empty
/// PRE-BOUNDARY read (#469). `None` when the stream never produced a byte
/// before the `boundary` flag fired (bytes after it are still collected
/// into the head — output semantics are unchanged — but they cannot claim
/// firstByteMs).
///
/// #637: past the cap the bytes are counted into `total_bytes` but dropped —
/// the loop NEVER stops reading, because a pipe that is not drained to EOF
/// backpressure-blocks the child's write side (a `cat hugefile` would hang
/// instead of finishing). Error semantics are unchanged: the first error
/// aborts the read and propagates.
async fn read_with_first_byte<R: tokio::io::AsyncRead + Unpin>(
    pipe: &mut R,
    started: Instant,
    boundary: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_bytes: usize,
) -> std::io::Result<PipeCapture> {
    let mut head = Vec::new();
    let mut total_bytes: u64 = 0;
    let mut first_byte_ms = None;
    let mut chunk = [0u8; 8192];
    loop {
        let n = pipe.read(&mut chunk).await?;
        if n == 0 {
            break;
        }
        // A byte that lands after the exit/timeout boundary fired is
        // output-only (the child is already gone; e.g. a TERM handler's
        // parting print) — it must not retroactively own the prelude.
        if first_byte_ms.is_none() && !boundary.load(std::sync::atomic::Ordering::Acquire) {
            first_byte_ms = Some(elapsed_ms(started));
        }
        // #637: 完整计数；头部保留到上限为止，之后的字节丢弃。读取
        // 循环本身不受上限影响（见函数文档）。
        total_bytes += n as u64;
        if head.len() < max_bytes {
            let keep = (max_bytes - head.len()).min(n);
            head.extend_from_slice(&chunk[..keep]);
        }
    }
    Ok(PipeCapture {
        hit_cap: total_bytes > max_bytes as u64,
        head,
        total_bytes,
        first_byte_ms,
    })
}

/// The model-supplied `timeout` argument, clamped into
/// [1, MAX_TIMEOUT_SECS] (default [`DEFAULT_TIMEOUT_SECS`]).
fn requested_timeout_secs(args: &Value) -> u64 {
    args.get("timeout")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_TIMEOUT_SECS)
        .clamp(1, MAX_TIMEOUT_SECS)
}

fn exit_code_display(status: std::process::ExitStatus) -> String {
    match status.code() {
        Some(code) => code.to_string(),
        None => "terminated by signal".to_string(),
    }
}

/// Write the full (untruncated) output to a `velites-bash-*` file in the
/// system temp dir; `None` when the write fails (notice then omits the
/// path, same content either way).
fn write_full_output(content: &str) -> Option<std::path::PathBuf> {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let path =
        std::env::temp_dir().join(format!("velites-bash-{}-{nanos}.log", std::process::id()));
    std::fs::write(&path, content).ok().map(|_| path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requested_timeout_is_clamped() {
        assert_eq!(requested_timeout_secs(&serde_json::json!({})), 120);
        assert_eq!(
            requested_timeout_secs(&serde_json::json!({"timeout": 30})),
            30
        );
        assert_eq!(
            requested_timeout_secs(&serde_json::json!({"timeout": 0})),
            1
        );
        // A model-supplied 10^9 must not let one call outrun the run's
        // wall-clock budget by orders of magnitude.
        assert_eq!(
            requested_timeout_secs(&serde_json::json!({"timeout": 1_000_000_000u64})),
            MAX_TIMEOUT_SECS
        );
        assert_eq!(
            requested_timeout_secs(&serde_json::json!({"timeout": u64::MAX})),
            MAX_TIMEOUT_SECS
        );
    }

    fn fresh_boundary() -> std::sync::Arc<std::sync::atomic::AtomicBool> {
        std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false))
    }

    #[tokio::test]
    async fn read_with_first_byte_keeps_head_and_counts_past_the_cap() {
        // #637: 100 字节的流、上限 30——头部保留 30 字节，完整计数仍是
        // 100（丢弃的字节只计数不保留）。
        let data = vec![b'a'; 100];
        let mut pipe: &[u8] = &data;
        let capture = read_with_first_byte(&mut pipe, Instant::now(), fresh_boundary(), 30)
            .await
            .unwrap();
        assert_eq!(capture.head, vec![b'a'; 30]);
        assert_eq!(capture.total_bytes, 100);
        assert!(capture.hit_cap);
        assert!(capture.first_byte_ms.is_some());
    }

    #[tokio::test]
    async fn read_with_first_byte_exact_cap_is_not_capped() {
        // 恰好等于上限：一个字节都没有丢，不算触顶。
        let data = vec![b'b'; 30];
        let mut pipe: &[u8] = &data;
        let capture = read_with_first_byte(&mut pipe, Instant::now(), fresh_boundary(), 30)
            .await
            .unwrap();
        assert_eq!(capture.head, data);
        assert_eq!(capture.total_bytes, 30);
        assert!(!capture.hit_cap);
    }

    #[tokio::test]
    async fn read_with_first_byte_under_cap_collects_everything() {
        let data = b"hello".to_vec();
        let mut pipe: &[u8] = &data;
        let capture = read_with_first_byte(&mut pipe, Instant::now(), fresh_boundary(), 50)
            .await
            .unwrap();
        assert_eq!(capture.head, data);
        assert_eq!(capture.total_bytes, 5);
        assert!(!capture.hit_cap);
    }

    #[tokio::test]
    async fn read_with_first_byte_cap_across_chunk_boundary() {
        // 跨 chunk 触顶：最后一 chunk 只保留到上限的前缀，计数完整。
        let data = vec![b'c'; 8192 + 10];
        let mut pipe: &[u8] = &data;
        let capture = read_with_first_byte(&mut pipe, Instant::now(), fresh_boundary(), 8192)
            .await
            .unwrap();
        assert_eq!(capture.head, vec![b'c'; 8192]);
        assert_eq!(capture.total_bytes, 8192 + 10);
        assert!(capture.hit_cap);
    }

    #[tokio::test]
    async fn read_with_first_byte_boundary_flag_clips_first_byte() {
        // #469 语义不因上限改变：boundary 已触发后到达的首字节不认领
        // firstByteMs（但仍被计数/保留）。
        let data = b"late".to_vec();
        let mut pipe: &[u8] = &data;
        let boundary = fresh_boundary();
        boundary.store(true, std::sync::atomic::Ordering::Release);
        let capture = read_with_first_byte(&mut pipe, Instant::now(), boundary, 50)
            .await
            .unwrap();
        assert_eq!(capture.head, data);
        assert_eq!(capture.total_bytes, 4);
        assert!(capture.first_byte_ms.is_none());
    }
}
