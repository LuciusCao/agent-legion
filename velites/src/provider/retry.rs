//! Internal retry wrapper (design §4 错误语义): transient model-call
//! failures (network errors, timeouts, 429, 5xx, interrupted streams) are
//! retried with exponential backoff INSIDE the harness, so a recovered call
//! never surfaces in the event stream — matching Pi, where a later
//! `stopReason=stop|toolUse` clears any transient error the Host recorded.
//! Deterministic failures (4xx other than 429) pass through untouched and
//! end the run as `stopReason=error` + `errorMessage` with exit 0.

use std::time::Duration;

use super::{CompletionRequest, Provider, ProviderError};
use crate::events::Message;

/// Retries `ProviderError::is_retryable()` failures with exponential backoff
/// (`base_delay * 2^attempt`), up to `max_retries` extra attempts.
pub struct RetryProvider<P> {
    inner: P,
    max_retries: u32,
    base_delay: Duration,
}

impl<P> RetryProvider<P> {
    pub fn new(inner: P, max_retries: u32, base_delay: Duration) -> Self {
        Self {
            inner,
            max_retries,
            base_delay,
        }
    }
}

impl<P: Provider> Provider for RetryProvider<P> {
    async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError> {
        let mut attempt: u32 = 0;
        loop {
            match self.inner.complete(req).await {
                Ok(message) => return Ok(message),
                Err(err) if err.is_retryable() && attempt < self.max_retries => {
                    let delay = self.base_delay * 2u32.pow(attempt);
                    attempt += 1;
                    eprintln!(
                        "velites: transient provider error (attempt {attempt}/{}), retrying in {}ms: {err}",
                        self.max_retries + 1,
                        delay.as_millis(),
                    );
                    tokio::time::sleep(delay).await;
                }
                Err(err) => return Err(err),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;
    use crate::provider::stub::StubProvider;

    struct FlakyProvider {
        calls: AtomicUsize,
        fail_times: usize,
    }

    impl Provider for FlakyProvider {
        async fn complete(&self, req: &CompletionRequest<'_>) -> Result<Message, ProviderError> {
            let call = self.calls.fetch_add(1, Ordering::SeqCst);
            if call < self.fail_times {
                return Err(ProviderError::Transient("boom".into()));
            }
            let mut message = Message::bare(crate::events::Role::Assistant, Vec::new());
            message.model = Some(req.model.to_string());
            message.stop_reason = Some(crate::events::StopReason::Stop);
            Ok(message)
        }
    }

    fn request<'a>(model: &'a str) -> CompletionRequest<'a> {
        CompletionRequest {
            model,
            system: "",
            messages: &[],
            tools: &[],
            thinking: None,
        }
    }

    #[tokio::test]
    async fn recovers_after_transient_failures() {
        let provider = RetryProvider::new(
            FlakyProvider {
                calls: AtomicUsize::new(0),
                fail_times: 2,
            },
            3,
            Duration::from_millis(1),
        );
        let message = provider.complete(&request("m")).await.unwrap();
        assert_eq!(message.stop_reason, Some(crate::events::StopReason::Stop));
    }

    #[tokio::test]
    async fn gives_up_after_max_retries() {
        let flaky = FlakyProvider {
            calls: AtomicUsize::new(0),
            fail_times: usize::MAX,
        };
        let provider = RetryProvider::new(flaky, 2, Duration::from_millis(1));
        let err = provider.complete(&request("m")).await.unwrap_err();
        assert!(err.to_string().contains("boom"));
        // 1 initial attempt + 2 retries.
        assert_eq!(provider.inner.calls.load(Ordering::SeqCst), 3);
    }

    #[tokio::test]
    async fn deterministic_errors_are_not_retried() {
        struct AlwaysDeterministic;
        impl Provider for AlwaysDeterministic {
            async fn complete(
                &self,
                _req: &CompletionRequest<'_>,
            ) -> Result<Message, ProviderError> {
                Err(ProviderError::Call("HTTP 404: model not found".into()))
            }
        }
        let provider = RetryProvider::new(AlwaysDeterministic, 5, Duration::from_millis(1));
        let err = provider.complete(&request("m")).await.unwrap_err();
        assert!(err.to_string().contains("404"));
    }

    #[tokio::test]
    async fn passes_through_stub_success() {
        let dir = tempfile::tempdir().unwrap();
        let fixture = dir.path().join("fixture.json");
        std::fs::write(
            &fixture,
            r#"{"responses":[{"content":[{"type":"text","text":"ok"}]}]}"#,
        )
        .unwrap();
        let stub = StubProvider::from_fixture(&fixture).unwrap();
        let provider = RetryProvider::new(stub, 3, Duration::from_millis(1));
        let message = provider.complete(&request("m")).await.unwrap();
        assert_eq!(message.provider.as_deref(), Some("stub"));
    }
}
