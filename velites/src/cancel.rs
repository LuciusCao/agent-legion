//! Graceful cancellation (design §5 优雅取消).
//!
//! One shared [`CancelToken`] per run: a SIGTERM handler flips it, and the
//! agent loop checks it at the defined checkpoints (turn boundary, after each
//! tool execution). The bash tool additionally watches it while a child is
//! running, so a cancel during a long command triggers the same
//! TERM → grace → KILL process-group cleanup as a timeout.
//!
//! Cancellation ends the run with `agent_end{reason: "cancelled"}` and exit
//! code 0: cancelling is a deliberate Host action (the worker owns the
//! process lifecycle), not a harness fault — non-zero exits are reserved for
//! harness failures (bad args, internal errors). SIGKILL remains the outer
//! backstop and is unchanged.

use tokio_util::sync::CancellationToken;

/// Shared cancellation flag, a thin wrapper over tokio-util's
/// [`CancellationToken`]. Cloning is cheap; all clones observe the same
/// flag. A default-constructed token is never cancelled (library/test use).
#[derive(Debug, Clone, Default)]
pub struct CancelToken {
    inner: CancellationToken,
}

impl CancelToken {
    pub fn new() -> Self {
        Self::default()
    }

    /// Arm the process-wide SIGTERM handler and return the token it trips.
    /// Non-unix platforms get a never-tripped token (velites targets
    /// unix workers; SIGTERM handling is unix-only).
    pub fn install_sigterm_handler() -> Self {
        let token = Self::new();
        #[cfg(unix)]
        {
            let armed = token.clone();
            tokio::spawn(async move {
                if let Ok(mut sigterm) =
                    tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                {
                    sigterm.recv().await;
                    armed.cancel();
                }
            });
        }
        token
    }

    pub fn cancel(&self) {
        self.inner.cancel();
    }

    pub fn is_cancelled(&self) -> bool {
        self.inner.is_cancelled()
    }

    /// Resolves once cancellation is requested; pends forever otherwise.
    pub async fn wait(&self) {
        self.inner.cancelled().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn default_token_is_never_cancelled() {
        let token = CancelToken::default();
        assert!(!token.is_cancelled());
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(50), token.wait())
                .await
                .is_err()
        );
    }

    /// Thin-wrapper check: cancel() must propagate to clones and wake wait().
    /// (Deeper cancellation semantics are tokio-util's own concern.)
    #[tokio::test]
    async fn cancel_wakes_waiters() {
        let token = CancelToken::new();
        let waiter = token.clone();
        let handle = tokio::spawn(async move {
            waiter.wait().await;
        });
        tokio::task::yield_now().await;
        token.cancel();
        tokio::time::timeout(std::time::Duration::from_secs(1), handle)
            .await
            .expect("waiter must wake promptly")
            .unwrap();
        assert!(token.is_cancelled());
    }

    /// install_sigterm_handler returns a live token and its armed handler
    /// trips that token when the process receives SIGTERM.
    #[cfg(unix)]
    #[tokio::test]
    async fn sigterm_trips_installed_token() {
        let token = CancelToken::install_sigterm_handler();
        assert!(!token.is_cancelled());
        // Give the spawned handler task a chance to register its listener.
        tokio::task::yield_now().await;
        // SAFETY: sending SIGTERM to our own process; tokio's signal
        // machinery installs a catch-all handler, so the process is not
        // terminated and the armed token is cancelled instead.
        unsafe {
            libc::kill(libc::getpid(), libc::SIGTERM);
        }
        tokio::time::timeout(std::time::Duration::from_secs(5), token.wait())
            .await
            .expect("SIGTERM must trip the installed token");
        assert!(token.is_cancelled());
    }
}
