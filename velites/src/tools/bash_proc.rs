//! Child-process lifecycle of the `bash` tool, split out of `bash.rs` for
//! the file-size budget:
//!
//! - the environment allowlist the child inherits (#922 R-4);
//! - process-group termination (TERM → grace → KILL, design §8);
//! - the post-exit drain bound (#942): once the command has exited (or was
//!   terminated), leftovers in its process group are signalled and the pipe
//!   readers get a bounded grace window, so a background process holding
//!   the output pipes can no longer hang the tool call or outlive it;
//! - the bounded pipe reader (#637 capture cap, #469 first-byte offset).

use std::ffi::OsString;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::io::AsyncReadExt;
use tokio_util::sync::CancellationToken;

use super::elapsed_ms;

/// TERM → KILL grace on the timeout / cancel paths.
pub(super) const TERM_GRACE: Duration = Duration::from_secs(3);

/// #942: how long the pipe readers may keep draining after the command is
/// gone. Past it the process group is SIGKILLed and the readers stop with
/// whatever they collected.
pub(super) const DRAIN_GRACE: Duration = Duration::from_secs(2);

/// Variables the bash child inherits by exact name (#922 R-4). Everything
/// else in the harness environment — provider credentials referenced from
/// models.json, worker-injected secrets — stays out of the model-driven
/// shell. Aligned with the Host-side code sandbox allowlist
/// (`shared/code_sandbox.py::child_env`) plus a few inert shell basics.
const INHERITED_VARS: &[&str] = &[
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TZ",
    "VIRTUAL_ENV",
];

/// Variable-name prefixes the bash child inherits: locale (`LC_*`) and the
/// interpreter knobs (`PYTHONPATH`, `PYTHONUTF8`, ...).
const INHERITED_PREFIXES: &[&str] = &["LC_", "PYTHON"];

fn is_inherited(name: &str) -> bool {
    let by_prefix = INHERITED_PREFIXES.iter().any(|p| name.starts_with(p));
    by_prefix || INHERITED_VARS.contains(&name)
}

/// The allowlisted subset of the harness environment for the bash child;
/// the caller clears the inherited environment and sets exactly these.
pub(super) fn inherited_env() -> Vec<(OsString, OsString)> {
    std::env::vars_os()
        .filter(|(name, _)| name.to_str().is_some_and(is_inherited))
        .collect()
}

#[cfg(unix)]
fn kill_process_group(pid: u32, signal: libc::c_int) {
    // The child was spawned with process_group(0), so pgid == pid.
    unsafe {
        libc::killpg(pid as libc::pid_t, signal);
    }
}

