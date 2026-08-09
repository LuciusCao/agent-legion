//! `bash` tool: run a command in the working directory with inherited env.
//!
//! The child is put in its own process group; on timeout OR cancellation the
//! whole group receives SIGTERM, then SIGKILL after a grace period (Pi
//! semantics, design §8). stdout+stderr volume is reported as `output_bytes`
//! (pre-truncation measurement). Output is truncated from the tail to 2000
//! lines or 50KB, whichever is hit first (pi-aligned, design §8); when
//! truncated, the full output is written to a temp file and the notice
//! points at it.

use std::time::Duration;

use serde_json::Value;
use tokio::io::AsyncReadExt;

use super::command_guard;
use super::truncate::{self, TruncatedBy};
use super::{ToolContext, ToolError, ToolOutput};

const DEFAULT_TIMEOUT_SECS: u64 = 120;
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
    let timeout_secs = args
        .get("timeout")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_TIMEOUT_SECS)
        .max(1);

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
    cmd.current_dir(&ctx.cwd)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        // Env is inherited by default; kill_on_drop is a safety net for
        // harness shutdown, the terminate path below handles timeout and
        // cancellation.
        .kill_on_drop(true);
    #[cfg(unix)]
    cmd.process_group(0);

    let mut child = cmd.spawn()?;
    let pid = child.id();

    let mut stdout_pipe = child.stdout.take().expect("stdout was piped");
    let mut stderr_pipe = child.stderr.take().expect("stderr was piped");
    let stdout_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        let result = stdout_pipe.read_to_end(&mut buf).await;
        result.map(|_| buf)
    });
    let stderr_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        let result = stderr_pipe.read_to_end(&mut buf).await;
        result.map(|_| buf)
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
    if timed_out || cancelled {
        terminate(&mut child, pid).await;
    }

    let stdout = stdout_task
        .await
        .map_err(|err| ToolError::Io(std::io::Error::other(err)))??;
    let stderr = stderr_task
        .await
        .map_err(|err| ToolError::Io(std::io::Error::other(err)))??;

    let output_bytes = (stdout.len() + stderr.len()) as u64;
    let stdout_text = String::from_utf8_lossy(&stdout);
    let stderr_text = String::from_utf8_lossy(&stderr);

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
    })
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
