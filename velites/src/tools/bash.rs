//! `bash` tool: run a command in the working directory with inherited env.
//!
//! The child is put in its own process group; on timeout OR cancellation the
//! whole group receives SIGTERM, then SIGKILL after a grace period (Pi
//! semantics, design §8). The model-supplied `timeout` is clamped to
//! [1s, 1h] (default 120s) so one call cannot outrun the run's wall-clock
//! budget by orders of magnitude. stdout+stderr volume is reported as
//! `output_bytes` (pre-truncation measurement). Output is truncated from the
//! tail to 2000 lines or 50KB, whichever is hit first (pi-aligned, design
//! §8); when truncated, the full output is written to a temp file and the
//! notice points at it.
//!
//! #469 phase instrumentation: the tool result carries `timing` with the
//! decomposition `totalMs = spawnMs + firstByteMs + restMs + reapMs` (see
//! `events::ToolTiming`). `firstByteMs` — spawn returned → first output
//! byte — covers the child's entire prelude (bash parsing, an internal
//! `<<EOF` heredoc write, interpreter startup, first side effect), which is
//! where the sampled #469 stall lives. The output pipes are therefore read
//! incrementally (first chunk observed, then read to end); collection
//! semantics (bytes, truncation, timeout) are unchanged.

use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::io::AsyncReadExt;

use super::command_guard;
use super::truncate::{self, TruncatedBy};
use super::{ToolContext, ToolError, ToolOutput};
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
    let started = Instant::now();
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
    // ordering per stream, and error semantics are unchanged.
    let output_started = Instant::now();
    let stdout_task =
        tokio::spawn(async move { read_with_first_byte(&mut stdout_pipe, output_started).await });
    let stderr_task =
        tokio::spawn(async move { read_with_first_byte(&mut stderr_pipe, output_started).await });

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
    // Phase 3 (#469): steady run — first byte → exit observed (the whole
    // wait on a no-output child). Measured from the prelude start so the
    // two phases partition the output window without overlap.
    let exit_offset_ms = elapsed_ms(output_started);
    let mut reap_ms = None;
    if timed_out || cancelled {
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

    let output_bytes = (stdout.0.len() + stderr.0.len()) as u64;
    let stdout_text = String::from_utf8_lossy(&stdout.0);
    let stderr_text = String::from_utf8_lossy(&stderr.0);

    let mut text = stdout_text.into_owned();
    if !stderr_text.is_empty() {
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str("[stderr]\n");
        text.push_str(&stderr_text);
    }

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
                start_line, truncation.total_lines, truncation.total_lines, limit_note, path_note,
            ));
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

    Ok(ToolOutput {
        content: vec![crate::events::ContentBlock::Text { text }],
        is_error,
        output_bytes,
        timing: Some(ToolTiming {
            total_ms: Some(elapsed_ms(started)),
            spawn_ms: Some(spawn_ms),
            // Phase 2 + 3 partition the output window: whichever stream
            // produced a byte first sets firstByteMs; restMs covers from the
            // prelude start to the exit, so firstByteMs + restMs = the whole
            // output window. A child with no output reports only restMs.
            first_byte_ms: stdout.1.or(stderr.1),
            rest_ms: Some(exit_offset_ms),
            reap_ms,
        }),
    })
}

/// Read one output pipe to EOF, recording the elapsed offset of the first
/// non-empty read (#469). `None` when the stream never produced a byte.
/// Bytes and error semantics are identical to `read_to_end` — the first
/// error aborts the read and propagates — only the first-chunk boundary is
/// additionally observed.
async fn read_with_first_byte<R: tokio::io::AsyncRead + Unpin>(
    pipe: &mut R,
    started: Instant,
) -> std::io::Result<(Vec<u8>, Option<u64>)> {
    let mut buf = Vec::new();
    let mut first_byte_ms = None;
    let mut chunk = [0u8; 8192];
    loop {
        let n = pipe.read(&mut chunk).await?;
        if n == 0 {
            break;
        }
        if first_byte_ms.is_none() {
            first_byte_ms = Some(elapsed_ms(started));
        }
        buf.extend_from_slice(&chunk[..n]);
    }
    Ok((buf, first_byte_ms))
}

/// Wall-clock milliseconds since `started` (monotonic, clamped at 0).
pub(super) fn elapsed_ms_pub(started: Instant) -> u64 {
    elapsed_ms(started)
}

fn elapsed_ms(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
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
}