/// TERM → grace → KILL the child's process group, then reap it.
pub(super) async fn terminate(child: &mut tokio::process::Child, pid: Option<u32>) {
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

/// #942: called once the command is gone (natural exit, or after
/// [`terminate`]). SIGTERM the process group right away — background
/// leftovers of a finished command must not outlive the tool call as
/// orphans — then, after [`DRAIN_GRACE`], SIGKILL it and cancel `stop` so
/// the pipe readers return what they collected (a process that left the
/// group but still holds the pipes would otherwise block them forever).
///
/// The caller aborts the returned task as soon as both readers finished,
/// so nothing is signalled after the drain completed. While any member of
/// the group is alive the kernel keeps the group id reserved, so the
/// signals cannot reach an unrelated process group.
pub(super) fn start_drain_watchdog(
    pid: Option<u32>,
    stop: CancellationToken,
) -> tokio::task::JoinHandle<()> {
    #[cfg(unix)]
    {
        if let Some(pid) = pid {
            kill_process_group(pid, libc::SIGTERM);
        }
    }
    tokio::spawn(async move {
        tokio::time::sleep(DRAIN_GRACE).await;
        #[cfg(unix)]
        {
            if let Some(pid) = pid {
                kill_process_group(pid, libc::SIGKILL);
            }
        }
        #[cfg(not(unix))]
        let _ = pid;
        stop.cancel();
    })
}

/// 一条输出 pipe 的采集结果（#637 带上限读取）。
pub(super) struct PipeCapture {
    /// 流头部，至多 `max_bytes` 字节；触顶后的字节只计数、不保留。
    pub(super) head: Vec<u8>,
    /// 完整流字节数（保留的头部 + 丢弃的尾部）——`output_bytes` 的统计
    /// 口径，触顶前后一致。
    pub(super) total_bytes: u64,
    /// 是否触顶（`total_bytes > max_bytes`，即确有字节被丢弃）。
    pub(super) hit_cap: bool,
    /// 首个非空 PRE-BOUNDARY 读的毫秒偏移（#469），语义与上限无关。
    pub(super) first_byte_ms: Option<u64>,
}

/// Read one output pipe to EOF, keeping at most `max_bytes` HEAD bytes in
/// memory (#637) and recording the elapsed offset of the first non-empty
/// PRE-BOUNDARY read (#469). `None` when the stream never produced a byte
/// before the `boundary` flag fired (bytes after it are still collected
/// into the head — output semantics are unchanged — but they cannot claim
/// firstByteMs).
///
/// #637: past the cap the bytes are counted into `total_bytes` but dropped —
/// the loop NEVER stops reading on its own, because a pipe that is not
/// drained to EOF backpressure-blocks the child's write side (a `cat
/// hugefile` would hang instead of finishing). The only early stop is the
/// `stop` token (#942 drain bound, see [`start_drain_watchdog`]): the reader
/// then returns what it collected so far. Error semantics are unchanged:
/// the first error aborts the read and propagates.
pub(super) async fn read_with_first_byte<R: tokio::io::AsyncRead + Unpin>(
    pipe: &mut R,
    started: Instant,
    boundary: Arc<AtomicBool>,
    max_bytes: usize,
    stop: CancellationToken,
) -> std::io::Result<PipeCapture> {
    let mut head = Vec::new();
    let mut total_bytes: u64 = 0;
    let mut first_byte_ms = None;
    let mut chunk = [0u8; 8192];
    loop {
        // Pipe reads are cancel-safe: a read dropped by the stop branch
        // consumed no bytes.
        let n = tokio::select! {
            read = pipe.read(&mut chunk) => read?,
            _ = stop.cancelled() => break,
        };
        if n == 0 {
            break;
        }
        // A byte that lands after the exit/timeout boundary fired is
        // output-only (the child is already gone; e.g. a TERM handler's
        // parting print) — it must not retroactively own the prelude.
        if first_byte_ms.is_none() && !boundary.load(Ordering::Acquire) {
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

#[cfg(test)]
mod tests {
    use tokio::io::AsyncWriteExt;

    use super::*;

    fn fresh_boundary() -> Arc<AtomicBool> {
        Arc::new(AtomicBool::new(false))
    }

    async fn read_all(data: &[u8], boundary: Arc<AtomicBool>, cap: usize) -> PipeCapture {
        let mut pipe: &[u8] = data;
        read_with_first_byte(
            &mut pipe,
            Instant::now(),
            boundary,
            cap,
            CancellationToken::new(),
        )
        .await
        .unwrap()
    }

    #[test]
    fn env_allowlist_keeps_basics_and_drops_everything_else() {
        for name in [
            "PATH",
            "HOME",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "PYTHONPATH",
            "VIRTUAL_ENV",
        ] {
            assert!(is_inherited(name), "{name} must be inherited");
        }
        for name in [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "LLM_GATEWAY_TOKEN",
            "AGENT_LEGION_DATABASE_URL",
            "AWS_SECRET_ACCESS_KEY",
            "VELITES_MODELS_PATH",
            "MY_PATH",
            "path",
        ] {
            assert!(!is_inherited(name), "{name} must not be inherited");
        }
    }

    #[tokio::test]
    async fn read_with_first_byte_keeps_head_and_counts_past_the_cap() {
        // #637: 100 字节的流、上限 30——头部保留 30 字节，完整计数仍是
        // 100（丢弃的字节只计数不保留）。
        let capture = read_all(&[b'a'; 100], fresh_boundary(), 30).await;
        assert_eq!(capture.head, vec![b'a'; 30]);
        assert_eq!(capture.total_bytes, 100);
        assert!(capture.hit_cap);
        assert!(capture.first_byte_ms.is_some());
    }

    #[tokio::test]
    async fn read_with_first_byte_exact_cap_is_not_capped() {
        // 恰好等于上限：一个字节都没有丢，不算触顶。
        let capture = read_all(&[b'b'; 30], fresh_boundary(), 30).await;
        assert_eq!(capture.head, vec![b'b'; 30]);
        assert_eq!(capture.total_bytes, 30);
        assert!(!capture.hit_cap);
    }

    #[tokio::test]
    async fn read_with_first_byte_under_cap_collects_everything() {
        let capture = read_all(b"hello", fresh_boundary(), 50).await;
        assert_eq!(capture.head, b"hello".to_vec());
        assert_eq!(capture.total_bytes, 5);
        assert!(!capture.hit_cap);
    }

    #[tokio::test]
    async fn read_with_first_byte_cap_across_chunk_boundary() {
        // 跨 chunk 触顶：最后一 chunk 只保留到上限的前缀，计数完整。
        let capture = read_all(&[b'c'; 8192 + 10], fresh_boundary(), 8192).await;
        assert_eq!(capture.head, vec![b'c'; 8192]);
        assert_eq!(capture.total_bytes, 8192 + 10);
        assert!(capture.hit_cap);
    }

    #[tokio::test]
    async fn read_with_first_byte_boundary_flag_clips_first_byte() {
        // #469 语义不因上限改变：boundary 已触发后到达的首字节不认领
        // firstByteMs（但仍被计数/保留）。
        let boundary = fresh_boundary();
        boundary.store(true, Ordering::Release);
        let capture = read_all(b"late", boundary, 50).await;
        assert_eq!(capture.head, b"late".to_vec());
        assert_eq!(capture.total_bytes, 4);
        assert!(capture.first_byte_ms.is_none());
    }

    #[tokio::test]
    async fn read_with_first_byte_stop_token_ends_a_pipe_that_never_closes() {
        // #942: the writer end stays open (a leftover process holding the
        // pipe); the stop token must end the read with the bytes so far.
        let (mut reader, mut writer) = tokio::io::duplex(64);
        writer.write_all(b"done\n").await.unwrap();
        let stop = CancellationToken::new();
        let reader_stop = stop.clone();
        let task = tokio::spawn(async move {
            let boundary = fresh_boundary();
            read_with_first_byte(&mut reader, Instant::now(), boundary, 50, reader_stop).await
        });
        tokio::time::sleep(Duration::from_millis(50)).await;
        stop.cancel();
        let capture = tokio::time::timeout(Duration::from_secs(5), task)
            .await
            .expect("stopped reader must return promptly")
            .unwrap()
            .unwrap();
        assert_eq!(capture.head, b"done\n".to_vec());
        drop(writer);
    }
}
